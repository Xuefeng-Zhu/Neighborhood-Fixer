"""Shared deterministic authorization and state machine; models never authorize writes."""

from datetime import datetime, timezone
import asyncio
import hashlib
import json
import math
from pathlib import Path
import secrets
import time
import uuid

from .config import Settings
from .store import create_store
from .models import Observation, Analysis, Approval, SubmissionAttempt
from . import fixtures

CATEGORY_TITLES = {
    "damaged_sidewalk": "Damaged sidewalk or curb ramp",
    "pothole": "Pothole",
    "walkway_obstruction": "Walkway obstruction",
}
RESERVED = {"IN_FLIGHT", "RECEIPT_CONFIRMED", "OUTCOME_UNKNOWN"}


class DomainError(Exception):
    def __init__(self, code, message, status=400, retryable=False):
        self.code, self.message, self.status, self.retryable = (
            code,
            message,
            status,
            retryable,
        )
        super().__init__(message)


def ident():
    return uuid.uuid4().hex


def iso(stamp=None):
    return (
        datetime.fromtimestamp(
            stamp if stamp is not None else time.time(), timezone.utc
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


def epoch(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def digest(payload):
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def distance(a, b):
    lat1, lat2 = math.radians(a["latitude"]), math.radians(b["latitude"])
    dlat, dlon = lat2 - lat1, math.radians(b["longitude"] - a["longitude"])
    return (
        6371000
        * 2
        * math.asin(
            min(
                1,
                math.sqrt(
                    math.sin(dlat / 2) ** 2
                    + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
                ),
            )
        )
    )


def cells(lat, lon):
    return (math.floor(lat / 0.001), math.floor(lon / 0.001))


class Domain:
    def __init__(self, settings=None, store=None):
        self.settings = settings or Settings()
        self.store = store or create_store(self.settings)
        self.evidence_dir = self.settings.data_dir / "evidence"
        if self.settings.mode == "local":
            self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def now(self, tx):
        ws = tx.get("workspace", tx.workspace_id) or {}
        return time.time() + ws.get("clock_offset", 0)

    def require(self, tx, kind, record_id):
        record = tx.get(kind, record_id)
        if not record:
            raise DomainError(
                "NOT_FOUND", "This record is unavailable in your workspace.", 404
            )
        return record

    def owner(self, item, principal):
        if item["owner_id"] != principal["user"]["id"]:
            raise DomainError(
                "FORBIDDEN",
                "Only the reporting resident can approve or change this report.",
                403,
            )

    def event(
        self,
        tx,
        incident_id,
        event_type,
        message,
        operation_id=None,
        tool=None,
        result=None,
    ):
        event_id = f"{int(self.now(tx) * 1000000):020d}_{ident()}"
        item = {
            "id": event_id,
            "incident_id": incident_id,
            "type": event_type,
            "message": message,
            "operation_id": operation_id or ident(),
            "created_at": iso(self.now(tx)),
        }
        if tool:
            item.update(
                tool=tool,
                result=result or message,
                provenance="Simulated AI fixture"
                if self.settings.mode == "local" and tool.startswith("agent.")
                else "Executed application tool",
            )
        tx.put("event", event_id, item)
        tx.put("event:" + incident_id, event_id, item)
        head = tx.get("event_head", incident_id) or {"event_ids": []}
        head["event_ids"] = (head["event_ids"] + [event_id])[-100:]
        tx.put("event_head", incident_id, head)
        return item

    def notify(self, tx, incident_id, message):
        for sub in tx.list("subscription:" + incident_id):
            if sub["incident_id"] == incident_id and sub["following"]:
                notification_id = ident()
                item = {
                    "id": notification_id,
                    "incident_id": incident_id,
                    "user_id": sub["user_id"],
                    "message": message,
                    "created_at": iso(self.now(tx)),
                    "read": False,
                }
                tx.put("notification", notification_id, item)
                tx.put("notification:" + sub["user_id"], notification_id, item)

    def subscribe(self, tx, incident_id, user_id, following=True):
        sub_id = incident_id + "_" + user_id
        existing = tx.get("subscription", sub_id)
        if following and not (existing and existing["following"]):
            count = sum(
                1 for sub in tx.list("subscription:" + incident_id) if sub["following"]
            )
            if count >= 20:
                raise DomainError(
                    "DEMO_FOLLOWER_LIMIT",
                    "This small demo supports up to 20 followers per incident.",
                    409,
                )
        item = {
            "id": sub_id,
            "incident_id": incident_id,
            "user_id": user_id,
            "following": following,
        }
        tx.put("subscription", sub_id, item)
        tx.put("subscription:" + incident_id, sub_id, item)
        tx.put("user_subscription:" + user_id, incident_id, dict(item, id=incident_id))

    def member(self, tx, incident, principal):
        user_id = principal["user"]["id"]
        return incident["owner_id"] == user_id or bool(
            tx.get("membership", incident["id"] + "_" + user_id)
        )

    def accessible(self, tx, incident, principal):
        if not incident["shared_public"] and not self.member(tx, incident, principal):
            raise DomainError("NOT_FOUND", "This case is not shared with you.", 404)

    def session(self, resident, workspace_id=None, existing=None):
        if not self.settings.local_controls:
            raise DomainError(
                "DEMO_DISABLED",
                "Local identity switching is disabled in this environment.",
                403,
            )
        if workspace_id and (not existing or existing["workspace_id"] != workspace_id):
            raise DomainError(
                "WORKSPACE_FORBIDDEN",
                "A workspace can only be reused through its existing authenticated session.",
                403,
            )
        workspace_id = workspace_id or (existing or {}).get("workspace_id") or ident()
        with self.store.atomic(workspace_id) as tx:
            if not tx.get("workspace", workspace_id):
                tx.put(
                    "workspace",
                    workspace_id,
                    {"created_at": iso(), "clock_offset": 0, "lost_receipt": False},
                )
                self.seed(tx)
            user = {
                "id": resident + "_" + workspace_id[:12],
                "resident": resident,
                "name": "Alex Morgan" if resident == "alex" else "Sam Rivera",
            }
            tx.put("user", user["id"], user)
        return {"user": user, "workspace_id": workspace_id, "mode": self.settings.mode}

    def seed(self, tx):
        case_id = ident()
        now = iso(self.now(tx))
        item = {
            "id": case_id,
            "owner_id": "fixture",
            "title": "A separate pothole near the crossing",
            "description": "Illustrative seeded issue: a pothole in the road near the crossing. This is a distinct problem from the curb ramp.",
            "category": "pothole",
            "latitude": 47.6154,
            "longitude": -122.3354,
            "location_label": "Cedar Street at 4th Avenue · fictional Demo Borough",
            "agency_status": "NOT_SUBMITTED",
            "resolution_status": "UNVERIFIED",
            "submission_status": "PREPARED",
            "observation_count": 1,
            "observation_ids": [],
            "shared_public": True,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "seeded": True,
            "cell": list(cells(47.6154, -122.3354)),
            "next_action": "A nearby but distinct illustrative issue.",
        }
        tx.put("incident", case_id, item)
        tx.put(
            "geo:" + ":".join(map(str, item["cell"])), case_id, {"incident_id": case_id}
        )
        self.event(
            tx,
            case_id,
            "ILLUSTRATION",
            "Illustrative fixture created; no report has been sent.",
        )

    def evidence(self, tx, evidence_id, principal, allow_public=False):
        evidence = self.require(tx, "evidence", evidence_id)
        shareable = False
        if (
            allow_public
            and evidence.get("public_approved")
            and evidence.get("incident_id")
        ):
            inc = tx.get("incident", evidence["incident_id"])
            shareable = bool(inc and inc["shared_public"])
        if evidence["owner_id"] != principal["user"]["id"] and not shareable:
            raise DomainError(
                "EVIDENCE_FORBIDDEN",
                "This resident has not shared this evidence with you.",
                403,
            )
        return evidence

    def check_evidence(self, tx, evidence_ids, principal):
        if len(evidence_ids) != len(set(evidence_ids)):
            raise DomainError(
                "DUPLICATE_ATTACHMENT", "Each photo may only be attached once."
            )
        return [self.evidence(tx, eid, principal) for eid in evidence_ids]

    def create_observation(self, principal, data):
        with self.store.atomic(principal["workspace_id"]) as tx:
            self.check_evidence(tx, data["evidence_ids"], principal)
            now = iso(self.now(tx))
            item = Observation(
                **data,
                id=ident(),
                workspace_id=tx.workspace_id,
                owner_id=principal["user"]["id"],
                created_at=now,
                updated_at=now,
            ).model_dump(mode="json")
            tx.put("observation", item["id"], item)
            return item

    def patch_observation(self, principal, observation_id, data):
        with self.store.atomic(principal["workspace_id"]) as tx:
            obs = self.require(tx, "observation", observation_id)
            self.owner(obs, principal)
            if obs.get("incident_id"):
                raise DomainError(
                    "OBSERVATION_LINKED",
                    "This observation is already linked. Revise the report draft or add another observation.",
                    409,
                )
            update = {k: v for k, v in data.items() if v is not None}
            self.check_evidence(
                tx, update.get("evidence_ids", obs["evidence_ids"]), principal
            )
            obs.update(
                update,
                analysis=None,
                updated_at=iso(self.now(tx)),
                version=obs["version"] + 1,
            )
            tx.put("observation", observation_id, obs)
            return obs

    def enqueue(self, tx, kind, payload, principal, due_at=None):
        op = ident()
        job = {
            "id": op,
            "kind": kind,
            "payload": payload,
            "principal": principal,
            "status": "pending",
            "due_at": due_at or self.now(tx),
            "attempts": 0,
            "lease_until": 0,
            "created_at": iso(self.now(tx)),
        }
        tx.put("job", op, job)
        tx.put("pending_job", op, {"job_id": op})
        if payload.get("incident_id"):
            tx.put("incident_jobs:" + payload["incident_id"], op, {"job_id": op})
        tx.put(
            "operation",
            op,
            {
                "id": op,
                "owner_id": principal["user"]["id"],
                "status": "pending",
                "result": None,
                "error": None,
                "created_at": iso(self.now(tx)),
            },
        )
        return op

    def start_analysis(self, principal, observation_id):
        with self.store.atomic(principal["workspace_id"]) as tx:
            obs = self.require(tx, "observation", observation_id)
            self.owner(obs, principal)
            op = self.enqueue(
                tx,
                "analyze",
                {"observation_id": observation_id, "version": obs["version"]},
                principal,
            )
        return op

    def candidates(self, tx, obs, principal):
        cell = cells(obs["latitude"], obs["longitude"])
        threshold = {"damaged_sidewalk": 45, "pothole": 25, "walkway_obstruction": 35}[
            obs["category"]
        ]
        result = []
        nearby = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nearby.extend(tx.list(f"geo:{cell[0] + dx}:{cell[1] + dy}", limit=100))
        for index in nearby:
            inc = tx.get("incident", index["incident_id"])
            if not inc:
                continue
            other = inc.get("cell", cells(inc["latitude"], inc["longitude"]))
            if (
                abs(cell[0] - other[0]) > 1
                or abs(cell[1] - other[1]) > 1
                or inc["category"] != obs["category"]
            ):
                continue
            if not inc["shared_public"] and not self.member(tx, inc, principal):
                continue
            if (
                self.now(tx) - epoch(inc["created_at"]) > 90 * 86400
                or inc["resolution_status"] == "RESIDENT_CONFIRMED_FIXED"
            ):
                continue
            if (
                obs.get("asset_id")
                and inc.get("asset_id")
                and obs["asset_id"] != inc["asset_id"]
            ):
                continue
            meters = distance(obs, inc)
            if meters <= threshold:
                words = set(obs["description"].lower().split()) & set(
                    inc["description"].lower().split()
                )
                result.append(
                    {
                        **self.public_projection(inc),
                        "distance_meters": round(meters),
                        "match_reason": "Nearby report in the same category; resident confirmation is required.",
                        "shared_terms": sorted(words)[:6],
                    }
                )
        return sorted(result, key=lambda c: c["distance_meters"])[:8]

    def decide(self, principal, observation_id, incident_id, different_issue):
        with self.store.atomic(principal["workspace_id"]) as tx:
            obs = self.require(tx, "observation", observation_id)
            self.owner(obs, principal)
            if obs.get("incident_id"):
                op = self.enqueue(
                    tx, "already_linked", {"incident_id": obs["incident_id"]}, principal
                )
                return op
            if not obs.get("analysis"):
                raise DomainError(
                    "ANALYSIS_REQUIRED", "Complete the observations review first.", 409
                )
            if obs["analysis"]["missing_information"]:
                raise DomainError(
                    "CLARIFICATION_REQUIRED",
                    "Answer the location and public-access questions before continuing.",
                    409,
                )
            if bool(incident_id) == bool(different_issue):
                raise DomainError(
                    "DECISION_REQUIRED",
                    "Choose an existing case or confirm that this is a different issue.",
                )
            if incident_id and incident_id not in [
                c["id"] for c in self.candidates(tx, obs, principal)
            ]:
                raise DomainError(
                    "INVALID_MATCH",
                    "The selected case is not an authorized nearby candidate.",
                    409,
                )
            op = self.enqueue(
                tx,
                "decide",
                {
                    "observation_id": observation_id,
                    "incident_id": incident_id,
                    "version": obs["version"],
                },
                principal,
            )
        return op

    def unlink_observation(self, principal, observation_id):
        with self.store.atomic(principal["workspace_id"]) as tx:
            obs = self.require(tx, "observation", observation_id)
            self.owner(obs, principal)
            if not obs.get("incident_id"):
                return {"unlinked": True}
            inc = self.require(tx, "incident", obs["incident_id"])
            if inc["observation_ids"][0] == observation_id:
                raise DomainError(
                    "CANONICAL_OBSERVATION",
                    "The initial observation anchors this case. Cancel the report instead of removing its original evidence.",
                    409,
                )
            inc["observation_ids"].remove(observation_id)
            inc["observation_count"] -= 1
            inc["version"] += 1
            inc["updated_at"] = iso(self.now(tx))
            tx.put("incident", inc["id"], inc)
            obs["incident_id"] = None
            tx.put("observation", obs["id"], obs)
            for eid in obs["evidence_ids"]:
                e = self.require(tx, "evidence", eid)
                e["public_approved"] = False
                e.pop("incident_id", None)
                tx.put("evidence", eid, e)
            remaining = [tx.get("observation", oid) for oid in inc["observation_ids"]]
            if not any(
                o and o["owner_id"] == principal["user"]["id"] for o in remaining
            ):
                tx.delete("membership", inc["id"] + "_" + principal["user"]["id"])
                tx.delete("user_incident:" + principal["user"]["id"], inc["id"])
            self.event(
                tx,
                inc["id"],
                "OBSERVATION_UNLINKED",
                "A resident reversed an observation link. The original observation was preserved; the submitted report is unchanged.",
            )
            return {"unlinked": True, "incident_id": inc["id"]}

    def public_projection(self, incident):
        keys = (
            "id",
            "title",
            "description",
            "category",
            "location_label",
            "agency_status",
            "resolution_status",
            "submission_status",
            "observation_count",
            "shared_public",
            "created_at",
            "updated_at",
            "version",
            "next_action",
        )
        result = {k: incident.get(k) for k in keys}
        result.update(
            latitude=round(incident["latitude"], 3),
            longitude=round(incident["longitude"], 3),
            owner_id="",
            following=False,
        )
        if incident.get("seeded"):
            result["thumbnail_url"] = "/fixtures/pothole.png"
        if incident.get("public_thumbnail_id"):
            result["thumbnail_url"] = (
                "/api/evidence/" + incident["public_thumbnail_id"] + "?public=true"
            )
        return result

    def list_incidents(
        self,
        principal,
        mine=False,
        category=None,
        status=None,
        following=False,
        cursor=None,
    ):
        with self.store.atomic(principal["workspace_id"]) as tx:
            items = []
            last = cursor
            # Bounded keyset read; a page may contain fewer matches after filtering.
            if mine:
                source = [
                    tx.get("incident", row["incident_id"])
                    for row in tx.list(
                        "user_incident:" + principal["user"]["id"],
                        limit=51,
                        after=cursor,
                    )
                ]
            elif following:
                source = [
                    tx.get("incident", row["incident_id"])
                    for row in tx.list(
                        "user_subscription:" + principal["user"]["id"],
                        limit=51,
                        after=cursor,
                    )
                ]
            else:
                source = tx.list("incident", limit=51, after=cursor)
            source = [item for item in source if item]
            for inc in source[:50]:
                last = inc["id"]
                is_member = self.member(tx, inc, principal)
                sub = tx.get("subscription", inc["id"] + "_" + principal["user"]["id"])
                is_following = bool(sub and sub["following"])
                if not inc["shared_public"] and not is_member:
                    continue
                if mine and not is_member:
                    continue
                if following and not is_following:
                    continue
                if category and inc["category"] != category:
                    continue
                if status and status not in (
                    inc["agency_status"],
                    inc["resolution_status"],
                    inc["submission_status"],
                ):
                    continue
                item = self.public_projection(inc)
                if is_member:
                    item.update(
                        owner_id=inc["owner_id"],
                        latitude=inc["latitude"],
                        longitude=inc["longitude"],
                    )
                item["following"] = is_following
                items.append(item)
            return {"items": items, "next_cursor": last if len(source) > 50 else None}

    def incident_detail(self, principal, incident_id):
        with self.store.atomic(principal["workspace_id"]) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.accessible(tx, inc, principal)
            is_owner = inc["owner_id"] == principal["user"]["id"]
            result = self.public_projection(inc)
            if self.member(tx, inc, principal):
                result.update(
                    owner_id=inc["owner_id"],
                    latitude=inc["latitude"],
                    longitude=inc["longitude"],
                )
            observations = []
            for oid in inc["observation_ids"]:
                obs = tx.get("observation", oid)
                if not obs:
                    continue
                own = obs["owner_id"] == principal["user"]["id"]
                if own or obs["share_public"]:
                    observations.append(
                        {
                            "id": obs["id"],
                            "description": obs["description"]
                            if own or obs["share_public"]
                            else "A resident added an observation.",
                            "created_at": obs["created_at"],
                            "is_yours": own,
                            "resident_name": (
                                tx.get("user", obs["owner_id"]) or {}
                            ).get("name", "Neighbor")
                            if own
                            else "Neighbor",
                            "evidence": [
                                self.evidence_projection(
                                    tx.get("evidence", eid), public=not own
                                )
                                for eid in obs["evidence_ids"]
                                if tx.get("evidence", eid)
                                and (
                                    own
                                    or tx.get("evidence", eid).get("public_approved")
                                )
                            ],
                        }
                    )
            head = tx.get("event_head", incident_id) or {"event_ids": []}
            events = [
                self.event_projection(e)
                for eid in head["event_ids"]
                if (e := tx.get("event:" + incident_id, eid))
            ]
            verifications = []
            for record in tx.list("verification:" + incident_id, limit=100):
                own = record["user_id"] == principal["user"]["id"]
                public = record.get("share_public") and inc["shared_public"]
                item = {k: record.get(k) for k in ("id", "choice", "created_at")}
                item.update(
                    is_yours=own, note=record.get("note", "") if own or public else ""
                )
                if record.get("evidence_id") and (own or public):
                    e = tx.get("evidence", record["evidence_id"])
                    if e:
                        item["evidence"] = self.evidence_projection(e, public=not own)
                verifications.append(item)
            result.update(
                observations=observations,
                verifications=verifications,
                events=events,
                is_owner=is_owner,
                analysis=inc.get("analysis"),
                routing=inc.get("routing"),
                agent_activity=[e for e in events if e.get("tool")],
                following=bool(
                    (
                        tx.get(
                            "subscription", incident_id + "_" + principal["user"]["id"]
                        )
                        or {}
                    ).get("following")
                ),
            )
            for phase in (
                inc.get("analysis") or {},
                inc.get("routing") or {},
                inc.get("coordinator") or {},
            ):
                result["agent_activity"].extend(phase.get("agent_activity", []))
            if is_owner and inc.get("draft_id"):
                result["draft"] = self.draft_projection(
                    self.require(tx, "draft", inc["draft_id"])
                )
            else:
                result["draft"] = None
            if inc.get("ticket_id"):
                ticket = self.require(tx, "ticket", inc["ticket_id"])
                result["ticket"] = {
                    k: ticket.get(k)
                    for k in (
                        "id",
                        "receipt_id",
                        "raw_status",
                        "normalized_status",
                        "closure_note",
                        "created_at",
                        "history",
                        "steps",
                    )
                }
                if is_owner:
                    result["ticket"]["url"] = ticket["url"]
                    if ticket.get("screenshot_evidence_id"):
                        result["ticket"]["screenshot_url"] = (
                            "/api/evidence/" + ticket["screenshot_evidence_id"]
                        )
            else:
                result["ticket"] = None
            return result

    def evidence_projection(self, evidence, public=False):
        return {
            k: evidence[k]
            for k in (
                "id",
                "sha256",
                "content_type",
                "size",
                "created_at",
                "sanitized",
                "public_approved",
            )
        } | {
            "url": "/api/evidence/"
            + evidence["id"]
            + ("?public=true" if public else ""),
            "thumbnail_url": "/api/evidence/"
            + evidence["id"]
            + ("?public=true" if public else ""),
        }

    def event_projection(self, event):
        return {k: v for k, v in event.items() if k not in ("workspace_id",)}

    def draft_projection(self, draft):
        return {k: v for k, v in draft.items() if k not in ("workspace_id", "owner_id")}

    def make_draft(self, tx, inc, principal, data):
        if inc["submission_status"] in RESERVED:
            raise DomainError(
                "SUBMISSION_RESERVED",
                "This case already has a reserved or submitted report. Reconcile its outcome first.",
                409,
            )
        if not inc.get("routing", {}).get("supported"):
            raise DomainError(
                "HANDOFF_REQUIRED",
                "Routing needs review. Download the handoff packet; automatic submission is unavailable.",
                409,
            )
        attachments = data.get("attachment_ids")
        if attachments is None:
            first = self.require(tx, "observation", inc["observation_ids"][0])
            attachments = first["evidence_ids"]
        evidence = self.check_evidence(tx, attachments, principal)
        if (
            self.settings.mode == "aws"
            and sum(e["size"] for e in evidence) > 4 * 1024 * 1024
        ):
            raise DomainError(
                "ATTACHMENTS_TOO_LARGE",
                "AWS demo reports support up to 4 MB of photos in total. Remove photos and review a fresh report.",
                413,
            )
        previous = tx.get("draft", inc.get("draft_id", ""))
        revision = previous["revision"] + 1 if previous else 1
        now = self.now(tx)
        frozen = {
            "recipient": "Demo Borough Public Works",
            "category": inc["category"],
            "description": data.get("description") or inc["description"],
            "location_label": inc["location_label"],
            "latitude": inc["latitude"],
            "longitude": inc["longitude"],
            "contact": data.get("contact") or {"name": "", "email": "", "phone": ""},
            "attachment_ids": attachments,
            "attachment_hashes": [e["sha256"] for e in evidence],
        }
        draft = {
            **frozen,
            "id": ident(),
            "incident_id": inc["id"],
            "owner_id": inc["owner_id"],
            "revision": revision,
            "payload_hash": digest(frozen),
            "expires_at": iso(now + self.settings.approval_seconds),
            "status": "AWAITING_APPROVAL",
            "created_at": iso(now),
        }
        tx.put("draft", draft["id"], draft)
        inc.update(
            draft_id=draft["id"],
            submission_status="AWAITING_APPROVAL",
            version=inc["version"] + 1,
            updated_at=iso(now),
            next_action="Review the exact report and approve when ready.",
        )
        tx.put("incident", inc["id"], inc)
        self.event(
            tx,
            inc["id"],
            "AWAITING_APPROVAL",
            f"Report revision {revision} is awaiting the reporting resident’s approval.",
            tool="agent.prepare_submission",
        )
        return draft

    def create_draft(self, principal, incident_id, data):
        with self.store.atomic(principal["workspace_id"]) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            return self.draft_projection(self.make_draft(tx, inc, principal, data))

    def approve(self, principal, incident_id, data):
        with self.store.atomic(principal["workspace_id"]) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            draft = self.require(tx, "draft", data["draft_id"])
            if (
                draft["incident_id"] != incident_id
                or inc.get("draft_id") != draft["id"]
            ):
                raise DomainError(
                    "STALE_APPROVAL",
                    "The report changed. Review and approve the latest revision.",
                    409,
                )
            if draft["payload_hash"] != data["payload_hash"]:
                raise DomainError(
                    "PAYLOAD_CHANGED",
                    "The displayed report does not match this revision.",
                    409,
                )
            if epoch(draft["expires_at"]) <= self.now(tx):
                raise DomainError(
                    "APPROVAL_EXPIRED",
                    "This report preview expired. Create and review a fresh revision.",
                    409,
                )
            if inc["submission_status"] in RESERVED:
                previous = self.require(tx, "attempt", inc["attempt_id"])
                if previous["draft_id"] == draft["id"]:
                    return previous["operation_id"]
                raise DomainError(
                    "SUBMISSION_RESERVED",
                    "This incident already has a submission reserved.",
                    409,
                )
            if inc["submission_status"] == "CANCELLED":
                raise DomainError(
                    "CANCELLED",
                    "Create a fresh report revision before approving again.",
                    409,
                )
            if not inc["routing"].get("supported"):
                raise DomainError(
                    "HANDOFF_REQUIRED",
                    "Automatic submission is not supported for this location.",
                    409,
                )
            evidence = self.check_evidence(tx, draft["attachment_ids"], principal)
            if (
                self.settings.mode == "aws"
                and sum(e["size"] for e in evidence) > 4 * 1024 * 1024
            ):
                raise DomainError(
                    "ATTACHMENTS_TOO_LARGE",
                    "AWS demo reports support up to 4 MB of photos in total. Remove photos and review a fresh report.",
                    413,
                )
            if [e["sha256"] for e in evidence] != draft["attachment_hashes"]:
                raise DomainError(
                    "ATTACHMENT_CHANGED",
                    "An approved attachment no longer matches its recorded hash.",
                    409,
                )
            approval_id, attempt_id = ident(), ident()
            approval = Approval(
                id=approval_id,
                approver_id=principal["user"]["id"],
                incident_id=incident_id,
                draft_id=draft["id"],
                payload_hash=draft["payload_hash"],
                recipient=draft["recipient"],
                attachment_hashes=draft["attachment_hashes"],
                expires_at=draft["expires_at"],
                status="RESERVED",
            ).model_dump()
            approval.update(
                created_at=iso(self.now(tx)),
                publish_consent=data["publish_consent"],
                share_evidence=data.get("share_evidence", False),
            )
            payload = {
                key: draft[key]
                for key in (
                    "recipient",
                    "category",
                    "description",
                    "location_label",
                    "latitude",
                    "longitude",
                    "contact",
                    "attachment_ids",
                    "attachment_hashes",
                    "payload_hash",
                )
            }
            attempt = SubmissionAttempt(
                id=attempt_id,
                incident_id=incident_id,
                draft_id=draft["id"],
                approval_id=approval_id,
                status="IN_FLIGHT",
                payload=payload,
            ).model_dump()
            op = self.enqueue(
                tx,
                "submit",
                {"incident_id": incident_id, "attempt_id": attempt_id},
                principal,
            )
            attempt.update(operation_id=op, created_at=iso(self.now(tx)))
            tx.put("approval", approval_id, approval)
            tx.put("attempt", attempt_id, attempt)
            # Separate publication consent never follows agency-send approval implicitly.
            inc.update(
                attempt_id=attempt_id,
                submission_status="IN_FLIGHT",
                shared_public=data["publish_consent"],
                version=inc["version"] + 1,
                updated_at=iso(self.now(tx)),
                next_action="The approved report is reserved for one submission.",
            )
            publish_photos = bool(
                data["publish_consent"] and data.get("share_evidence")
            )
            for e in evidence:
                e["public_approved"] = publish_photos
                e["incident_id"] = incident_id
                tx.put("evidence", e["id"], e)
            if publish_photos and evidence:
                inc["public_thumbnail_id"] = evidence[0]["id"]
            elif inc.get("public_thumbnail_id") in draft["attachment_ids"]:
                inc.pop("public_thumbnail_id", None)
            tx.put("incident", incident_id, inc)
            self.event(
                tx,
                incident_id,
                "APPROVED",
                f"Exact report revision {draft['revision']} approved and atomically reserved.",
                op,
            )
            return op

    def cancel(self, principal, incident_id):
        with self.store.atomic(principal["workspace_id"]) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            attempt = tx.get("attempt", inc.get("attempt_id", ""))
            if attempt and attempt.get("write_started"):
                raise DomainError(
                    "WRITE_ALREADY_STARTED",
                    "The agency write has begun. Cancellation cannot undo it; check the receipt or reconcile the outcome.",
                    409,
                )
            if attempt:
                attempt["status"] = "CANCELLED"
                tx.put("attempt", attempt["id"], attempt)
                approval = self.require(tx, "approval", attempt["approval_id"])
                approval["status"] = "CANCELLED"
                tx.put("approval", approval["id"], approval)
            inc.update(
                submission_status="CANCELLED",
                next_action="Submission cancelled before any agency write.",
                updated_at=iso(self.now(tx)),
                version=inc["version"] + 1,
            )
            tx.put("incident", incident_id, inc)
            self.event(
                tx,
                incident_id,
                "CANCELLED",
                "Submission cancelled before the agency write began.",
            )
            return {"cancelled": True}

    def reconcile(self, principal, incident_id):
        with self.store.atomic(principal["workspace_id"]) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            if inc["submission_status"] != "OUTCOME_UNKNOWN":
                raise DomainError(
                    "RECONCILIATION_NOT_NEEDED",
                    "Only an uncertain submission can be reconciled.",
                    409,
                )
            return self.enqueue(
                tx,
                "reconcile",
                {"incident_id": incident_id, "attempt_id": inc["attempt_id"]},
                principal,
            )

    def verification(self, principal, incident_id, data):
        with self.store.atomic(principal["workspace_id"]) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.accessible(tx, inc, principal)
            if data.get("evidence_id"):
                self.evidence(tx, data["evidence_id"], principal)
            mapping = {
                "looks_fixed": "RESIDENT_CONFIRMED_FIXED",
                "still_present": "STILL_PRESENT",
                "unable_to_verify": "UNABLE_TO_VERIFY",
            }
            inc.update(
                resolution_status=mapping[data["choice"]],
                updated_at=iso(self.now(tx)),
                version=inc["version"] + 1,
                next_action={
                    "looks_fixed": "Resident-confirmed fixed. This is not a safety certification.",
                    "still_present": "The issue is still present. A new outbound follow-up requires fresh approval.",
                    "unable_to_verify": "Verification is optional. Stay in a safe, accessible place.",
                }[data["choice"]],
            )
            tx.put("incident", incident_id, inc)
            vid = ident()
            record = {
                **data,
                "id": vid,
                "incident_id": incident_id,
                "user_id": principal["user"]["id"],
                "created_at": iso(self.now(tx)),
            }
            tx.put("verification", vid, record)
            tx.put("verification:" + incident_id, vid, record)
            if data.get("evidence_id") and data.get("share_public"):
                e = self.evidence(tx, data["evidence_id"], principal)
                e["public_approved"] = True
                e["incident_id"] = incident_id
                tx.put("evidence", e["id"], e)
            text = {
                "looks_fixed": "A resident confirmed that the issue looks fixed.",
                "still_present": "A resident reports the issue is still present. No new agency submission was created.",
                "unable_to_verify": "A resident was unable to verify the repair.",
            }[data["choice"]]
            self.event(tx, incident_id, "VERIFICATION", text)
            self.notify(tx, incident_id, text)
            return {"resolution_status": inc["resolution_status"]}

    def claim_job(self, workspace_id, job_id=None):
        with self.store.atomic(workspace_id) as tx:
            now = self.now(tx)
            jobs = (
                [tx.get("job", job_id)]
                if job_id
                else [
                    tx.get("job", row["job_id"])
                    for row in tx.list("pending_job", limit=500)
                ]
            )
            for job in jobs:
                if (
                    not job
                    or job["status"] in ("completed", "failed")
                    or job["due_at"] > now
                    or job["lease_until"] > now
                ):
                    continue
                if job["status"] == "running" and job["kind"] == "submit":
                    attempt = self.require(tx, "attempt", job["payload"]["attempt_id"])
                    if attempt.get("write_started"):
                        self.mark_unknown(
                            tx,
                            attempt,
                            job["id"],
                            "Worker restarted after a possible agency write. Receipt lookup is required; no write was retried.",
                        )
                        job["status"] = "completed"
                        tx.delete("pending_job", job["id"])
                        tx.put("job", job["id"], job)
                        tx.put(
                            "operation",
                            job["id"],
                            {
                                "id": job["id"],
                                "owner_id": job["principal"]["user"]["id"],
                                "status": "completed",
                                "result": {
                                    "submission_status": "OUTCOME_UNKNOWN",
                                    "incident_id": attempt["incident_id"],
                                },
                                "error": None,
                            },
                        )
                        continue
                job.update(
                    status="running",
                    lease_until=now + self.settings.lease_seconds,
                    attempts=job["attempts"] + 1,
                    lease_owner=ident(),
                )
                tx.put("job", job["id"], job)
                op = self.require(tx, "operation", job["id"])
                op["status"] = "running"
                tx.put("operation", op["id"], op)
                return job
        return None

    async def run_job(self, workspace_id, job_id=None):
        job = self.claim_job(workspace_id, job_id)
        if not job:
            return False
        try:
            result = await self.execute_job(workspace_id, job)
            with self.store.atomic(workspace_id) as tx:
                current = self.require(tx, "job", job["id"])
                if current.get("lease_owner") != job["lease_owner"]:
                    return True
                current.update(status="completed", lease_until=0)
                tx.put("job", job["id"], current)
                tx.delete("pending_job", job["id"])
                op = self.require(tx, "operation", job["id"])
                op.update(status="completed", result=result, error=None)
                tx.put("operation", op["id"], op)
        except Exception as exc:
            code = exc.code if isinstance(exc, DomainError) else "OPERATION_FAILED"
            message = (
                exc.message
                if isinstance(exc, DomainError)
                else "The operation failed. Review configuration or retry; no fixture fallback was used."
            )
            with self.store.atomic(workspace_id) as tx:
                current = self.require(tx, "job", job["id"])
                if current.get("lease_owner") != job["lease_owner"]:
                    return True
                if job["kind"] == "submit":
                    attempt = self.require(tx, "attempt", job["payload"]["attempt_id"])
                    if attempt["status"] == "IN_FLIGHT":
                        if attempt.get("write_started"):
                            self.mark_unknown(
                                tx,
                                attempt,
                                job["id"],
                                "Execution failed after a possible agency write. Receipt reconciliation is required.",
                            )
                        else:
                            attempt["status"] = "FAILED_BEFORE_SUBMISSION"
                            tx.put("attempt", attempt["id"], attempt)
                            inc = self.require(tx, "incident", attempt["incident_id"])
                            inc.update(
                                submission_status="FAILED_BEFORE_SUBMISSION",
                                next_action="Submission stopped before sending. Review the error and create a fresh report revision.",
                            )
                            tx.put("incident", inc["id"], inc)
                # Safe reads only: finite backoff. Analysis/route can be explicitly retried by resident.
                retry = job["kind"] == "check_status" and job["attempts"] < 3
                current.update(
                    status="pending" if retry else "failed",
                    lease_until=0,
                    due_at=self.now(tx)
                    + min(60, 2 ** job["attempts"])
                    + secrets.randbelow(3),
                )
                tx.put("job", job["id"], current)
                if not retry:
                    tx.delete("pending_job", job["id"])
                op = self.require(tx, "operation", job["id"])
                op.update(
                    status="pending" if retry else "failed",
                    error={"code": code, "message": message, "retryable": retry},
                )
                tx.put("operation", op["id"], op)
                if job["payload"].get("incident_id"):
                    self.event(
                        tx,
                        job["payload"]["incident_id"],
                        "OPERATION_FAILED",
                        message,
                        job["id"],
                    )
        return True

    def engine(self):
        if self.settings.mode == "local":
            return fixtures
        from services.agents import runtime_client

        return runtime_client

    async def execute_job(self, workspace_id, job):
        p = job["principal"]
        payload = job["payload"]
        kind = job["kind"]
        if kind == "already_linked":
            return payload
        if kind == "analyze":
            with self.store.atomic(workspace_id) as tx:
                obs = self.require(tx, "observation", payload["observation_id"])
                self.owner(obs, p)
                evidence = self.check_evidence(tx, obs["evidence_ids"], p)
                candidates = self.candidates(tx, obs, p)
            analysis = Analysis.model_validate(
                await asyncio.to_thread(
                    self.engine().analyze, obs, evidence, candidates, p
                )
            ).model_dump(mode="json")
            required = fixtures.analyze(obs, evidence, candidates, p)[
                "missing_information"
            ]
            analysis["missing_information"] = list(
                dict.fromkeys(analysis["missing_information"] + required)
            )
            with self.store.atomic(workspace_id) as tx:
                current = self.require(tx, "observation", obs["id"])
                if current["version"] != payload["version"]:
                    raise DomainError(
                        "STALE_ANALYSIS",
                        "The observation changed. Analyze the current revision.",
                        409,
                    )
                current["analysis"] = analysis
                tx.put("observation", current["id"], current)
            return {"analysis": analysis, "duplicate_candidates": candidates}
        if kind == "decide":
            with self.store.atomic(workspace_id) as tx:
                obs = self.require(tx, "observation", payload["observation_id"])
                self.owner(obs, p)
                if obs.get("incident_id"):
                    return {"incident_id": obs["incident_id"]}
                if obs["version"] != payload["version"]:
                    raise DomainError(
                        "STALE_DECISION",
                        "Observation changed; review candidates again.",
                        409,
                    )
            routing = (
                await asyncio.to_thread(self.engine().route, obs, p)
                if not payload["incident_id"]
                else None
            )
            if routing:
                trusted = fixtures.route(obs, p)
                if (
                    not trusted["supported"]
                    or routing.get("recipient") != "Demo Borough Public Works"
                    or routing.get("category") != obs["category"]
                ):
                    routing = {
                        **routing,
                        "supported": False,
                        "status": "HANDOFF_REQUIRED",
                        "recipient": None,
                        "unresolved_questions": trusted["unresolved_questions"]
                        or ["The agent route does not match trusted configuration."],
                    }
            proposal = (
                await asyncio.to_thread(self.engine().prepare, obs, routing, p)
                if routing and routing.get("supported")
                else None
            )
            with self.store.atomic(workspace_id) as tx:
                obs = self.require(tx, "observation", obs["id"])
                if obs.get("incident_id"):
                    return {"incident_id": obs["incident_id"]}
                if obs["version"] != payload["version"]:
                    raise DomainError(
                        "STALE_DECISION",
                        "Observation changed; review candidates again.",
                        409,
                    )
                if payload["incident_id"]:
                    if payload["incident_id"] not in [
                        c["id"] for c in self.candidates(tx, obs, p)
                    ]:
                        raise DomainError(
                            "INVALID_MATCH",
                            "The candidate changed or is no longer shared.",
                            409,
                        )
                    inc = self.require(tx, "incident", payload["incident_id"])
                    inc["observation_ids"].append(obs["id"])
                    inc["observation_count"] += 1
                    inc["version"] += 1
                    inc["updated_at"] = iso(self.now(tx))
                    tx.put("incident", inc["id"], inc)
                    self.event(
                        tx,
                        inc["id"],
                        "OBSERVATION_LINKED",
                        "A neighbor added evidence to the same case. No second submission workflow was created.",
                        job["id"],
                        tool="link_observation",
                    )
                    self.notify(
                        tx, inc["id"], "A neighbor added an observation to this case."
                    )
                else:
                    now = iso(self.now(tx))
                    iid = ident()
                    inc = {
                        "id": iid,
                        "owner_id": p["user"]["id"],
                        "title": CATEGORY_TITLES[obs["category"]],
                        "description": obs["description"],
                        "category": obs["category"],
                        "latitude": obs["latitude"],
                        "longitude": obs["longitude"],
                        "location_label": obs["location_label"],
                        "agency_status": "NOT_SUBMITTED",
                        "resolution_status": "UNVERIFIED",
                        "submission_status": "PREPARED",
                        "observation_count": 1,
                        "observation_ids": [obs["id"]],
                        "shared_public": obs["share_public"],
                        "version": 1,
                        "created_at": now,
                        "updated_at": now,
                        "cell": list(cells(obs["latitude"], obs["longitude"])),
                        "asset_id": obs.get("asset_id"),
                        "analysis": obs["analysis"],
                        "routing": routing,
                        "coordinator": proposal,
                    }
                    tx.put("incident", iid, inc)
                    tx.put(
                        "geo:" + ":".join(map(str, inc["cell"])),
                        iid,
                        {"incident_id": iid},
                    )
                    self.event(
                        tx,
                        iid,
                        "MATCHED",
                        "Checking nearby cases completed. Resident confirmed a distinct issue.",
                        job["id"],
                        tool="query_nearby_incidents",
                    )
                    self.event(
                        tx,
                        iid,
                        "ANALYZED",
                        "Observations reviewed; dimensions and safety remain unverified.",
                        job["id"],
                        tool="agent.analyze",
                    )
                    self.event(
                        tx,
                        iid,
                        "ROUTED",
                        "Routing checked against the trusted fictional registry.",
                        job["id"],
                        tool="agent.lookup_jurisdiction",
                        result="Supported fictional destination"
                        if routing.get("supported")
                        else "Assisted handoff required",
                    )
                    if routing.get("supported"):
                        self.make_draft(
                            tx,
                            inc,
                            p,
                            {
                                "description": proposal["description"]
                                if proposal
                                else obs["description"]
                            },
                        )
                    else:
                        inc.update(
                            submission_status="HANDOFF_REQUIRED",
                            next_action="Confirm jurisdiction and recipient. Download a complete report packet for assisted handoff.",
                        )
                        tx.put("incident", iid, inc)
                        self.event(
                            tx,
                            iid,
                            "HANDOFF_REQUIRED",
                            "Jurisdiction or asset responsibility is uncertain; automatic sending is disabled.",
                            job["id"],
                        )
                obs["incident_id"] = inc["id"]
                tx.put("observation", obs["id"], obs)
                if obs["share_public"] and obs.get("share_evidence", False):
                    for e in self.check_evidence(tx, obs["evidence_ids"], p):
                        e["public_approved"] = True
                        e["incident_id"] = inc["id"]
                        tx.put("evidence", e["id"], e)
                    if obs["evidence_ids"] and not inc.get("public_thumbnail_id"):
                        inc = tx.get("incident", inc["id"])
                        inc["public_thumbnail_id"] = obs["evidence_ids"][0]
                        tx.put("incident", inc["id"], inc)
                tx.put(
                    "membership",
                    inc["id"] + "_" + p["user"]["id"],
                    {"incident_id": inc["id"], "user_id": p["user"]["id"]},
                )
                tx.put(
                    "user_incident:" + p["user"]["id"],
                    inc["id"],
                    {"incident_id": inc["id"]},
                )
                self.subscribe(tx, inc["id"], p["user"]["id"])
                return {"incident_id": inc["id"]}
        if kind == "submit":
            return await self.execute_submission(workspace_id, job)
        if kind == "reconcile":
            from services.worker.browser import lookup_receipt

            with self.store.atomic(workspace_id) as tx:
                attempt = self.require(tx, "attempt", payload["attempt_id"])
            receipt = await lookup_receipt(attempt["id"])
            if receipt:
                with self.store.atomic(workspace_id) as tx:
                    self.record_receipt(tx, attempt, receipt, job)
                return {
                    "incident_id": attempt["incident_id"],
                    "submission_status": "RECEIPT_CONFIRMED",
                }
            return {
                "incident_id": attempt["incident_id"],
                "submission_status": "OUTCOME_UNKNOWN",
                "message": "No receipt found. Human review is required; no report was resubmitted.",
            }
        if kind == "check_status":
            from services.worker.browser import get_ticket_status

            with self.store.atomic(workspace_id) as tx:
                inc = self.require(tx, "incident", payload["incident_id"])
                ticket = self.require(tx, "ticket", inc["ticket_id"])
            status = await get_ticket_status(ticket["receipt_id"])
            with self.store.atomic(workspace_id) as tx:
                self.record_status(tx, inc["id"], status, job["id"])
                if (
                    status.get("normalized_status") != "CLOSED"
                    and payload.get("checks", 0) + 1 < self.settings.max_status_checks
                ):
                    self.enqueue(
                        tx,
                        "check_status",
                        {
                            "incident_id": inc["id"],
                            "checks": payload.get("checks", 0) + 1,
                        },
                        p,
                        self.now(tx) + self.settings.status_interval,
                    )
            return {
                "incident_id": inc["id"],
                "agency_status": status.get("normalized_status", "UNKNOWN"),
            }
        raise DomainError("UNKNOWN_JOB", "The worker does not support this operation.")

    def mark_unknown(self, tx, attempt, operation_id, message):
        attempt["status"] = "OUTCOME_UNKNOWN"
        tx.put("attempt", attempt["id"], attempt)
        inc = self.require(tx, "incident", attempt["incident_id"])
        inc.update(
            submission_status="OUTCOME_UNKNOWN",
            agency_status="UNKNOWN",
            next_action="Receipt uncertain. Reconcile the existing attempt; no automatic resubmission.",
            updated_at=iso(self.now(tx)),
            version=inc["version"] + 1,
        )
        tx.put("incident", inc["id"], inc)
        self.event(
            tx,
            inc["id"],
            "OUTCOME_UNKNOWN",
            message,
            operation_id,
            tool="browser.submit_approved_report",
        )
        self.notify(
            tx,
            inc["id"],
            "The report outcome is uncertain. The owner can look up the existing receipt without resubmitting.",
        )

    async def execute_submission(self, workspace_id, job):
        from services.worker.browser import (
            submit_report,
            OutcomeUnknown,
            HandoffRequired,
            FailedBeforeSubmission,
        )

        with self.store.atomic(workspace_id) as tx:
            attempt = self.require(tx, "attempt", job["payload"]["attempt_id"])
            inc = self.require(tx, "incident", attempt["incident_id"])
            approval = self.require(tx, "approval", attempt["approval_id"])
            if (
                attempt["status"] == "CANCELLED"
                or inc["submission_status"] == "CANCELLED"
            ):
                return {"incident_id": inc["id"], "submission_status": "CANCELLED"}
            if attempt["status"] != "IN_FLIGHT":
                return {
                    "incident_id": inc["id"],
                    "submission_status": attempt["status"],
                }
            if epoch(approval["expires_at"]) <= self.now(tx):
                attempt["status"] = "FAILED_BEFORE_SUBMISSION"
                tx.put("attempt", attempt["id"], attempt)
                inc.update(
                    submission_status="FAILED_BEFORE_SUBMISSION",
                    next_action="Approval expired before submission. Review a fresh revision.",
                )
                tx.put("incident", inc["id"], inc)
                return {
                    "incident_id": inc["id"],
                    "submission_status": "FAILED_BEFORE_SUBMISSION",
                }
            if (
                inc.get("draft_id") != approval["draft_id"]
                or approval["payload_hash"] != attempt["payload"]["payload_hash"]
            ):
                raise DomainError(
                    "STALE_APPROVAL",
                    "Submission approval no longer matches the frozen report.",
                    409,
                )
            # Canonical incident is strongly read and reserved. Linking never changes this payload.
            paths = []
            for eid, expected in zip(
                attempt["payload"]["attachment_ids"],
                attempt["payload"]["attachment_hashes"],
            ):
                evidence = self.require(tx, "evidence", eid)
                path = self.materialize_evidence(evidence)
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise DomainError(
                        "ATTACHMENT_CHANGED",
                        "The attachment failed its integrity check.",
                        409,
                    )
                paths.append(str(path))
            frozen = dict(attempt["payload"])
            frozen["simulate_lost_receipt"] = bool(
                (tx.get("workspace", workspace_id) or {}).get("lost_receipt")
            )
            frozen["workspace_id"] = workspace_id
            self.event(
                tx,
                inc["id"],
                "SUBMISSION_PREPARING",
                "Browser is preparing the exact approved submission.",
                job["id"],
                tool="browser.prepare_submission",
            )

        def before_write():
            with self.store.atomic(workspace_id) as tx:
                active = self.require(tx, "attempt", attempt["id"])
                current = self.require(tx, "incident", inc["id"])
                approval = self.require(tx, "approval", active["approval_id"])
                leased = self.require(tx, "job", job["id"])
                if (
                    active["status"] != "IN_FLIGHT"
                    or current["submission_status"] != "IN_FLIGHT"
                    or leased.get("lease_owner") != job["lease_owner"]
                ):
                    raise HandoffRequired(
                        "The submission was cancelled or its worker lease changed before the agency write."
                    )
                if (
                    epoch(approval["expires_at"]) <= self.now(tx)
                    or current.get("draft_id") != approval["draft_id"]
                ):
                    raise HandoffRequired(
                        "Approval expired or changed before the agency write."
                    )
                active["write_started"] = True
                active["write_started_at"] = iso(self.now(tx))
                tx.put("attempt", active["id"], active)
                attempt.update(active)
                self.event(
                    tx,
                    inc["id"],
                    "SUBMISSION_STARTED",
                    "Browser started the exact approved agency write.",
                    job["id"],
                    tool="browser.submit_approved_report",
                )

        try:
            receipt = await submit_report(
                frozen,
                attempt["id"],
                paths,
                str(self.evidence_dir / (attempt["id"] + "-receipt.png")),
                before_write=before_write,
            )
        except OutcomeUnknown:
            with self.store.atomic(workspace_id) as tx:
                self.mark_unknown(
                    tx,
                    attempt,
                    job["id"],
                    "The connection ended after the report may have been accepted. Receipt lookup is required; no retry was sent.",
                )
            return {"incident_id": inc["id"], "submission_status": "OUTCOME_UNKNOWN"}
        except (HandoffRequired, FailedBeforeSubmission) as exc:
            state = (
                "HANDOFF_REQUIRED"
                if isinstance(exc, HandoffRequired)
                else "FAILED_BEFORE_SUBMISSION"
            )
            with self.store.atomic(workspace_id) as tx:
                current_attempt = self.require(tx, "attempt", attempt["id"])
                if current_attempt["status"] == "CANCELLED":
                    return {"incident_id": inc["id"], "submission_status": "CANCELLED"}
                attempt.update(status=state, write_started=False)
                tx.put("attempt", attempt["id"], attempt)
                current = self.require(tx, "incident", inc["id"])
                current.update(
                    submission_status=state,
                    next_action="Browser stopped before sending. Review the agency form and configuration.",
                )
                tx.put("incident", inc["id"], current)
                self.event(
                    tx,
                    inc["id"],
                    state,
                    "Browser stopped before submission; recipient, form, or portal availability needs review.",
                    job["id"],
                )
            return {"incident_id": inc["id"], "submission_status": state}
        except BaseException:
            with self.store.atomic(workspace_id) as tx:
                self.mark_unknown(
                    tx,
                    attempt,
                    job["id"],
                    "An unexpected browser interruption left the outcome uncertain. No automatic retry was sent.",
                )
            return {"incident_id": inc["id"], "submission_status": "OUTCOME_UNKNOWN"}
        with self.store.atomic(workspace_id) as tx:
            self.record_receipt(tx, attempt, receipt, job)
        return {
            "incident_id": inc["id"],
            "submission_status": "RECEIPT_CONFIRMED",
            "receipt_id": receipt["receipt_id"],
        }

    def record_receipt(self, tx, attempt, receipt, job):
        inc = self.require(tx, "incident", attempt["incident_id"])
        if inc.get("ticket_id"):
            return
        ticket_id = ident()
        now = iso(self.now(tx))
        ticket = {
            "id": ticket_id,
            "incident_id": inc["id"],
            "attempt_id": attempt["id"],
            "receipt_id": receipt["receipt_id"],
            "url": receipt["url"],
            "raw_status": receipt.get("raw_status", "RECEIVED"),
            "normalized_status": receipt.get("normalized_status", "RECEIVED"),
            "closure_note": "",
            "created_at": now,
            "history": [],
            "steps": receipt.get("steps", []),
        }
        screenshot = receipt.get("screenshot_path")
        if screenshot:
            evidence = None
            try:
                path = Path(screenshot)
                if not path.is_file():
                    raise FileNotFoundError("Optional receipt screenshot unavailable")
                path.chmod(0o600)
                raw = path.read_bytes()
                eid = ident()
                evidence = {
                    "id": eid,
                    "owner_id": inc["owner_id"],
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content_type": "image/png",
                    "size": len(raw),
                    "created_at": now,
                    "sanitized": True,
                    "public_approved": False,
                    "purpose": "redacted_receipt",
                }
                if self.settings.mode == "local":
                    evidence["path"] = screenshot
                else:
                    import boto3

                    key = tx.workspace_id + "/" + eid + ".png"
                    boto3.client("s3", region_name=self.settings.region).put_object(
                        Bucket=self.settings.evidence_bucket,
                        Key=key,
                        Body=raw,
                        ContentType="image/png",
                        ServerSideEncryption="AES256",
                    )
                    evidence["object_key"] = key
                    evidence["s3_key"] = key
            except Exception:
                # A verified receipt is authoritative even when optional capture
                # storage fails. Keep backend/transaction errors outside this
                # narrow optional I/O boundary, and never log storage secrets.
                evidence = None
                ticket["steps"] = [
                    *ticket["steps"],
                    "Receipt confirmed; optional screenshot storage unavailable",
                ]
                self.event(
                    tx,
                    inc["id"],
                    "RECEIPT_CAPTURE_UNAVAILABLE",
                    "The agency receipt is confirmed. Optional screenshot storage was unavailable.",
                    job["id"],
                    tool="storage.receipt_screenshot",
                )
            if evidence is not None:
                tx.put("evidence", evidence["id"], evidence)
                ticket["screenshot_evidence_id"] = evidence["id"]
        tx.put("ticket", ticket_id, ticket)
        attempt["status"] = "RECEIPT_CONFIRMED"
        tx.put("attempt", attempt["id"], attempt)
        inc.update(
            ticket_id=ticket_id,
            submission_status="RECEIPT_CONFIRMED",
            agency_status=ticket["normalized_status"],
            next_action="Following the agency response. Physical resolution remains unverified.",
            updated_at=now,
            version=inc["version"] + 1,
        )
        tx.put("incident", inc["id"], inc)
        self.event(
            tx,
            inc["id"],
            "RECEIPT_RECEIVED",
            "Receipt received from the fictional agency portal: "
            + ticket["receipt_id"],
            job["id"],
            tool="browser.capture_receipt",
        )
        self.notify(
            tx,
            inc["id"],
            "One agency report was received. Everyone follows the same ticket.",
        )
        self.enqueue(
            tx,
            "check_status",
            {"incident_id": inc["id"], "checks": 0},
            job["principal"],
            self.now(tx) + self.settings.status_interval,
        )

    def record_status(self, tx, incident_id, status, operation_id=None):
        inc = self.require(tx, "incident", incident_id)
        ticket = self.require(tx, "ticket", inc["ticket_id"])
        normalized = status.get("normalized_status", "UNKNOWN")
        if normalized not in {
            "NOT_SUBMITTED",
            "RECEIVED",
            "OPEN",
            "IN_PROGRESS",
            "CLOSED",
            "UNKNOWN",
        }:
            normalized = "UNKNOWN"
        raw = status.get("raw_status", normalized)
        note = status.get("closure_note", "")
        changed = normalized != ticket["normalized_status"] or note != ticket.get(
            "closure_note", ""
        )
        ticket.update(raw_status=raw, normalized_status=normalized, closure_note=note)
        if changed:
            ticket["history"].append(
                {
                    "raw_status": raw,
                    "normalized_status": normalized,
                    "closure_note": note,
                    "created_at": iso(self.now(tx)),
                }
            )
            ticket["history"] = ticket["history"][-50:]
        tx.put("ticket", ticket["id"], ticket)
        inc.update(
            agency_status=normalized,
            updated_at=iso(self.now(tx)),
            version=inc["version"] + 1,
        )
        if normalized == "CLOSED" and inc["resolution_status"] == "UNVERIFIED":
            inc.update(
                resolution_status="VERIFICATION_REQUESTED",
                next_action="The agency closed its ticket. If you can safely check, tell neighbors whether the issue is fixed.",
            )
        tx.put("incident", inc["id"], inc)
        if changed:
            self.event(
                tx,
                inc["id"],
                "AGENCY_STATUS",
                "Agency status changed to "
                + normalized
                + ". Physical resolution is tracked separately.",
                operation_id,
                tool="browser.get_status",
            )
            self.notify(
                tx,
                inc["id"],
                "The agency marked the ticket "
                + normalized.lower()
                + "."
                + (
                    " Resident verification is optional; agency closure does not confirm a repair."
                    if normalized == "CLOSED"
                    else ""
                ),
            )

    def materialize_evidence(self, evidence):
        if self.settings.mode == "local":
            return Path(evidence["path"])
        from services.agents.aws_storage import download_evidence

        return Path(
            download_evidence(
                self.settings.evidence_bucket,
                evidence["object_key"],
                evidence["sha256"],
            )
        )
