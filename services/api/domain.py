"""Shared deterministic authorization and state machine; models never authorize writes."""

import asyncio
import hashlib
import json
import math
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import fixtures
from .config import Settings
from .models import Analysis, Approval, Observation, SubmissionAttempt
from .policy import Policy
from .store import create_store

CATEGORY_TITLES = {
    "damaged_sidewalk": "Damaged sidewalk or curb ramp",
    "pothole": "Pothole",
    "walkway_obstruction": "Walkway obstruction",
}
RESERVED = {"IN_FLIGHT", "RECEIPT_CONFIRMED", "OUTCOME_UNKNOWN"}
OUTREACH_RESEARCH_SECONDS = 24 * 60 * 60
OUTREACH_CANDIDATE_SECONDS = 15 * 60
VOICE_SESSION_SECONDS = 10 * 60


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


class Domain(Policy):
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
        self.writable_incident(item)
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
        self.writable_incident(self.require(tx, "incident", incident_id))
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
        generation = (
            self.settings.data_generation if self.settings.mode == "aws" else "local"
        )
        case_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{tx.workspace_id}:{generation}:sample-pothole"
        ).hex
        if tx.get("incident", case_id):
            return case_id
        observation_id = uuid.uuid5(uuid.NAMESPACE_URL, case_id + ":observation").hex
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
            "observation_ids": [observation_id],
            "shared_public": True,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "seeded": True,
            "is_sample": True,
            "cell": list(cells(47.6154, -122.3354)),
            "next_action": "A nearby but distinct illustrative issue.",
        }
        tx.put("incident", case_id, item)
        tx.put(
            "observation",
            observation_id,
            {
                "id": observation_id,
                "owner_id": "fixture",
                "incident_id": case_id,
                "description": item["description"],
                "category": "pothole",
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "location_label": item["location_label"],
                "location_confirmed": True,
                "asset_public": "yes",
                "evidence_ids": [],
                "share_public": True,
                "share_evidence": False,
                "version": 1,
                "created_at": now,
                "updated_at": now,
                "analysis": None,
                "is_sample": True,
            },
        )
        tx.put(
            "geo:" + ":".join(map(str, item["cell"])), case_id, {"incident_id": case_id}
        )
        self.event(
            tx,
            case_id,
            "ILLUSTRATION",
            "Illustrative fixture created; no report has been sent.",
        )
        return case_id

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

    def create_observation(self, principal, data, idempotency_key=None):
        with self.authorized(principal) as tx:
            self.check_admission(tx, principal)
            request_id, request = self.request_record(
                tx, principal, "report", idempotency_key, digest(data)
            )
            if request:
                return request["response"]
            self.check_evidence(tx, data["evidence_ids"], principal)
            self.consume_quota(tx, principal, "reports")
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
            if request_id:
                tx.put(
                    "request",
                    request_id,
                    {
                        "body_hash": digest(data),
                        "status": "completed",
                        "response": item,
                    },
                )
            return item

    def patch_observation(self, principal, observation_id, data):
        with self.authorized(principal) as tx:
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
        self.check_admission(tx, principal)
        reasoning = kind in ("analyze", "decide")
        deduplicated = kind in ("analyze", "decide")
        request_id = (
            digest([principal["user"]["id"], kind, payload]) if deduplicated else None
        )
        if request_id:
            # Retain the legacy record kind so in-flight distinct-issue decisions
            # remain idempotent across deployments.
            previous = tx.get("reasoning_request", request_id)
            if previous:
                operation = tx.get("operation", previous["operation_id"])
                if operation and operation["status"] in (
                    "pending",
                    "running",
                    "completed",
                ):
                    return operation["id"]
        if reasoning:
            self.consume_quota(tx, principal, "reasoning")
        op = ident()
        job = {
            "id": op,
            "kind": kind,
            "payload": payload,
            "principal": principal,
            "generation": principal.get("generation"),
            "status": "pending",
            "due_at": due_at or self.now(tx),
            "attempts": 0,
            "lease_until": 0,
            "created_at": iso(self.now(tx)),
        }
        tx.put("job", op, job)
        tx.put("pending_job", op, {"job_id": op})
        if request_id:
            tx.put("reasoning_request", request_id, {"operation_id": op})
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
        with self.authorized(principal) as tx:
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
            if not inc or inc.get("is_sample") or inc.get("seeded"):
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
        with self.authorized(principal) as tx:
            obs = self.require(tx, "observation", observation_id)
            self.owner(obs, principal)
            if bool(incident_id) == bool(different_issue):
                raise DomainError(
                    "DECISION_REQUIRED",
                    "Choose an existing case or confirm that this is a different issue.",
                )
            payload = {
                "observation_id": observation_id,
                # None is the explicit different-issue outcome after the XOR check.
                "incident_id": incident_id,
                "version": obs["version"],
            }
            request_id = digest([principal["user"]["id"], "decide", payload])
            previous = tx.get("reasoning_request", request_id)
            if previous:
                operation = tx.get("operation", previous["operation_id"])
                if operation and operation["status"] in (
                    "pending",
                    "running",
                    "completed",
                ):
                    return operation["id"]
            if obs.get("incident_id"):
                raise DomainError(
                    "OBSERVATION_LINKED",
                    "This observation is already linked. Revise the report draft or add another observation.",
                    409,
                )
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
                payload,
                principal,
            )
        return op

    def unlink_observation(self, principal, observation_id):
        with self.authorized(principal) as tx:
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
            obs.update(
                incident_id=None,
                version=obs["version"] + 1,
                updated_at=iso(self.now(tx)),
            )
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
            "is_sample",
        )
        result = {k: incident.get(k) for k in keys}
        result.update(
            is_sample=bool(incident.get("is_sample") or incident.get("seeded")),
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

    def incident_projection(self, incident, principal, is_member):
        result = self.public_projection(incident)
        if is_member:
            result["owner_id"] = incident["owner_id"]
        if incident["owner_id"] == principal["user"]["id"]:
            result.update(
                latitude=incident["latitude"],
                longitude=incident["longitude"],
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
        with self.authorized(principal) as tx:
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
                item = self.incident_projection(inc, principal, is_member)
                item["following"] = is_following
                items.append(item)
            return {"items": items, "next_cursor": last if len(source) > 50 else None}

    def incident_detail(self, principal, incident_id):
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.accessible(tx, inc, principal)
            is_owner = inc["owner_id"] == principal["user"]["id"]
            is_member = self.member(tx, inc, principal)
            result = self.incident_projection(inc, principal, is_member)
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
            routing = inc.get("routing")
            if routing and not is_owner:
                routing = {k: v for k, v in routing.items() if k != "agent_activity"}
            result.update(
                observations=observations,
                verifications=verifications,
                events=events,
                is_owner=is_owner,
                analysis=inc.get("analysis") if is_owner else None,
                routing=routing,
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
            if is_owner:
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

    def outreach_context(self, incident):
        return digest(
            {
                "incident_id": incident["id"],
                "category": incident["category"],
                "description": incident["description"],
                "latitude": incident["latitude"],
                "longitude": incident["longitude"],
                "location_label": incident["location_label"],
            }
        )

    def outreach_eligibility(self, tx, incident):
        if incident.get("is_sample") or incident.get("seeded"):
            return False, "Illustrative samples cannot run contact research."
        observation = self.require(tx, "observation", incident["observation_ids"][0])
        if not observation.get("location_confirmed"):
            return False, "Confirm this case location before researching a contact."
        if observation.get("asset_public") != "yes":
            return (
                False,
                "Confirm this is a public street or walkway before researching a government contact.",
            )
        if not observation.get("analysis"):
            return False, "Complete the case analysis before researching a contact."
        return True, None

    @staticmethod
    def outreach_projection(record):
        if not record:
            return None
        return {
            key: value
            for key, value in record.items()
            if key
            not in (
                "workspace_id",
                "owner_id",
                "approver_id",
                "expires_epoch",
                "provider_deadline_at",
                "started_epoch",
                "playback_token_hash",
                "playback_token_hashes",
                "provider_claim_id",
                "provider_claimed_at",
                "request_id",
            )
        }

    def attach_playback_token(self, tx, principal, run):
        if run.get("owner_id") != principal["user"]["id"]:
            raise DomainError(
                "FORBIDDEN", "Only the reporting resident can play this demo.", 403
            )
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        hashes = list(run.get("playback_token_hashes", []))
        legacy = run.pop("playback_token_hash", None)
        if legacy and legacy not in hashes:
            hashes.append(legacy)
        hashes.append(token_hash)
        run["playback_token_hashes"] = hashes[-3:]
        tx.put("voice_run", run["id"], run)
        return {**self.outreach_projection(run), "playback_token": token}

    @staticmethod
    def require_playback_token(run, token):
        presented = hashlib.sha256((token or "").encode()).hexdigest()
        hashes = list(run.get("playback_token_hashes", []))
        if run.get("playback_token_hash"):
            hashes.append(run["playback_token_hash"])
        valid = any(secrets.compare_digest(presented, item) for item in hashes)
        if not token or len(token) > 128 or not valid:
            raise DomainError(
                "VOICE_PLAYBACK_CAPABILITY_REQUIRED",
                "This temporary playback capability is missing or invalid.",
                403,
            )

    def _mark_record_stale(self, tx, kind, record_id):
        record = tx.get(kind, record_id or "")
        if record and record.get("status") not in (
            "COMPLETED",
            "SIMULATED_NOT_SENT",
            "SIMULATED_NOT_DIALED",
            "ENDED",
        ):
            record["status"] = "STALE"
            tx.put(kind, record["id"], record)
            approval = tx.get("outreach_approval", record.get("approval_id", ""))
            if approval:
                approval["status"] = "STALE"
                tx.put("outreach_approval", approval["id"], approval)

    def ensure_no_active_voice_run(self, tx, incident):
        run = tx.get("voice_run", incident.get("outreach_voice_run_id", ""))
        if run and run.get("status") in ("GENERATING", "RUNNING"):
            raise DomainError(
                "VOICE_SIMULATION_ACTIVE",
                "End the current internal voice simulation before changing its research reference.",
                409,
            )

    def invalidate_outreach(self, tx, incident, *, selection=False):
        self.ensure_no_active_voice_run(tx, incident)
        self._mark_record_stale(
            tx, "outreach_email_draft", incident.get("outreach_email_draft_id")
        )
        self._mark_record_stale(
            tx, "voice_envelope", incident.get("outreach_voice_envelope_id")
        )
        if selection:
            selected = tx.get(
                "contact_selection", incident.get("outreach_contact_selection_id", "")
            )
            if selected:
                selected["status"] = "STALE"
                tx.put("contact_selection", selected["id"], selected)
            incident.pop("outreach_contact_selection_id", None)
        incident.pop("outreach_email_draft_id", None)
        incident.pop("outreach_voice_envelope_id", None)

    def scrub_contact_research(self, tx, research_id, status="STALE"):
        research = tx.get("contact_research", research_id or "")
        if not research:
            return
        research.pop("contacts", None)
        research.update(status=status, expires_epoch=int(self.now(tx)))
        tx.put("contact_research", research["id"], research)
        self._purge_contact_candidates(tx, research["id"])

    def _contact_candidates(self, tx, research):
        from . import outreach

        contacts = outreach.get_contact_candidates(
            self.settings,
            self.store,
            tx.workspace_id,
            research["id"],
        )
        return contacts if isinstance(contacts, list) else []

    def _purge_contact_candidates(self, tx, research_id):
        from . import outreach

        if not research_id:
            return
        outreach.delete_contact_candidates(
            self.settings, self.store, tx.workspace_id, research_id
        )

    def _put_contact_candidates(self, tx, research, contacts):
        from . import outreach

        outreach.put_contact_candidates(
            self.settings,
            self.store,
            tx.workspace_id,
            research["id"],
            contacts,
            int(research["expires_epoch"]),
        )

    def contact_research_projection(self, tx, research, selection=None):
        result = self.outreach_projection(research)
        contacts = []
        if research and research.get("status") == "READY":
            contacts = self._contact_candidates(tx, research)
            if not contacts and selection and selection.get("status") == "SELECTED":
                contacts = [selection["contact"]]
        result["contacts"] = contacts
        return result

    def scrub_voice_outcome(self, tx, incident, run, status):
        envelope = tx.get("voice_envelope", run.get("envelope_id", ""))
        if envelope:
            request = tx.get("outreach_request", envelope.get("request_id", ""))
            if request:
                request["status"] = "ERASED"
                tx.put("outreach_request", request["id"], request)
            envelope = {
                key: envelope[key]
                for key in (
                    "id",
                    "incident_id",
                    "owner_id",
                    "revision",
                    "payload_hash",
                    "execution_target",
                    "created_at",
                )
                if key in envelope
            }
            envelope["status"] = status
            tx.put("voice_envelope", envelope["id"], envelope)
            if incident.get("outreach_voice_envelope_id") == envelope["id"]:
                incident.pop("outreach_voice_envelope_id", None)
        run["status"] = status
        for key in (
            "playback_token",
            "playback_token_hash",
            "playback_token_hashes",
            "provider_claim_id",
            "provider_claimed_at",
            "provider_deadline_at",
            "started_epoch",
            "started_at",
            "transcript_expires_at",
            "played_turn_ids",
            "turns",
            "transcript",
            "captions",
            "audio",
            "facts",
            "research_reference",
            "selected_contact",
            "allowed_intents",
            "refusal_rules",
        ):
            run.pop(key, None)
        tx.put("voice_run", run["id"], run)
        tx.put("incident", incident["id"], incident)

    def reconcile_outreach_context(self, principal, incident_id):
        with self.authorized(principal) as tx:
            incident = self.require(tx, "incident", incident_id)
            self.owner(incident, principal)
            current_hash = self.outreach_context(incident)
            candidate = tx.get(
                "jurisdiction_candidate",
                incident.get("outreach_jurisdiction_id", ""),
            )
            research = tx.get(
                "contact_research",
                incident.get("outreach_contact_research_id", ""),
            )
            research_expired = bool(
                research
                and research.get("status") == "READY"
                and epoch(research["expires_at"]) <= self.now(tx)
            )
            stale = bool(
                (candidate and candidate.get("context_hash") != current_hash)
                or (research and research.get("context_hash") != current_hash)
                or research_expired
            )
            if not stale:
                return None
            run = tx.get("voice_run", incident.get("outreach_voice_run_id", ""))
            if run and run.get("status") in ("GENERATING", "RUNNING"):
                return run["id"]
            if candidate and candidate.get("status") not in ("STALE", "EXPIRED"):
                candidate["status"] = "STALE"
                tx.put("jurisdiction_candidate", candidate["id"], candidate)
            if research:
                self.scrub_contact_research(
                    tx,
                    research["id"],
                    "EXPIRED" if research_expired else "STALE",
                )
            self.invalidate_outreach(tx, incident, selection=True)
            tx.put("incident", incident["id"], incident)
            return None

    def reconcile_outreach_boundary(self, principal, incident_id):
        """Persist stale/expired invalidation before a protected action runs."""

        with self.authorized(principal) as tx:
            incident = self.require(tx, "incident", incident_id)
            self.owner(incident, principal)
            current_hash = self.outreach_context(incident)
            candidate = tx.get(
                "jurisdiction_candidate",
                incident.get("outreach_jurisdiction_id", ""),
            )
            research = tx.get(
                "contact_research",
                incident.get("outreach_contact_research_id", ""),
            )
            invalid = bool(
                (candidate and candidate.get("context_hash") != current_hash)
                or (research and research.get("context_hash") != current_hash)
                or (
                    research
                    and research.get("status") == "READY"
                    and epoch(research["expires_at"]) <= self.now(tx)
                )
            )
        active_run_id = self.reconcile_outreach_context(principal, incident_id)
        if active_run_id:
            self.end_voice_simulation(principal, incident_id, active_run_id, "stale")
            self.reconcile_outreach_context(principal, incident_id)
            raise DomainError(
                "STALE_OUTREACH_CONTEXT",
                "Case facts or contact research changed, so the internal simulation ended.",
                409,
            )
        if invalid:
            raise DomainError(
                "STALE_OUTREACH_CONTEXT",
                "Case facts or contact research changed. Review fresh contact results.",
                409,
            )

    def consume_outreach_quota(self, tx, principal, kind):
        now = datetime.fromtimestamp(self.now(tx), timezone.utc)
        day = now.strftime("%Y-%m-%d")
        resident_id = f"{principal['user']['id']}:{day}"
        workspace_id = f"workspace:{day}"
        limits = {
            "research": (
                self.settings.contact_research_daily_limit,
                self.settings.workspace_contact_research_daily_limit,
            ),
            "voice": (
                self.settings.voice_simulation_daily_limit,
                self.settings.workspace_voice_simulation_daily_limit,
            ),
        }[kind]
        for key, limit, scope in (
            (resident_id, limits[0], "resident"),
            (workspace_id, limits[1], "workspace"),
        ):
            row = tx.get("outreach_quota", key) or {
                "day": day,
                "research": 0,
                "voice": 0,
            }
            if row[kind] >= limit:
                subject = "Your" if scope == "resident" else "The workspace's"
                error = DomainError(
                    "OUTREACH_DAILY_LIMIT_REACHED",
                    f"{subject} daily {kind} simulation limit has been reached.",
                    429,
                    True,
                )
                error.details = {
                    "resource": kind,
                    "scope": scope,
                    "limit": limit,
                    "remaining": 0,
                    "reset_at": iso((int(now.timestamp()) // 86400 + 1) * 86400),
                }
                raise error
        for key in (resident_id, workspace_id):
            row = tx.get("outreach_quota", key) or {
                "day": day,
                "research": 0,
                "voice": 0,
            }
            row[kind] += 1
            tx.put("outreach_quota", key, row)

    def _require_outreach(self):
        if not self.settings.outreach_enabled:
            raise DomainError(
                "CONTACT_RESEARCH_DISABLED",
                "Official contact research is not enabled for this deployment.",
                503,
            )

    def begin_outreach_request(self, tx, principal, action, incident_id, key, payload):
        if not key:
            if self.settings.mode == "aws":
                raise DomainError(
                    "IDEMPOTENCY_KEY_REQUIRED",
                    "Provide a UUID Idempotency-Key for this outreach action.",
                    400,
                )
            return None, None
        try:
            normalized = str(uuid.UUID(key))
            if normalized != key.lower():
                raise ValueError()
        except (ValueError, AttributeError):
            raise DomainError(
                "IDEMPOTENCY_KEY_REQUIRED",
                "Provide a canonical UUID Idempotency-Key for this outreach action.",
                400,
            ) from None
        request_id = digest([principal["user"]["id"], action, incident_id, normalized])
        body_hash = digest(
            {"action": action, "incident_id": incident_id, "payload": payload}
        )
        current = tx.get("outreach_request", request_id)
        if current and current.get("expires_epoch", 0) <= self.now(tx):
            current = None
        if current and current["body_hash"] != body_hash:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT",
                "This request key was already used with different content.",
                409,
            )
        if current and current.get("status") == "ERASED":
            raise DomainError(
                "OUTREACH_REVISION_ERASED",
                "That private simulation revision was erased after completion.",
                409,
            )
        if current and current.get("status") == "COMPLETED":
            target = tx.get(current["target_kind"], current["target_id"])
            if target and target.get("status") != "FAILED":
                projection = (
                    self.contact_research_projection(tx, target)
                    if current["target_kind"] == "contact_research"
                    else self.outreach_projection(target)
                )
                return request_id, projection
            current["status"] = "FAILED"
            tx.put("outreach_request", request_id, current)
        if current and current.get("status") == "RESERVED":
            raise DomainError(
                "OUTREACH_REQUEST_PENDING",
                "This outreach action is still pending. Retry with the same request key.",
                409,
                True,
            )
        tx.put(
            "outreach_request",
            request_id,
            {
                "body_hash": body_hash,
                "status": "RESERVED",
                "action": action,
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "created_at": iso(self.now(tx)),
                "expires_epoch": int(self.now(tx) + 24 * 60 * 60),
            },
        )
        return request_id, None

    def finish_outreach_request(self, tx, request_id, target_kind, target_id):
        if not request_id:
            return
        request = self.require(tx, "outreach_request", request_id)
        request.update(status="COMPLETED", target_kind=target_kind, target_id=target_id)
        tx.put("outreach_request", request_id, request)

    def fail_outreach_request(self, principal, request_id):
        if not request_id:
            return
        with self.authorized(principal) as tx:
            request = tx.get("outreach_request", request_id)
            if request and request.get("status") in ("RESERVED", "COMPLETED"):
                request["status"] = "FAILED"
                tx.put("outreach_request", request_id, request)

    def jurisdiction_preview(self, principal, incident_id, idempotency_key=None):
        self._require_outreach()
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            self.ensure_no_active_voice_run(tx, inc)
            available, reason = self.outreach_eligibility(tx, inc)
            if not available:
                raise DomainError("CONTACT_RESEARCH_UNAVAILABLE", reason, 409)
            context_hash = self.outreach_context(inc)
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                "jurisdiction_preview",
                incident_id,
                idempotency_key,
                {"context_hash": context_hash},
            )
            if replay:
                return replay
            current = tx.get(
                "jurisdiction_candidate", inc.get("outreach_jurisdiction_id", "")
            )
            if (
                current
                and current.get("context_hash") == context_hash
                and current.get("status") == "PENDING"
                and epoch(current["provider_deadline_at"]) > self.now(tx)
            ):
                raise DomainError(
                    "JURISDICTION_LOOKUP_PENDING",
                    "The current jurisdiction check is still pending.",
                    409,
                    True,
                )
            if (
                current
                and current.get("context_hash") == context_hash
                and current.get("status") in ("AWAITING_CONFIRMATION", "CONFIRMED")
                and epoch(current["expires_at"]) > self.now(tx)
            ):
                self.finish_outreach_request(
                    tx, request_id, "jurisdiction_candidate", current["id"]
                )
                return self.outreach_projection(current)
            if current:
                current["status"] = "STALE"
                tx.put("jurisdiction_candidate", current["id"], current)
                self.invalidate_outreach(tx, inc, selection=True)
                self.scrub_contact_research(
                    tx, inc.get("outreach_contact_research_id"), "STALE"
                )
            now = self.now(tx)
            candidate = {
                "id": ident(),
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "context_hash": context_hash,
                "status": "PENDING",
                "created_at": iso(now),
                "expires_at": iso(now + OUTREACH_CANDIDATE_SECONDS),
                "provider_deadline_at": iso(now + 30),
            }
            inc["outreach_jurisdiction_id"] = candidate["id"]
            tx.put("jurisdiction_candidate", candidate["id"], candidate)
            tx.put("incident", incident_id, inc)
            latitude, longitude = inc["latitude"], inc["longitude"]
        from . import outreach

        try:
            proposed = outreach.reverse_geocode(self.settings, latitude, longitude)
        except outreach.OutreachProviderError as exc:
            with self.authorized(principal) as tx:
                failed = tx.get("jurisdiction_candidate", candidate["id"])
                if failed and failed.get("status") == "PENDING":
                    failed["status"] = "FAILED"
                    tx.put("jurisdiction_candidate", failed["id"], failed)
            self.fail_outreach_request(principal, request_id)
            raise DomainError(exc.code, exc.message, 503, exc.retryable) from exc
        stale = False
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            candidate = self.require(tx, "jurisdiction_candidate", candidate["id"])
            now = self.now(tx)
            if (
                self.outreach_context(inc) != context_hash
                or inc.get("outreach_jurisdiction_id") != candidate["id"]
                or candidate.get("status") != "PENDING"
            ):
                candidate["status"] = "STALE"
                tx.put("jurisdiction_candidate", candidate["id"], candidate)
                stale = True
            else:
                candidate.update(
                    proposed,
                    status="AWAITING_CONFIRMATION",
                    expires_at=iso(now + OUTREACH_CANDIDATE_SECONDS),
                )
                candidate["label"] = candidate["display_name"]
                candidate.pop("provider_deadline_at", None)
                tx.put("jurisdiction_candidate", candidate["id"], candidate)
                self.finish_outreach_request(
                    tx, request_id, "jurisdiction_candidate", candidate["id"]
                )
                self.event(
                    tx,
                    incident_id,
                    "JURISDICTION_CANDIDATE",
                    "A government-area candidate is ready for resident confirmation. No contact search occurred.",
                    tool="research.reverse_geocode",
                    result=(
                        "Seattle demo candidate"
                        if candidate["supported"]
                        else "Outside Seattle demo"
                    ),
                )
                result = self.outreach_projection(candidate)
        if stale:
            self.fail_outreach_request(principal, request_id)
            raise DomainError(
                "STALE_JURISDICTION",
                "The case changed during lookup. Review the current location.",
                409,
            )
        return result

    def contact_research(self, principal, incident_id, data, idempotency_key=None):
        self._require_outreach()
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            self.ensure_no_active_voice_run(tx, inc)
            available, reason = self.outreach_eligibility(tx, inc)
            if not available:
                raise DomainError("CONTACT_RESEARCH_UNAVAILABLE", reason, 409)
            candidate = self.require(tx, "jurisdiction_candidate", data["candidate_id"])
            if candidate.get("status") not in (
                "AWAITING_CONFIRMATION",
                "CONFIRMED",
            ):
                raise DomainError(
                    "JURISDICTION_NOT_READY",
                    "Finish the current jurisdiction check before contact research.",
                    409,
                    candidate.get("status") == "PENDING",
                )
            if (
                candidate["incident_id"] != incident_id
                or inc.get("outreach_jurisdiction_id") != candidate["id"]
                or candidate["context_hash"] != data["context_hash"]
                or self.outreach_context(inc) != data["context_hash"]
            ):
                raise DomainError(
                    "STALE_JURISDICTION",
                    "The location candidate no longer matches this case.",
                    409,
                )
            if epoch(candidate["expires_at"]) <= self.now(tx):
                raise DomainError(
                    "JURISDICTION_EXPIRED",
                    "The location candidate expired. Check the location again.",
                    409,
                )
            if not data.get("confirmed"):
                raise DomainError(
                    "JURISDICTION_CONFIRMATION_REQUIRED",
                    "Confirm the jurisdiction before contact research.",
                    409,
                )
            if not candidate.get("supported"):
                raise DomainError(
                    "UNSUPPORTED_JURISDICTION",
                    "Version one researches Seattle, Washington cases only.",
                    409,
                )
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                "contact_research",
                incident_id,
                idempotency_key,
                data,
            )
            if replay:
                return replay
            existing = tx.get(
                "contact_research", inc.get("outreach_contact_research_id", "")
            )
            if existing and existing["candidate_id"] == candidate["id"]:
                if (
                    not data.get("refresh")
                    and existing["status"] == "READY"
                    and epoch(existing["expires_at"]) > self.now(tx)
                ):
                    self.finish_outreach_request(
                        tx, request_id, "contact_research", existing["id"]
                    )
                    return self.contact_research_projection(tx, existing)
                if existing["status"] == "PENDING" and epoch(
                    existing["provider_deadline_at"]
                ) > self.now(tx):
                    self.finish_outreach_request(
                        tx, request_id, "contact_research", existing["id"]
                    )
                    return self.contact_research_projection(tx, existing)
                if existing["status"] == "PENDING":
                    existing.update(status="FAILED", provider="unavailable")
                    tx.put("contact_research", existing["id"], existing)
                    self.event(
                        tx,
                        incident_id,
                        "CONTACT_RESEARCH_FAILED",
                        "Contact research timed out. No outreach occurred.",
                    )
                elif epoch(existing["expires_at"]) <= self.now(tx):
                    self.scrub_contact_research(tx, existing["id"], "EXPIRED")
            if existing:
                self.scrub_contact_research(tx, existing["id"], "STALE")
            self.consume_outreach_quota(tx, principal, "research")
            now = self.now(tx)
            research = {
                "id": ident(),
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "candidate_id": candidate["id"],
                "context_hash": candidate["context_hash"],
                "status": "PENDING",
                "query_scope": {
                    "jurisdiction": "Seattle, WA",
                    "category": inc["category"],
                },
                "provider": "pending",
                "selected_contact_id": None,
                "created_at": iso(now),
                "expires_at": iso(now + OUTREACH_RESEARCH_SECONDS),
                "expires_epoch": int(now + OUTREACH_RESEARCH_SECONDS),
                "provider_deadline_at": iso(now + 5 * 60),
            }
            candidate["status"] = "CONFIRMED"
            inc["outreach_contact_research_id"] = research["id"]
            self.invalidate_outreach(tx, inc, selection=True)
            tx.put("jurisdiction_candidate", candidate["id"], candidate)
            tx.put("contact_research", research["id"], research)
            tx.put("incident", incident_id, inc)
            self.finish_outreach_request(
                tx, request_id, "contact_research", research["id"]
            )
            self.event(
                tx,
                incident_id,
                "JURISDICTION_CONFIRMED",
                "The reporting resident confirmed the Seattle research area. No outreach occurred.",
            )
            category = inc["category"]
            pending_result = {**self.outreach_projection(research), "contacts": []}
        from . import outreach

        if self.settings.mode == "aws":
            try:
                outreach.dispatch_outreach_worker(
                    self.settings,
                    {
                        "action": "research_contacts",
                        "workspace_id": principal["workspace_id"],
                        "research_id": research["id"],
                    },
                )
            except outreach.OutreachProviderError as exc:
                self.fail_contact_research(principal["workspace_id"], research["id"])
                self.fail_outreach_request(principal, request_id)
                raise DomainError(exc.code, exc.message, 503, exc.retryable) from exc
            return pending_result
        try:
            contacts, provider = outreach.research_contacts(self.settings, category)
        except outreach.OutreachProviderError as exc:
            self.fail_contact_research(principal["workspace_id"], research["id"])
            self.fail_outreach_request(principal, request_id)
            raise DomainError(exc.code, exc.message, 503, exc.retryable) from exc
        return self.complete_contact_research(
            principal["workspace_id"], research["id"], contacts, provider
        )

    def fail_contact_research(self, workspace_id, research_id, claimant_id=None):
        with self.store.atomic(workspace_id) as tx:
            self.check_admission(tx)
            current = tx.get("contact_research", research_id)
            if not current or current.get("status") != "PENDING":
                return (
                    self.contact_research_projection(tx, current) if current else None
                )
            if claimant_id and current.get("provider_claim_id") != claimant_id:
                return self.contact_research_projection(tx, current)
            current.update(status="FAILED", provider="unavailable")
            current.pop("contacts", None)
            current.pop("provider_claim_id", None)
            current.pop("provider_claimed_at", None)
            tx.put("contact_research", current["id"], current)
            self._purge_contact_candidates(tx, current["id"])
            self.event(
                tx,
                current["incident_id"],
                "CONTACT_RESEARCH_FAILED",
                "Official contact research failed. No outreach occurred.",
            )
            return self.contact_research_projection(tx, current)

    def complete_contact_research(
        self,
        workspace_id,
        research_id,
        contacts,
        provider,
        claimant_id=None,
    ):
        from . import outreach

        with self.store.atomic(workspace_id) as tx:
            self.check_admission(tx)
            current = self.require(tx, "contact_research", research_id)
            inc = self.require(tx, "incident", current["incident_id"])
            if current.get("status") == "READY":
                selection = tx.get(
                    "contact_selection",
                    inc.get("outreach_contact_selection_id", ""),
                )
                return self.contact_research_projection(tx, current, selection)
            if claimant_id and current.get("provider_claim_id") != claimant_id:
                raise DomainError(
                    "CONTACT_RESEARCH_CLAIM_LOST",
                    "Another worker owns this contact research operation.",
                    409,
                )
            if current.get("status") == "PENDING" and self.now(tx) >= epoch(
                current["provider_deadline_at"]
            ):
                current.update(status="FAILED", provider="unavailable")
                current.pop("contacts", None)
                tx.put("contact_research", current["id"], current)
                self._purge_contact_candidates(tx, current["id"])
                self.event(
                    tx,
                    current["incident_id"],
                    "CONTACT_RESEARCH_FAILED",
                    "Contact research finished after its deadline and was discarded. No outreach occurred.",
                )
                return self.contact_research_projection(tx, current)
            if (
                current.get("status") != "PENDING"
                or inc.get("outreach_contact_research_id") != current["id"]
                or self.outreach_context(inc) != current["context_hash"]
            ):
                if current.get("status") == "PENDING":
                    current["status"] = "STALE"
                    tx.put("contact_research", current["id"], current)
                raise DomainError(
                    "STALE_CONTACT_RESEARCH",
                    "The case changed during research. Start a fresh search.",
                    409,
                )
            retrieved = iso(self.now(tx))
            normalized = []
            for contact in contacts[:3]:
                try:
                    source_url = outreach.validate_official_url(
                        contact.get("source_url", ""),
                        self.settings.official_domain_exceptions,
                        resolve=False,
                    )
                except ValueError:
                    continue
                validated_channels = set(contact.get("validated_channels") or ())
                email = (
                    outreach.normalize_shared_email(contact.get("email") or "")
                    if "email" in validated_channels
                    else None
                )
                phone = (
                    outreach.normalize_us_phone(contact.get("phone") or "")
                    if "phone" in validated_channels
                    else None
                )
                if not email and not phone:
                    continue
                item = {
                    "agency": str(contact.get("agency") or "Government office")[:120],
                    "role": str(contact.get("role") or "Public contact")[:120],
                    "email": str(email)[:200] if email else None,
                    "phone": str(phone)[:50] if phone else None,
                    "source_title": str(
                        contact.get("source_title")
                        or "Official government contact page"
                    )[:160],
                    "source_url": source_url,
                    "source_hostname": outreach._hostname(source_url),
                    "match_reason": str(
                        contact.get("match_reason")
                        or "Validated official government source."
                    )[:300],
                    "retrieved_at": retrieved,
                }
                item["id"] = digest(
                    [
                        current["id"],
                        item["source_url"],
                        item["email"],
                        item["phone"],
                    ]
                )[:32]
                normalized.append(item)
            current.update(
                status="READY",
                contact_result_ids=[item["id"] for item in normalized],
                contact_count=len(normalized),
                provider=str(provider)[:120],
                completed_at=retrieved,
            )
            current.pop("contacts", None)
            current.pop("provider_claim_id", None)
            current.pop("provider_claimed_at", None)
            self._put_contact_candidates(tx, current, normalized)
            tx.put("contact_research", current["id"], current)
            self.event(
                tx,
                current["incident_id"],
                "CONTACT_RESEARCH_COMPLETED",
                "Official government contact research completed. No outreach occurred.",
                tool="research.official_contacts",
                result=f"{len(normalized)} validated public contact options",
            )
            return self.contact_research_projection(tx, current)

    def select_contact(self, principal, incident_id, data, idempotency_key=None):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            self.ensure_no_active_voice_run(tx, inc)
            research = self.require(tx, "contact_research", data["research_id"])
            if (
                research["incident_id"] != incident_id
                or inc.get("outreach_contact_research_id") != research["id"]
                or research["status"] != "READY"
                or self.outreach_context(inc) != research["context_hash"]
            ):
                raise DomainError(
                    "STALE_CONTACT_RESEARCH",
                    "Refresh official contact results before selecting.",
                    409,
                )
            if epoch(research["expires_at"]) <= self.now(tx):
                raise DomainError(
                    "CONTACT_RESEARCH_EXPIRED",
                    "The contact research expired. Run it again before selecting.",
                    409,
                )
            previous = tx.get(
                "contact_selection", inc.get("outreach_contact_selection_id", "")
            )
            candidates = self._contact_candidates(tx, research)
            if (
                not candidates
                and previous
                and research.get("selected_contact_id") == previous.get("contact_id")
            ):
                candidates = [previous["contact"]]
            contact = next(
                (item for item in candidates if item["id"] == data["contact_id"]),
                None,
            )
            if not contact:
                raise DomainError(
                    "CONTACT_NOT_FOUND",
                    "Select one of the current official contact results.",
                    409,
                )
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                "contact_selection",
                incident_id,
                idempotency_key,
                data,
            )
            if replay:
                self._purge_contact_candidates(tx, research["id"])
                return replay
            # The transient candidate set contains unselected public contacts. Purge it
            # before any selection or idempotency write can commit, so a failed purge
            # cannot leave a durable selection pointing at a lingering candidate set.
            self._purge_contact_candidates(tx, research["id"])
            if previous and previous["contact"]["id"] == contact["id"]:
                self.finish_outreach_request(
                    tx, request_id, "contact_selection", previous["id"]
                )
                return self.outreach_projection(previous)
            self.invalidate_outreach(tx, inc)
            now = self.now(tx)
            selection = {
                "id": ident(),
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "research_id": research["id"],
                "contact_id": contact["id"],
                "contact": contact,
                "context_hash": digest(
                    [research["context_hash"], research["id"], contact["id"]]
                ),
                "status": "SELECTED",
                "selected_at": iso(now),
            }
            research["selected_contact_id"] = contact["id"]
            research.pop("contacts", None)
            inc["outreach_contact_selection_id"] = selection["id"]
            tx.put("contact_research", research["id"], research)
            tx.put("contact_selection", selection["id"], selection)
            tx.put("incident", incident_id, inc)
            self.finish_outreach_request(
                tx, request_id, "contact_selection", selection["id"]
            )
            self.event(
                tx,
                incident_id,
                "CONTACT_SELECTED",
                "The reporting resident selected a research reference. No outreach occurred.",
            )
            result = self.outreach_projection(selection)
        return result

    def _current_selection(self, tx, inc):
        selection = self.require(
            tx, "contact_selection", inc.get("outreach_contact_selection_id", "")
        )
        if selection.get("status") != "SELECTED":
            raise DomainError(
                "CONTACT_SELECTION_REQUIRED",
                "Select a current contact research result first.",
                409,
            )
        research = self.require(tx, "contact_research", selection["research_id"])
        expired = epoch(research["expires_at"]) <= self.now(tx)
        if (
            research.get("selected_contact_id") != selection["contact"]["id"]
            or research["status"] != "READY"
            or expired
            or self.outreach_context(inc) != research["context_hash"]
        ):
            if research.get("status") == "READY":
                self.scrub_contact_research(
                    tx, research["id"], "EXPIRED" if expired else "STALE"
                )
            self.invalidate_outreach(tx, inc, selection=True)
            tx.put("incident", inc["id"], inc)
            raise DomainError(
                "STALE_CONTACT_SELECTION",
                "The selected research reference changed or expired.",
                409,
            )
        return selection

    @staticmethod
    def _frozen_outreach_payload(kind, record):
        fields = (
            (
                "revision",
                "channel",
                "subject",
                "body",
                "research_reference",
                "selected_contact",
                "research_snapshot_id",
                "execution_target",
                "context_hash",
            )
            if kind == "outreach_email_draft"
            else (
                "revision",
                "facts",
                "allowed_intents",
                "refusal_rules",
                "max_turns",
                "max_duration_seconds",
                "research_reference",
                "selected_contact",
                "research_snapshot_id",
                "execution_target",
                "context_hash",
            )
        )
        return {field: record.get(field) for field in fields}

    def _require_current_outreach_record(self, tx, inc, kind, record):
        selection = self._current_selection(tx, inc)
        frozen = self._frozen_outreach_payload(kind, record)
        if (
            record.get("research_snapshot_id") != selection["id"]
            or record.get("context_hash") != selection["context_hash"]
            or record.get("research_reference") != selection["contact"]
            or record.get("selected_contact") != selection["contact"]
            or digest(frozen) != record.get("payload_hash")
        ):
            self._mark_record_stale(tx, kind, record.get("id"))
            pointer = (
                "outreach_email_draft_id"
                if kind == "outreach_email_draft"
                else "outreach_voice_envelope_id"
            )
            if inc.get(pointer) == record.get("id"):
                inc.pop(pointer, None)
                tx.put("incident", inc["id"], inc)
            raise DomainError(
                "STALE_OUTREACH_APPROVAL",
                "The simulation preview no longer matches current approved case facts.",
                409,
            )
        return selection

    def create_email_draft(self, principal, incident_id, data, idempotency_key=None):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            selection = self._current_selection(tx, inc)
            contact = selection["contact"]
            if not contact.get("email"):
                raise DomainError(
                    "EMAIL_REFERENCE_UNAVAILABLE",
                    "The selected official source does not publish an email address.",
                    409,
                )
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                "email_draft",
                incident_id,
                idempotency_key,
                data,
            )
            if replay:
                return replay
            previous = tx.get(
                "outreach_email_draft", inc.get("outreach_email_draft_id", "")
            )
            revision = previous["revision"] + 1 if previous else 1
            subject = f"Neighborhood report: {CATEGORY_TITLES[inc['category']]} at {inc['location_label']}"
            body = (
                f"Hello {contact['role']},\n\n"
                f"A Seattle resident would like to report {CATEGORY_TITLES[inc['category']].lower()} "
                f"at {inc['location_label']}.\n\n"
                f"Resident-provided description: {inc['description']}\n\n"
                "Please advise which official reporting channel would be appropriate."
            )
            now = self.now(tx)
            frozen = {
                "revision": revision,
                "channel": "email",
                "subject": subject,
                "body": body,
                "research_reference": contact,
                "selected_contact": contact,
                "research_snapshot_id": selection["id"],
                "execution_target": "internal-email-simulator-v1",
                "context_hash": selection["context_hash"],
            }
            draft = {
                **frozen,
                "id": ident(),
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "payload_hash": digest(frozen),
                "status": "AWAITING_APPROVAL",
                "created_at": iso(now),
                "expires_at": iso(now + self.settings.approval_seconds),
            }
            self._mark_record_stale(
                tx, "outreach_email_draft", inc.get("outreach_email_draft_id")
            )
            inc["outreach_email_draft_id"] = draft["id"]
            tx.put("outreach_email_draft", draft["id"], draft)
            tx.put("incident", incident_id, inc)
            self.finish_outreach_request(
                tx, request_id, "outreach_email_draft", draft["id"]
            )
            self.event(
                tx,
                incident_id,
                "EMAIL_SIMULATION_DRAFTED",
                f"Internal email simulation revision {revision} is awaiting approval; nothing was sent.",
                tool="outreach.prepare_email_draft",
            )
            return self.outreach_projection(draft)

    def approve_email_draft(self, principal, incident_id, data, idempotency_key=None):
        return self._approve_outreach(
            principal,
            incident_id,
            "outreach_email_draft",
            "outreach_email_draft_id",
            data["draft_id"],
            data["payload_hash"],
            "simulate_email",
            idempotency_key,
        )

    def _approve_outreach(
        self,
        principal,
        incident_id,
        kind,
        pointer,
        record_id,
        payload_hash,
        action,
        idempotency_key=None,
    ):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            record = self.require(tx, kind, record_id)
            if record["incident_id"] != incident_id or inc.get(pointer) != record["id"]:
                raise DomainError(
                    "STALE_OUTREACH_APPROVAL",
                    "The simulation draft changed. Review the latest revision.",
                    409,
                )
            self._require_current_outreach_record(tx, inc, kind, record)
            if record["payload_hash"] != payload_hash:
                raise DomainError(
                    "PAYLOAD_CHANGED",
                    "The displayed simulation does not match this revision.",
                    409,
                )
            if epoch(record["expires_at"]) <= self.now(tx):
                raise DomainError(
                    "OUTREACH_APPROVAL_EXPIRED",
                    "The simulation preview expired. Prepare and review a fresh revision.",
                    409,
                )
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                action + "_approval",
                incident_id,
                idempotency_key,
                {"record_id": record_id, "payload_hash": payload_hash},
            )
            if replay:
                return replay
            if record.get("approval_id"):
                previous = self.require(tx, "outreach_approval", record["approval_id"])
                self.finish_outreach_request(
                    tx, request_id, "outreach_approval", previous["id"]
                )
                return self.outreach_projection(previous)
            approval = {
                "id": ident(),
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "approver_id": principal["user"]["id"],
                "record_id": record["id"],
                "payload_hash": payload_hash,
                "action": action,
                "status": "APPROVED",
                "created_at": iso(self.now(tx)),
                "expires_at": record["expires_at"],
            }
            record.update(status="APPROVED", approval_id=approval["id"])
            tx.put("outreach_approval", approval["id"], approval)
            tx.put(kind, record["id"], record)
            self.finish_outreach_request(
                tx, request_id, "outreach_approval", approval["id"]
            )
            self.event(
                tx,
                incident_id,
                "OUTREACH_SIMULATION_APPROVED",
                "The reporting resident approved an exact internal simulation revision. No outreach occurred.",
            )
            return self.outreach_projection(approval)

    def run_email_simulation(self, principal, incident_id, data, idempotency_key=None):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            draft = self.require(tx, "outreach_email_draft", data["draft_id"])
            self._require_current_outreach_record(
                tx, inc, "outreach_email_draft", draft
            )
            if (
                inc.get("outreach_email_draft_id") != draft["id"]
                or draft["payload_hash"] != data["payload_hash"]
                or draft.get("status") not in ("APPROVED", "SIMULATED_NOT_SENT")
            ):
                raise DomainError(
                    "EMAIL_SIMULATION_NOT_APPROVED",
                    "Approve the current exact email simulation before running it.",
                    409,
                )
            approval = self.require(
                tx, "outreach_approval", draft.get("approval_id", "")
            )
            if (
                approval["status"] != "APPROVED"
                or approval["action"] != "simulate_email"
                or epoch(approval["expires_at"]) <= self.now(tx)
            ):
                raise DomainError(
                    "EMAIL_SIMULATION_NOT_APPROVED",
                    "The email simulation approval expired or changed.",
                    409,
                )
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                "email_simulation",
                incident_id,
                idempotency_key,
                data,
            )
            if replay:
                return replay
            existing = tx.get("simulation_receipt", draft.get("receipt_id", ""))
            if existing:
                self.finish_outreach_request(
                    tx, request_id, "simulation_receipt", existing["id"]
                )
                return self.outreach_projection(existing)
            receipt_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"internal-email-simulator-v1:{approval['id']}"
            ).hex
            receipt = {
                "id": receipt_id,
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "channel": "email",
                "status": "SIMULATED_NOT_SENT",
                "execution_target": "internal-email-simulator-v1",
                "draft_id": draft["id"],
                "payload_hash": draft["payload_hash"],
                "subject": draft["subject"],
                "research_snapshot_id": draft["research_snapshot_id"],
                "created_at": iso(self.now(tx)),
                "summary": "Recorded inside Neighborhood Fixer only; no email was sent.",
            }
            draft.update(status="SIMULATED_NOT_SENT", receipt_id=receipt_id)
            inc["outreach_email_receipt_id"] = receipt_id
            tx.put("outreach_email_draft", draft["id"], draft)
            tx.put("simulation_receipt", receipt_id, receipt)
            tx.put("incident", incident_id, inc)
            self.finish_outreach_request(
                tx, request_id, "simulation_receipt", receipt_id
            )
            self.event(
                tx,
                incident_id,
                "EMAIL_SIMULATION_COMPLETED",
                "The reporting resident completed an internal email simulation. No email was sent.",
            )
            return self.outreach_projection(receipt)

    def create_voice_envelope(self, principal, incident_id, idempotency_key=None):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            self.ensure_no_active_voice_run(tx, inc)
            selection = self._current_selection(tx, inc)
            contact = selection["contact"]
            if not contact.get("phone"):
                raise DomainError(
                    "PHONE_REFERENCE_UNAVAILABLE",
                    "The selected official source does not publish a phone number.",
                    409,
                )
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                "voice_envelope",
                incident_id,
                idempotency_key,
                {},
            )
            if replay:
                return replay
            previous = tx.get(
                "voice_envelope", inc.get("outreach_voice_envelope_id", "")
            )
            revision = previous["revision"] + 1 if previous else 1
            now = self.now(tx)
            facts = [
                {
                    "id": "category",
                    "label": "Issue category",
                    "value": CATEGORY_TITLES[inc["category"]],
                },
                {
                    "id": "description",
                    "label": "Resident-provided description",
                    "value": " ".join(inc["description"].split())[:160],
                },
                {
                    "id": "location",
                    "label": "Resident-confirmed location",
                    "value": " ".join(inc["location_label"].split())[:160],
                },
                {
                    "id": "jurisdiction",
                    "label": "Confirmed research area",
                    "value": "Seattle, Washington, US",
                },
            ]
            frozen = {
                "revision": revision,
                "facts": facts,
                "allowed_intents": {
                    "reporting_agent": [
                        "report_issue",
                        "answer_location",
                        "ask_next_step",
                    ],
                    "fictional_intake_agent": [
                        "request_location",
                        "acknowledge",
                        "close",
                    ],
                },
                "refusal_rules": [
                    "Do not claim a real call, message, ticket, receipt, service promise, or government response.",
                    "Do not add facts, contact details, severity, ownership, or urgency.",
                    "The receiving role must identify itself as fictional.",
                ],
                "max_turns": 6,
                "max_duration_seconds": 90,
                "research_reference": contact,
                "selected_contact": contact,
                "research_snapshot_id": selection["id"],
                "execution_target": "internal-voice-simulator-v1",
                "context_hash": selection["context_hash"],
            }
            envelope = {
                **frozen,
                "id": ident(),
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "payload_hash": digest(frozen),
                "status": "AWAITING_APPROVAL",
                "variability_notice": "Wording may vary only inside this approved fact and intent envelope.",
                "created_at": iso(now),
                "expires_at": iso(now + self.settings.approval_seconds),
                "request_id": request_id,
            }
            self._mark_record_stale(
                tx, "voice_envelope", inc.get("outreach_voice_envelope_id")
            )
            inc["outreach_voice_envelope_id"] = envelope["id"]
            tx.put("voice_envelope", envelope["id"], envelope)
            tx.put("incident", incident_id, inc)
            self.finish_outreach_request(
                tx, request_id, "voice_envelope", envelope["id"]
            )
            self.event(
                tx,
                incident_id,
                "VOICE_SIMULATION_DRAFTED",
                "A bounded internal voice simulation is awaiting approval; no number was dialed.",
                tool="outreach.prepare_voice_envelope",
            )
            return self.outreach_projection(envelope)

    def approve_voice_envelope(
        self, principal, incident_id, data, idempotency_key=None
    ):
        return self._approve_outreach(
            principal,
            incident_id,
            "voice_envelope",
            "outreach_voice_envelope_id",
            data["envelope_id"],
            data["payload_hash"],
            "simulate_voice",
            idempotency_key,
        )

    def validate_voice_script(self, envelope, result):
        turns = result.get("turns") if isinstance(result, dict) else None
        expected = [
            ("reporting_agent", "report_issue"),
            ("fictional_intake_agent", "request_location"),
            ("reporting_agent", "answer_location"),
            ("fictional_intake_agent", "acknowledge"),
            ("reporting_agent", "ask_next_step"),
            ("fictional_intake_agent", "close"),
        ]
        if not isinstance(turns, list) or len(turns) != envelope["max_turns"]:
            raise DomainError(
                "VOICE_SCRIPT_INVALID",
                "The voice agents did not produce the approved bounded dialogue.",
                503,
                True,
            )
        facts = {item["id"]: item["value"] for item in envelope["facts"]}
        fact_rules = {
            "report_issue": {"category", "description"},
            "request_location": set(),
            "answer_location": {"location", "jurisdiction"},
            "acknowledge": set(),
            "ask_next_step": set(),
            "close": set(),
        }
        variants = {
            "report_issue": {
                "report_standard": "This internal demo reports {category}. The resident described: {description}",
                "report_concise": "Resident report for this internal demo: {category}. Description: {description}",
            },
            "request_location": {
                "ask_location_standard": "I am a fictional demo intake agent. What location was approved for this simulation?",
                "ask_location_brief": "Fictional demo intake agent here. Please provide the approved location.",
            },
            "answer_location": {
                "location_standard": "The resident-confirmed location is {location}. The confirmed research area is {jurisdiction}.",
                "location_concise": "Approved location: {location}. Confirmed research area: {jurisdiction}.",
            },
            "acknowledge": {
                "acknowledge_standard": "This fictional intake simulation recorded those resident-provided details without contacting an office.",
                "acknowledge_brief": "The fictional simulation recorded those details internally only.",
            },
            "ask_next_step": {
                "next_step_standard": "What would the next step be in this fictional demonstration?",
                "next_step_brief": "What is the fictional demonstration's next step?",
            },
            "close": {
                "close_standard": "This internal demonstration is complete. No phone number was dialed and no government response is claimed.",
                "close_brief": "The internal demo is complete. No number was dialed.",
            },
        }
        total = 0
        safe = []
        for turn, required in zip(turns, expected, strict=True):
            if not isinstance(turn, dict) or set(turn) != {
                "speaker",
                "intent",
                "fact_ids",
                "variant_id",
            }:
                raise DomainError(
                    "VOICE_SCRIPT_INVALID", "Invalid voice turn.", 503, True
                )
            speaker, intent = turn.get("speaker"), turn.get("intent")
            fact_ids, variant_id = turn.get("fact_ids"), turn.get("variant_id")
            if (speaker, intent) != required:
                raise DomainError(
                    "VOICE_SCRIPT_INVALID", "Invalid voice turn order.", 503, True
                )
            if not isinstance(fact_ids, list) or set(fact_ids) != fact_rules[intent]:
                raise DomainError(
                    "VOICE_SCRIPT_INVALID",
                    "Voice turn exceeded approved facts.",
                    503,
                    True,
                )
            template = variants[intent].get(variant_id)
            if not template:
                raise DomainError(
                    "VOICE_SCRIPT_INVALID",
                    "Voice agents selected wording outside the approved catalog.",
                    503,
                    True,
                )
            caption = template.format(**facts)
            if not 1 <= len(caption) <= 300:
                raise DomainError(
                    "VOICE_SCRIPT_INVALID",
                    "Rendered voice caption is too long.",
                    503,
                    True,
                )
            total += len(caption)
            safe.append(
                {
                    "id": ident(),
                    "speaker": speaker,
                    "intent": intent,
                    "fact_ids": fact_ids,
                    "variant_id": variant_id,
                    "caption": caption,
                }
            )
        if total > 1800:
            raise DomainError(
                "VOICE_SCRIPT_INVALID", "Voice script exceeded its limit.", 503, True
            )
        return safe

    def _voice_run_projection(self, principal, run):
        result = self.outreach_projection(run)
        if (
            run.get("status") == "RUNNING"
            and run.get("started_epoch")
            and time.time() - run["started_epoch"] >= 90
        ):
            self.end_voice_simulation(
                principal, run["incident_id"], run["id"], "expired"
            )
            result["status"] = "INTERRUPTED"
        return result

    def voice_run_status(self, principal, incident_id, run_id, playback_token):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            run = self.require(tx, "voice_run", run_id)
            if run["incident_id"] != incident_id:
                raise DomainError("NOT_FOUND", "Voice simulation not found.", 404)
            self.require_playback_token(run, playback_token)
            result = self.outreach_projection(run)
            expired = (
                run.get("status") == "RUNNING"
                and run.get("started_epoch")
                and time.time() - run["started_epoch"] >= 90
            )
        if expired:
            self.end_voice_simulation(principal, incident_id, run_id, "expired")
            return {**result, "status": "INTERRUPTED"}
        if run.get("status") != "RUNNING":
            return result

        from . import outreach

        session = outreach.get_voice_session(
            self.settings,
            self.store,
            principal["workspace_id"],
            run["id"],
        )
        if not session:
            self.end_voice_simulation(principal, incident_id, run_id, "expired")
            return {**result, "status": "INTERRUPTED"}
        result["turns"] = [
            {
                **turn,
                "audio_url": (
                    f"/api/incidents/{run['incident_id']}/outreach/voice/"
                    f"runs/{run['id']}/turns/{turn['id']}/audio"
                ),
            }
            for turn in session["turns"]
        ]
        return result

    def run_voice_simulation(self, principal, incident_id, data, idempotency_key=None):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            envelope = self.require(tx, "voice_envelope", data["envelope_id"])
            self._require_current_outreach_record(tx, inc, "voice_envelope", envelope)
            if (
                inc.get("outreach_voice_envelope_id") != envelope["id"]
                or envelope["payload_hash"] != data["payload_hash"]
                or envelope.get("status")
                not in (
                    "APPROVED",
                    "GENERATING",
                    "RUNNING",
                    "COMPLETED",
                    "ENDED",
                    "FAILED",
                )
            ):
                raise DomainError(
                    "VOICE_SIMULATION_NOT_APPROVED",
                    "Approve the current voice envelope before starting it.",
                    409,
                )
            approval = self.require(
                tx, "outreach_approval", envelope.get("approval_id", "")
            )
            if (
                approval["status"] != "APPROVED"
                or approval["action"] != "simulate_voice"
                or epoch(approval["expires_at"]) <= self.now(tx)
            ):
                raise DomainError(
                    "VOICE_SIMULATION_NOT_APPROVED",
                    "The voice simulation approval expired or changed.",
                    409,
                )
            request_id, replay = self.begin_outreach_request(
                tx,
                principal,
                "voice_simulation",
                incident_id,
                idempotency_key,
                data,
            )
            if replay:
                replay_run = tx.get("voice_run", replay.get("id", ""))
                if replay_run and replay_run.get("status") in (
                    "GENERATING",
                    "RUNNING",
                ):
                    return self.attach_playback_token(tx, principal, replay_run)
                return replay
            existing = tx.get("voice_run", envelope.get("run_id", ""))
            if existing and existing.get("status") == "FAILED":
                envelope.update(status="APPROVED")
                envelope.pop("run_id", None)
                tx.put("voice_envelope", envelope["id"], envelope)
                existing = None
            if existing:
                self.finish_outreach_request(
                    tx, request_id, "voice_run", existing["id"]
                )
                if existing.get("status") in ("GENERATING", "RUNNING"):
                    return self.attach_playback_token(tx, principal, existing)
                return self._voice_run_projection(principal, existing)
            self.consume_outreach_quota(tx, principal, "voice")
            run = {
                "id": ident(),
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "envelope_id": envelope["id"],
                "payload_hash": envelope["payload_hash"],
                "research_snapshot_id": envelope["research_snapshot_id"],
                "status": "GENERATING",
                "execution_target": "internal-voice-simulator-v1",
                "created_at": iso(self.now(tx)),
                "provider_deadline_at": iso(self.now(tx) + 5 * 60),
            }
            envelope.update(status="GENERATING", run_id=run["id"])
            inc["outreach_voice_run_id"] = run["id"]
            tx.put("voice_envelope", envelope["id"], envelope)
            tx.put("voice_run", run["id"], run)
            tx.put("incident", incident_id, inc)
            self.finish_outreach_request(tx, request_id, "voice_run", run["id"])
            initial_response = self.attach_playback_token(tx, principal, run)
            private_envelope = dict(envelope)
        from . import outreach

        private_envelope.update(
            workspace_id=principal["workspace_id"], owner_id=principal["user"]["id"]
        )
        if self.settings.mode == "aws":
            try:
                outreach.dispatch_outreach_worker(
                    self.settings,
                    {
                        "action": "simulate_voice",
                        "workspace_id": principal["workspace_id"],
                        "envelope_id": envelope["id"],
                        "payload_hash": envelope["payload_hash"],
                    },
                )
            except outreach.OutreachProviderError as exc:
                self.fail_voice_generation(principal["workspace_id"], run["id"])
                self.fail_outreach_request(principal, request_id)
                raise DomainError(exc.code, exc.message, 503, exc.retryable) from exc
            return initial_response
        turns = None
        error = None
        for _ in range(2):
            try:
                proposal = outreach.generate_voice_script(
                    self.settings, private_envelope, principal
                )
                turns = self.validate_voice_script(private_envelope, proposal)
                break
            except Exception as exc:  # noqa: BLE001 - one bounded regeneration.
                error = exc
        if turns is None:
            self.fail_voice_generation(principal["workspace_id"], run["id"])
            self.fail_outreach_request(principal, request_id)
            if isinstance(error, DomainError):
                raise error
            raise DomainError(
                "VOICE_SIMULATION_FAILED",
                "The internal voice simulation could not be prepared. No number was dialed.",
                503,
                True,
            ) from error
        try:
            current = self.complete_voice_generation(
                principal["workspace_id"], run["id"], turns
            )
        except Exception:  # noqa: BLE001 - fail closed and purge transient turns.
            self.fail_voice_generation(principal["workspace_id"], run["id"])
            self.fail_outreach_request(principal, request_id)
            raise
        return {
            **self._voice_run_projection(principal, current),
            "playback_token": initial_response["playback_token"],
        }

    def fail_voice_generation(self, workspace_id, run_id, claimant_id=None):
        from . import outreach

        projected = None
        may_purge = claimant_id is None
        try:
            with self.store.atomic(workspace_id) as tx:
                self.check_admission(tx)
                current = tx.get("voice_run", run_id)
                if not current:
                    return None
                if claimant_id and current.get("provider_claim_id") != claimant_id:
                    return self.outreach_projection(current)
                may_purge = True
                if current.get("status") == "GENERATING":
                    incident = self.require(tx, "incident", current["incident_id"])
                    self.scrub_voice_outcome(tx, incident, current, "FAILED")
                    self.event(
                        tx,
                        current["incident_id"],
                        "VOICE_SIMULATION_FAILED",
                        "The internal voice simulation failed before playback. No number was dialed.",
                    )
                projected = self.outreach_projection(current)
        finally:
            if may_purge:
                outreach.delete_voice_session(
                    self.settings, self.store, workspace_id, run_id
                )
        return projected

    def complete_voice_generation(self, workspace_id, run_id, turns, claimant_id=None):
        from . import outreach

        with self.store.atomic(workspace_id) as tx:
            self.check_admission(tx)
            current = self.require(tx, "voice_run", run_id)
            if current.get("status") == "RUNNING":
                return self.outreach_projection(current)
            if claimant_id and current.get("provider_claim_id") != claimant_id:
                raise DomainError(
                    "VOICE_PROVIDER_CLAIM_LOST",
                    "Another worker owns this voice generation operation.",
                    409,
                )
            if current.get("status") != "GENERATING":
                raise DomainError(
                    "STALE_VOICE_SIMULATION",
                    "This voice generation is no longer current.",
                    409,
                )
            envelope = self.require(tx, "voice_envelope", current["envelope_id"])
            incident = self.require(tx, "incident", current["incident_id"])
            if (
                envelope.get("status") != "GENERATING"
                or incident.get("outreach_voice_run_id") != current["id"]
                or incident.get("outreach_voice_envelope_id") != envelope["id"]
            ):
                raise DomainError(
                    "STALE_VOICE_SIMULATION",
                    "This voice generation is no longer current.",
                    409,
                )
            if self.now(tx) >= epoch(current["provider_deadline_at"]):
                raise DomainError(
                    "VOICE_GENERATION_EXPIRED",
                    "The voice generation deadline passed before captions were stored.",
                    409,
                )
            owner_id = current["owner_id"]
        expires_epoch = time.time() + VOICE_SESSION_SECONDS
        expires_at = iso(expires_epoch)
        if self.settings.mode == "aws":
            try:
                from services.agents.aws_workflow import start_voice_cleanup

                start_voice_cleanup(workspace_id, run_id, expires_at)
            except Exception as exc:
                raise DomainError(
                    "VOICE_CLEANUP_SCHEDULING_FAILED",
                    "The temporary voice cleanup could not be scheduled, so no captions were stored.",
                    503,
                    True,
                ) from exc
        outreach.put_voice_session(
            self.settings,
            self.store,
            workspace_id,
            run_id,
            {
                "run_id": run_id,
                "incident_id": current["incident_id"],
                "owner_id": owner_id,
                "turns": turns,
                "expires_epoch": expires_epoch,
                "ttl_epoch": time.time() + 15 * 60,
            },
        )
        stale = False
        projected = None
        with self.store.atomic(workspace_id) as tx:
            self.check_admission(tx)
            current = self.require(tx, "voice_run", run_id)
            if (
                current.get("status") != "GENERATING"
                or (claimant_id and current.get("provider_claim_id") != claimant_id)
                or self.now(tx) >= epoch(current["provider_deadline_at"])
            ):
                stale = True
            else:
                current.update(
                    status="RUNNING",
                    ready_at=iso(self.now(tx)),
                    transcript_expires_at=iso(expires_epoch),
                    turn_count=len(turns),
                )
                current.pop("provider_claim_id", None)
                current.pop("provider_claimed_at", None)
                current_envelope = self.require(
                    tx, "voice_envelope", current["envelope_id"]
                )
                current_envelope["status"] = "RUNNING"
                tx.put("voice_run", current["id"], current)
                tx.put("voice_envelope", current_envelope["id"], current_envelope)
                self.event(
                    tx,
                    current["incident_id"],
                    "VOICE_SIMULATION_STARTED",
                    "An internal voice simulation started. No phone number was dialed.",
                    tool="agent.simulate_voice",
                    result="Validated bounded internal dialogue; captions were not recorded in activity",
                )
                projected = self.outreach_projection(current)
        if stale:
            outreach.delete_voice_session(
                self.settings, self.store, workspace_id, run_id
            )
            raise DomainError(
                "STALE_VOICE_SIMULATION",
                "This voice generation is no longer current.",
                409,
            )
        return projected

    def voice_turn_audio(self, principal, incident_id, run_id, turn_id, playback_token):
        self._require_outreach()
        self.reconcile_outreach_boundary(principal, incident_id)
        expired = False
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            run = self.require(tx, "voice_run", run_id)
            if run["incident_id"] != incident_id or run["status"] != "RUNNING":
                raise DomainError(
                    "VOICE_SESSION_UNAVAILABLE",
                    "This temporary voice session is no longer available.",
                    410,
                )
            self.require_playback_token(run, playback_token)
            if not run.get("started_epoch"):
                run.update(started_epoch=time.time(), started_at=iso(self.now(tx)))
                tx.put("voice_run", run["id"], run)
            expired = time.time() - run["started_epoch"] >= 90
        if expired:
            self.end_voice_simulation(principal, incident_id, run_id, "expired")
            raise DomainError(
                "VOICE_SESSION_EXPIRED",
                "The approved demo call reached its 90-second limit.",
                410,
            )
        from . import outreach

        session = outreach.get_voice_session(
            self.settings, self.store, principal["workspace_id"], run_id
        )
        turn = next(
            (
                item
                for item in (session or {}).get("turns", [])
                if item["id"] == turn_id
            ),
            None,
        )
        if not turn:
            raise DomainError(
                "VOICE_SESSION_UNAVAILABLE",
                "This temporary voice turn expired and was not retained.",
                410,
            )
        try:
            outreach.claim_voice_audio(
                self.settings,
                self.store,
                principal["workspace_id"],
                run_id,
                turn_id,
                int(session["expires_epoch"]),
            )
            audio_stream, media_type = outreach.synthesize_audio_stream(
                self.settings, turn["caption"], turn["speaker"]
            )
        except outreach.OutreachProviderError as exc:
            status = 429 if exc.code == "VOICE_AUDIO_LIMIT_REACHED" else 503
            raise DomainError(exc.code, exc.message, status, exc.retryable) from exc

        def stream_and_mark_served():
            completed = False
            deadline_reached = False
            source = iter(audio_stream)
            deadline = run["started_epoch"] + 90
            try:
                while True:
                    if time.time() >= deadline:
                        deadline_reached = True
                        break
                    try:
                        chunk = next(source)
                    except StopIteration:
                        completed = True
                        break
                    if time.time() >= deadline:
                        deadline_reached = True
                        break
                    yield chunk
            finally:
                try:
                    close = getattr(source, "close", None)
                    if callable(close):
                        close()
                finally:
                    if deadline_reached:
                        self.end_voice_simulation(
                            principal, incident_id, run_id, "expired"
                        )
                if completed and not deadline_reached:
                    with self.authorized(principal) as tx:
                        current = tx.get("voice_run", run_id)
                        if current and current.get("status") == "RUNNING":
                            try:
                                self.require_playback_token(current, playback_token)
                            except DomainError:
                                return
                            played = list(current.get("played_turn_ids", []))
                            if turn_id not in played:
                                played.append(turn_id)
                                current["played_turn_ids"] = played[:6]
                                tx.put("voice_run", current["id"], current)

        return stream_and_mark_served(), media_type

    def end_voice_simulation(
        self,
        principal,
        incident_id,
        run_id,
        reason,
        idempotency_key=None,
    ):
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            run = self.require(tx, "voice_run", run_id)
            if run["incident_id"] != incident_id:
                raise DomainError("NOT_FOUND", "Voice simulation not found.", 404)
            request_id, replay = (None, None)
            if reason not in ("expired", "stale"):
                request_id, replay = self.begin_outreach_request(
                    tx,
                    principal,
                    "voice_simulation_end",
                    incident_id,
                    idempotency_key,
                    {"run_id": run_id, "reason": reason},
                )
                if replay:
                    self.scrub_voice_outcome(
                        tx, inc, run, replay.get("run_status", run["status"])
                    )
                    from . import outreach

                    outreach.delete_voice_session(
                        self.settings,
                        self.store,
                        principal["workspace_id"],
                        run_id,
                    )
                    return replay
            existing = tx.get("simulation_receipt", run.get("receipt_id", ""))
            if existing:
                self.scrub_voice_outcome(
                    tx, inc, run, existing.get("run_status", run["status"])
                )
                self.finish_outreach_request(
                    tx, request_id, "simulation_receipt", existing["id"]
                )
                from . import outreach

                outreach.delete_voice_session(
                    self.settings,
                    self.store,
                    principal["workspace_id"],
                    run_id,
                )
                return self.outreach_projection(existing)
            now = self.now(tx)
            elapsed = (
                max(0, time.time() - run["started_epoch"])
                if run.get("started_epoch")
                else 0
            )
            if elapsed >= 90:
                reason = "expired"
            completed = bool(
                reason == "completed"
                and run.get("status") == "RUNNING"
                and run.get("started_epoch")
                and run.get("turn_count", 0) > 0
                and len(set(run.get("played_turn_ids", []))) >= run.get("turn_count", 0)
            )
            if reason == "completed" and not completed:
                reason = "resident"
            run_status = {
                "completed": "COMPLETED",
                "resident": "ENDED",
                "expired": "INTERRUPTED",
                "stale": "INTERRUPTED",
            }[reason]
            receipt = {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL, f"internal-voice-simulator-v1:{run['id']}"
                ).hex,
                "incident_id": incident_id,
                "owner_id": principal["user"]["id"],
                "channel": "voice",
                "status": "SIMULATED_NOT_DIALED",
                "run_status": run_status,
                "execution_target": "internal-voice-simulator-v1",
                "run_id": run["id"],
                "envelope_id": run["envelope_id"],
                "payload_hash": run["payload_hash"],
                "turn_count": run.get("turn_count", 0),
                "duration_seconds": max(0, min(90, round(elapsed))),
                "research_snapshot_id": run["research_snapshot_id"],
                "created_at": iso(now),
                "summary": "Internal simulation completed; no phone number was dialed. Audio and captions were temporary and were not saved."
                if run_status == "COMPLETED"
                else (
                    "Stopped by resident; no phone number was dialed. Audio and captions were not saved."
                    if run_status == "ENDED"
                    else (
                        "Case facts changed, so the internal demo stopped; no phone number was dialed. Audio and captions were not saved."
                        if reason == "stale"
                        else "The internal demo reached its time limit; no phone number was dialed. Audio and captions were not saved."
                    )
                ),
            }
            run.update(status=run_status, ended_at=iso(now), receipt_id=receipt["id"])
            inc["outreach_voice_receipt_id"] = receipt["id"]
            self.scrub_voice_outcome(tx, inc, run, run_status)
            tx.put("simulation_receipt", receipt["id"], receipt)
            self.finish_outreach_request(
                tx, request_id, "simulation_receipt", receipt["id"]
            )
            self.event(
                tx,
                incident_id,
                "VOICE_SIMULATION_COMPLETED"
                if run_status == "COMPLETED"
                else "VOICE_SIMULATION_ENDED",
                (
                    "The reporting resident completed an internal call simulation. No phone number was dialed."
                    if run_status == "COMPLETED"
                    else (
                        "Case facts changed, so the internal call simulation ended. No phone number was dialed."
                        if reason == "stale"
                        else "The internal call simulation ended. No phone number was dialed."
                    )
                ),
            )
        from . import outreach

        outreach.delete_voice_session(
            self.settings, self.store, principal["workspace_id"], run_id
        )
        return self.outreach_projection(receipt)

    def expire_voice_run(self, workspace_id, run_id):
        """Finalize a scheduled transient-session purge without user admission."""

        with self.store.atomic(workspace_id) as tx:
            run = tx.get("voice_run", run_id)
            if not run:
                return None
            existing = tx.get("simulation_receipt", run.get("receipt_id", ""))
            if existing:
                return self.outreach_projection(existing)
            if run.get("status") not in ("GENERATING", "RUNNING"):
                return self.outreach_projection(run)
            incident = tx.get("incident", run.get("incident_id", ""))
            if not incident:
                return None
            now = self.now(tx)
            receipt = {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"internal-voice-simulator-v1:{run['id']}",
                ).hex,
                "incident_id": run["incident_id"],
                "owner_id": run["owner_id"],
                "channel": "voice",
                "status": "SIMULATED_NOT_DIALED",
                "run_status": "INTERRUPTED",
                "execution_target": "internal-voice-simulator-v1",
                "run_id": run["id"],
                "envelope_id": run["envelope_id"],
                "payload_hash": run["payload_hash"],
                "turn_count": run.get("turn_count", 0),
                "duration_seconds": 0,
                "research_snapshot_id": run["research_snapshot_id"],
                "created_at": iso(now),
                "summary": "The temporary internal demo expired; no phone number was dialed. Audio and captions were not saved.",
            }
            run.update(
                status="INTERRUPTED", ended_at=iso(now), receipt_id=receipt["id"]
            )
            incident["outreach_voice_receipt_id"] = receipt["id"]
            self.scrub_voice_outcome(tx, incident, run, "INTERRUPTED")
            tx.put("simulation_receipt", receipt["id"], receipt)
            self.event(
                tx,
                run["incident_id"],
                "VOICE_SIMULATION_ENDED",
                "The temporary internal call simulation expired. No phone number was dialed.",
            )
            return self.outreach_projection(receipt)

    def outreach_snapshot(self, principal, incident_id):
        active_run_id = self.reconcile_outreach_context(principal, incident_id)
        if active_run_id:
            self.end_voice_simulation(principal, incident_id, active_run_id, "stale")
            self.reconcile_outreach_context(principal, incident_id)
        timed_out_run_id = None
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            available, reason = self.outreach_eligibility(tx, inc)
            provider_ready = self.settings.mode == "local" or bool(
                self.settings.outreach_enabled
                and self.settings.contact_research_function
            )
            if available and not provider_ready:
                available = False
                reason = (
                    "Official contact research is not configured for this deployment."
                )
            result = {
                "available": available,
                "disabled_reason": reason,
                "voice_available": available
                and (
                    self.settings.mode == "local"
                    or bool(
                        self.settings.voice_transcripts_table
                        and self.settings.outreach_provider_function
                    )
                ),
            }
            current_research = tx.get(
                "contact_research", inc.get("outreach_contact_research_id", "")
            )
            if current_research:
                if current_research.get("status") == "PENDING" and epoch(
                    current_research["provider_deadline_at"]
                ) <= self.now(tx):
                    current_research.update(status="FAILED", provider="unavailable")
                    tx.put("contact_research", current_research["id"], current_research)
                    self.event(
                        tx,
                        incident_id,
                        "CONTACT_RESEARCH_FAILED",
                        "Contact research timed out. No outreach occurred.",
                    )
                elif current_research.get("status") == "READY" and epoch(
                    current_research["expires_at"]
                ) <= self.now(tx):
                    self.scrub_contact_research(tx, current_research["id"], "EXPIRED")
            current_run = tx.get("voice_run", inc.get("outreach_voice_run_id", ""))
            if (
                current_run
                and current_run.get("status") == "GENERATING"
                and epoch(current_run["provider_deadline_at"]) <= self.now(tx)
            ):
                timed_out_run_id = current_run["id"]
                self.scrub_voice_outcome(tx, inc, current_run, "FAILED")
                self.event(
                    tx,
                    incident_id,
                    "VOICE_SIMULATION_FAILED",
                    "Voice generation timed out before playback. No number was dialed.",
                )
            pointers = {
                "jurisdiction": ("jurisdiction_candidate", "outreach_jurisdiction_id"),
                "research": ("contact_research", "outreach_contact_research_id"),
                "selection": ("contact_selection", "outreach_contact_selection_id"),
                "email_draft": ("outreach_email_draft", "outreach_email_draft_id"),
                "email_receipt": ("simulation_receipt", "outreach_email_receipt_id"),
                "voice_envelope": ("voice_envelope", "outreach_voice_envelope_id"),
                "voice_run": ("voice_run", "outreach_voice_run_id"),
                "voice_receipt": ("simulation_receipt", "outreach_voice_receipt_id"),
            }
            current_selection = tx.get(
                "contact_selection",
                inc.get("outreach_contact_selection_id", ""),
            )
            voice_run = None
            for name, (kind, pointer) in pointers.items():
                record = tx.get(kind, inc.get(pointer, ""))
                if (
                    name == "jurisdiction"
                    and record
                    and (
                        record.get("status") in ("PENDING", "FAILED")
                        or not all(
                            field in record
                            for field in (
                                "display_name",
                                "locality",
                                "municipality",
                                "region",
                                "country_code",
                                "label",
                                "supported",
                                "provider",
                            )
                        )
                    )
                ):
                    record = None
                if name == "research" and record:
                    result[name] = self.contact_research_projection(
                        tx, record, current_selection
                    )
                else:
                    result[name] = self.outreach_projection(record)
                if name == "voice_run":
                    voice_run = record
        if timed_out_run_id:
            from . import outreach

            outreach.delete_voice_session(
                self.settings,
                self.store,
                principal["workspace_id"],
                timed_out_run_id,
            )
        if voice_run:
            result["voice_run"] = self._voice_run_projection(principal, voice_run)
        return result

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
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.owner(inc, principal)
            return self.draft_projection(self.make_draft(tx, inc, principal, data))

    def approve(self, principal, incident_id, data):
        with self.authorized(principal) as tx:
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
        with self.authorized(principal) as tx:
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
        with self.authorized(principal) as tx:
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
        with self.authorized(principal) as tx:
            inc = self.require(tx, "incident", incident_id)
            self.writable_incident(inc)
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
            self.check_admission(tx)
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
                self.check_admission(tx, job["principal"])
                if (
                    self.settings.mode == "aws"
                    and job.get("generation") != self.settings.data_generation
                ):
                    raise DomainError(
                        "STALE_GENERATION",
                        "This job belongs to an earlier demo generation.",
                        409,
                    )
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
            with self.job_transaction(workspace_id, job, settlement=True) as tx:
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
            with self.job_transaction(workspace_id, job, settlement=True) as tx:
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
            with self.job_transaction(workspace_id, job) as tx:
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
            # The service owns the complete list of resident facts required to
            # proceed. Model-suggested questions can preserve uncertainty, but
            # cannot create an extra blocking gate (for example, asking the
            # resident to confirm a duplicate before the duplicate decision UI).
            model_only_questions = [
                question
                for question in analysis["missing_information"]
                if question not in required
            ]
            analysis["unknowns"] = list(
                dict.fromkeys(analysis["unknowns"] + model_only_questions)
            )
            analysis["missing_information"] = required
            with self.job_transaction(workspace_id, job) as tx:
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
            with self.job_transaction(workspace_id, job) as tx:
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
            with self.job_transaction(workspace_id, job):
                pass
            proposal = (
                await asyncio.to_thread(self.engine().prepare, obs, routing, p)
                if routing and routing.get("supported")
                else None
            )
            with self.job_transaction(workspace_id, job) as tx:
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

            with self.job_transaction(workspace_id, job) as tx:
                attempt = self.require(tx, "attempt", payload["attempt_id"])
            receipt = await lookup_receipt(attempt["id"])
            if receipt:
                with self.job_transaction(workspace_id, job) as tx:
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

            with self.job_transaction(workspace_id, job) as tx:
                inc = self.require(tx, "incident", payload["incident_id"])
                ticket = self.require(tx, "ticket", inc["ticket_id"])
            status = await get_ticket_status(ticket["receipt_id"])
            with self.job_transaction(workspace_id, job) as tx:
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
            FailedBeforeSubmission,
            HandoffRequired,
            OutcomeUnknown,
            submit_report,
        )

        with self.job_transaction(workspace_id, job) as tx:
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
            with self.job_transaction(workspace_id, job) as tx:
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
            with self.job_transaction(workspace_id, job) as tx:
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
            with self.job_transaction(workspace_id, job) as tx:
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
            with self.job_transaction(workspace_id, job) as tx:
                self.mark_unknown(
                    tx,
                    attempt,
                    job["id"],
                    "An unexpected browser interruption left the outcome uncertain. No automatic retry was sent.",
                )
            return {"incident_id": inc["id"], "submission_status": "OUTCOME_UNKNOWN"}
        with self.job_transaction(workspace_id, job, settlement=True) as tx:
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
        try:
            self.check_admission(tx, job["principal"])
        except DomainError:
            # Persist a known receipt while maintenance drains existing workers;
            # no fresh status action is admitted. Reset must await that drain.
            return
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
