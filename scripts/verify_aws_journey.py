"""Bounded, explicitly gated real-AWS demonstration with two provisioned residents.

No account provisioning, model fixture fallback, virtual clock, report retries,
or municipal submissions. Configuration containing bearer tokens stays private.
The output is a redacted evidence report, never a dump of HTTP/provider responses.
Use --stop-before-approval with --confirm to exercise analyses, shared-case linking,
draft integrity and privacy, then preserve the case without approving or submitting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.set_demo_ticket_status import OperatorError, operate


class JourneyError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def load_config(path: Path) -> dict:
    try:
        if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise JourneyError(
                "PRIVATE_CONFIG_REQUIRED",
                "The explicit token configuration file must be a regular private file with mode 0600.",
            )
        config = json.loads(path.read_text())
    except JourneyError:
        raise
    except Exception:  # noqa: BLE001 - keep credentials and provider payloads out of reports
        raise JourneyError(
            "CONFIG_UNAVAILABLE", "Unable to read the private JSON configuration."
        ) from None
    if not isinstance(config, dict):
        raise JourneyError(
            "CONFIG_INVALID", "The private configuration must be a JSON object."
        )
    required = ("api_base", "region", "stack_name", "expected_workspace_id")
    if any(not isinstance(config.get(key), str) or not config[key] for key in required):
        raise JourneyError(
            "CONFIG_INCOMPLETE",
            "Private configuration needs api_base, region, stack_name, expected_workspace_id, and two private session files or bearer tokens.",
        )
    parsed = urlsplit(config["api_base"])
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(
            f".execute-api.{config['region']}.amazonaws.com"
        )
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise JourneyError(
            "DESTINATION_REJECTED",
            "Use the deployed stack's HTTPS API Gateway URL in its configured region.",
        )
    subjects = []
    for who in ("a", "b"):
        session_path = config.get("session_" + who + "_file")
        if session_path:
            if config.get("token_" + who):
                raise JourneyError(
                    "CREDENTIAL_SOURCE_AMBIGUOUS",
                    "Use either a private session file or an inline bearer token for each resident, not both.",
                )
            session_path = Path(session_path)
            if not session_path.is_absolute():
                session_path = path.parent / session_path
            try:
                if (
                    not session_path.is_file()
                    or stat.S_IMODE(session_path.stat().st_mode) & 0o077
                ):
                    raise ValueError("Session file must be private")
                session = json.loads(session_path.read_text())
                expires = datetime.fromisoformat(session["expires_at"])
                if expires.tzinfo is None or expires <= datetime.now(UTC):
                    raise ValueError("Session expired")
                if (
                    session.get("schema_version") != 1
                    or session.get("api_origin") != f"{parsed.scheme}://{parsed.netloc}"
                    or session.get("workspace_id") != config["expected_workspace_id"]
                    or not session.get("user_id")
                ):
                    raise ValueError("Session scope mismatch")
                config["token_" + who] = session["access_token"]
                subjects.append(session["user_id"])
            except Exception:  # noqa: BLE001 - keep credentials and provider payloads out of reports
                raise JourneyError(
                    "SESSION_FILE_INVALID",
                    "A provided private session file is expired, invalid, or scoped to a different API/workspace.",
                ) from None
        if (
            not isinstance(config.get("token_" + who), str)
            or not config["token_" + who]
        ):
            raise JourneyError(
                "BEARER_TOKEN_MISSING",
                "Each resident needs a current private session file or bearer token.",
            )
    if len(subjects) == 2 and subjects[0] == subjects[1]:
        raise JourneyError(
            "DISTINCT_RESIDENTS_REQUIRED",
            "The private session files must identify different Cognito residents.",
        )
    if config["token_a"] == config["token_b"]:
        raise JourneyError(
            "DISTINCT_RESIDENTS_REQUIRED",
            "Provide separate current bearer tokens for the two provisioned residents.",
        )
    return config


def approved_hash(draft: dict) -> str:
    keys = (
        "recipient",
        "category",
        "description",
        "location_label",
        "latitude",
        "longitude",
        "contact",
        "attachment_ids",
        "attachment_hashes",
    )
    try:
        payload = {key: draft[key] for key in keys}
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    except Exception:  # noqa: BLE001 - keep credentials and provider payloads out of reports
        raise JourneyError(
            "DRAFT_INVALID",
            "The persisted report draft does not contain a valid frozen payload.",
        ) from None


class Journey:
    def __init__(
        self,
        config,
        report_path,
        *,
        clients=None,
        operator=operate,
        portal_client=None,
        operation_seconds=300,
        total_seconds=900,
        sleeper=time.sleep,
        stop_before_approval=False,
    ):
        self.config, self.report_path, self.operator = (
            config,
            Path(report_path),
            operator,
        )
        self.operation_seconds = min(max(operation_seconds, 1), 300)
        self.deadline = time.monotonic() + min(max(total_seconds, 1), 900)
        self.sleep = sleeper
        self.stop_before_approval = stop_before_approval
        self.owns_clients = clients is None
        self.clients = clients or {
            who: httpx.Client(
                base_url=config["api_base"].rstrip("/"),
                headers={"Authorization": "Bearer " + config["token_" + who]},
                timeout=30,
                follow_redirects=False,
            )
            for who in ("a", "b")
        }
        self.portal_client = portal_client or httpx.Client(
            timeout=15, follow_redirects=False
        )
        self.owns_portal_client = portal_client is None
        self.report = {
            "run_id": uuid.uuid4().hex,
            "kind": "real_aws_fictional_agency_journey",
            "status": "running",
            "phase": "preflight",
            "checks": {},
            "operation_ids": [],
            "write_requests": [],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model_fixture_fallback": False,
        }

    def close(self):
        if self.owns_clients:
            for client in self.clients.values():
                client.close()
        if self.owns_portal_client:
            self.portal_client.close()

    def save(self):
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.report_path.with_name(
            self.report_path.name + "." + self.report["run_id"] + ".pending"
        )
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(self.report, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, self.report_path)

    def phase(self, name):
        self.report["phase"] = name
        self.save()
        print(json.dumps({"phase": name}), flush=True)

    def remaining(self):
        if time.monotonic() >= self.deadline:
            raise JourneyError(
                "JOURNEY_TIMEOUT",
                "The bounded verification window ended. Inspect the saved operation and case; do not automatically repeat a submission.",
            )

    def request(self, who, method, path, *, expected=(200,), **kwargs):
        self.remaining()
        if not path.startswith("/api/"):
            raise JourneyError(
                "PATH_REJECTED",
                "Only the fixed application API paths may receive resident tokens.",
            )
        attempts = 3 if method == "GET" else 1
        if method != "GET":
            self.report["write_requests"].append(
                {
                    "method": method,
                    "action": path.rsplit("/", 1)[-1],
                    "resident": who,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            self.save()
        for attempt in range(attempts):
            try:
                response = self.clients[who].request(method, path, **kwargs)
            except httpx.TransportError:
                if attempt + 1 < attempts:
                    self.sleep(min(2**attempt, 4))
                    continue
                code = (
                    "READ_UNAVAILABLE"
                    if method == "GET"
                    else "WRITE_RESPONSE_UNCERTAIN"
                )
                raise JourneyError(
                    code,
                    "The API response was unavailable. Writes are never automatically repeated; inspect the saved case and operation before continuing.",
                ) from None
            if (
                response.status_code in (429, 502, 503, 504)
                and method == "GET"
                and attempt + 1 < attempts
            ):
                self.sleep(min(2**attempt, 4))
                continue
            if response.status_code not in expected:
                raise JourneyError(
                    "API_REQUEST_REJECTED",
                    f"The API rejected a {method} request during {self.report['phase']} (HTTP {response.status_code}). Review the saved phase; response bodies are not logged.",
                )
            try:
                return response.json()
            except Exception:  # noqa: BLE001 - keep credentials and provider payloads out of reports
                raise JourneyError(
                    "API_RESPONSE_INVALID",
                    "The API returned an invalid structured response. No write was retried.",
                ) from None

    def operation(self, who, operation_id):
        self.report["operation_ids"].append(operation_id)
        self.save()
        until = min(self.deadline, time.monotonic() + self.operation_seconds)
        delay = 1
        while time.monotonic() < until:
            result = self.request(who, "GET", "/api/operations/" + operation_id)
            if result.get("status") == "completed":
                return result.get("result") or {}
            if result.get("status") == "failed":
                raise JourneyError(
                    "PERSISTED_OPERATION_FAILED",
                    "A persisted AWS operation failed. Review that operation; fixture fallback and automatic model retries are disabled.",
                )
            self.sleep(delay)
            delay = min(delay * 1.5, 8)
        raise JourneyError(
            "OPERATION_TIMEOUT",
            "The operation exceeded the verification polling limit. It remains persisted; inspect it before starting any new work.",
        )

    def upload(self, who, filename):
        path = ROOT / "fixtures" / "images" / filename
        if not path.is_file():
            raise JourneyError(
                "FIXTURE_ASSET_MISSING",
                "An original illustrative fixture image is missing; no substitute is generated.",
            )
        with path.open("rb") as photo:
            return self.request(
                who,
                "POST",
                "/api/uploads",
                expected=(201,),
                files={"file": (filename, photo, "image/png")},
            )

    def analyze_observation(self, who, evidence, description):
        observation = self.request(
            who,
            "POST",
            "/api/observations",
            expected=(201,),
            json={
                "description": description,
                "category": "damaged_sidewalk",
                "latitude": 47.615,
                "longitude": -122.335,
                "location_label": "Maple & Alder · fictional Demo Borough",
                "location_confirmed": True,
                "asset_public": "yes",
                "evidence_ids": [evidence["id"]],
                "share_public": True,
                "share_evidence": False,
            },
        )
        self.report["observation_" + who] = observation["id"]
        self.save()
        operation = self.request(
            who,
            "POST",
            "/api/observations/" + observation["id"] + "/analyze",
            expected=(202,),
        )
        result = self.operation(who, operation["operation_id"])
        analysis = result.get("analysis", {})
        if (
            "Amazon Bedrock" not in analysis.get("provenance", "")
            or "Strands" not in analysis.get("provenance", "")
            or not analysis.get("agent_activity")
        ):
            raise JourneyError(
                "REAL_MODEL_EVIDENCE_MISSING",
                "The result lacks actual Bedrock/Strands provenance and tool activity. No fixture result is accepted.",
            )
        if not analysis.get("unknowns"):
            raise JourneyError(
                "ANALYSIS_UNCERTAINTY_MISSING",
                "The analyst did not retain uncertainty about the illustrative image. Review the observation before continuing.",
            )
        if analysis.get("missing_information"):
            raise JourneyError(
                "RESIDENT_CLARIFICATION_REQUIRED",
                "The analyst requested clarification. Review the persisted observation; the verification script will not invent facts.",
            )
        self.report["checks"]["real_analysis_" + who] = {
            "provenance_verified": True,
            "tool_count": len(analysis["agent_activity"]),
            "unknown_count": len(analysis.get("unknowns", [])),
        }
        self.save()
        return observation, result

    def run(self):
        if self.report_path.exists():
            self.close()
            raise JourneyError(
                "EXISTING_REPORT_REQUIRES_REVIEW",
                "This verification report already exists. Review its saved case before choosing a fresh output file; no existing run is automatically replayed.",
            )
        try:
            self.phase("preflight")
            health = self.request("a", "GET", "/api/health")
            if health.get("mode") != "aws" or health.get("status") != "ok":
                raise JourneyError(
                    "AWS_NOT_READY",
                    "The API is not reporting a configured AWS backend.",
                )
            expected_providers = {
                "model_provider": "Amazon Bedrock via Strands",
                "agent_runtime": "AgentCore Runtime",
                "browser_provider": "AgentCore Browser",
                "storage": "DynamoDB + private S3",
            }
            if health.get("missing_configuration") or any(
                health.get("integrations", {}).get(key, {}).get("provider") != provider
                or health.get("integrations", {}).get(key, {}).get("status")
                != "configured"
                for key, provider in expected_providers.items()
            ):
                raise JourneyError(
                    "AWS_INTEGRATIONS_NOT_CONFIGURED",
                    "The API must report configured Bedrock, AgentCore Runtime, AgentCore Browser, and private AWS storage before this journey.",
                )
            sessions = {
                who: self.request(who, "GET", "/api/session") for who in ("a", "b")
            }
            if (
                any(
                    s.get("workspace_id") != self.config["expected_workspace_id"]
                    or s.get("mode") != "aws"
                    for s in sessions.values()
                )
                or sessions["a"]["user"]["id"] == sessions["b"]["user"]["id"]
            ):
                raise JourneyError(
                    "WORKSPACE_MEMBERSHIP_MISMATCH",
                    "The tokens must identify different Cognito residents in the explicitly provisioned shared workspace.",
                )
            # Public preflight uses a separate client: no resident token is ever
            # forwarded to the fictional portal or Secrets Manager.
            preview = self.operator(
                stack_name=self.config["stack_name"],
                region=self.config["region"],
                receipt_id="DB-000000000000",
                status="CLOSED",
                confirm=False,
            )
            response = self.portal_client.get(preview["portal"] + "/health")
            status_health = response.json() if response.status_code == 200 else {}
            if (
                status_health.get("mode") != "aws"
                or status_health.get("environment") != "demo"
                or status_health.get("destination") != "fictional"
                or status_health.get("status_management_enabled") is not True
            ):
                raise JourneyError(
                    "PORTAL_STATUS_CONTROL_DISABLED",
                    "Enable the deployed fictional portal's explicit demo status control before starting this journey.",
                )
            existing = self.request("a", "GET", "/api/incidents")
            if existing.get("next_cursor") or any(
                c.get("category") == "damaged_sidewalk"
                and abs(c.get("latitude", 0) - 47.615) < 0.001
                and abs(c.get("longitude", 0) + 122.335) < 0.001
                for c in existing.get("items", [])
            ):
                raise JourneyError(
                    "EXISTING_CASE_REQUIRES_REVIEW",
                    "The workspace already contains a nearby case or more than one page of cases. Review existing work instead of automatically creating another report.",
                )
            self.report["checks"]["shared_cognito_workspace"] = True
            self.report["checks"]["aws_and_fictional_portal_preflight"] = True
            self.phase("analyze_resident_a")
            before = self.upload("a", "curb-before.png")
            first, _ = self.analyze_observation(
                "a",
                before,
                "Illustrative synthetic demo: broken curb ramp edge beside the yellow tactile surface. The location is a fictional public crossing; dimensions and safety are unverified.",
            )
            self.phase("route_and_prepare_report")
            decision = self.request(
                "a",
                "POST",
                "/api/observations/" + first["id"] + "/decision",
                expected=(202,),
                json={"different_issue": True},
            )
            incident_id = self.operation("a", decision["operation_id"])["incident_id"]
            self.report["incident_id"] = incident_id
            self.save()
            case = self.request("a", "GET", "/api/incidents/" + incident_id)
            draft = case.get("draft")
            if case.get("submission_status") != "AWAITING_APPROVAL" or not draft:
                raise JourneyError(
                    "DRAFT_NOT_READY",
                    "Routing did not produce an approvable report. Review the preserved case; no approval was sent.",
                )
            if (
                draft.get("recipient") != "Demo Borough Public Works"
                or draft.get("category") != "damaged_sidewalk"
                or draft.get("latitude") != 47.615
                or draft.get("longitude") != -122.335
                or any(draft.get("contact", {}).values())
                or draft.get("attachment_ids") != [before["id"]]
                or draft.get("attachment_hashes") != [before["sha256"]]
                or approved_hash(draft) != draft.get("payload_hash")
            ):
                raise JourneyError(
                    "DRAFT_INTEGRITY_REJECTED",
                    "The frozen draft changed its configured recipient, category, coordinates, contacts, or selected attachment. Review it before any approval.",
                )
            self.report["approved_payload_hash"] = draft["payload_hash"]
            self.request("b", "GET", "/api/evidence/" + before["id"], expected=(403,))
            neighbor_case = self.request("b", "GET", "/api/incidents/" + incident_id)
            if neighbor_case.get("draft") is not None or any(
                obs.get("evidence") for obs in neighbor_case.get("observations", [])
            ):
                raise JourneyError(
                    "PRIVATE_CASE_DATA_EXPOSED",
                    "The neighboring resident can access an unshared photo or private report draft. Stop before approval and review access controls.",
                )
            self.report["checks"]["neighbor_cannot_read_private_photo_or_draft"] = True
            self.phase("analyze_and_link_resident_b")
            second, analysis = self.analyze_observation(
                "b",
                self.upload("b", "curb-before.png"),
                "Illustrative synthetic demo: I observed the same broken curb ramp edge beside the yellow tactile surface at this fictional crossing; stroller passage appears difficult, but safety and dimensions are unverified.",
            )
            if incident_id not in [
                c.get("id") for c in analysis.get("duplicate_candidates", [])
            ]:
                raise JourneyError(
                    "CANONICAL_CANDIDATE_MISSING",
                    "The second observation did not identify the existing case. No new incident or second submission is created automatically.",
                )
            linked = self.request(
                "b",
                "POST",
                "/api/observations/" + second["id"] + "/decision",
                expected=(202,),
                json={"incident_id": incident_id, "different_issue": False},
            )
            if (
                self.operation("b", linked["operation_id"]).get("incident_id")
                != incident_id
            ):
                raise JourneyError(
                    "LINK_FAILED",
                    "The second resident's observation did not link to the canonical case.",
                )
            current = self.request("a", "GET", "/api/incidents/" + incident_id)
            if (
                current.get("observation_count") != 2
                or current.get("draft", {}).get("payload_hash") != draft["payload_hash"]
            ):
                raise JourneyError(
                    "DRAFT_CHANGED_AFTER_LINK",
                    "The draft changed while linking evidence. A new review is required; no submission was sent.",
                )
            self.report["checks"]["two_observations_one_immutable_draft"] = True
            if self.stop_before_approval:
                if current.get(
                    "submission_status"
                ) != "AWAITING_APPROVAL" or current.get("ticket"):
                    raise JourneyError(
                        "CASE_NO_LONGER_AWAITING_APPROVAL",
                        "The case is no longer awaiting its first approval. Review the existing case; no approval was sent by this run.",
                    )
                # The hash describes a reviewed draft, not resident authorization.
                self.report.pop("approved_payload_hash", None)
                self.report["checks"]["stopped_before_approval"] = {
                    "approval_requests_sent": 0,
                    "browser_submission_attempted": False,
                    "agency_closure_requested": False,
                }
                self.report.update(
                    status="paused_before_approval",
                    phase="paused_before_approval",
                    paused_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    draft_id=draft["id"],
                    draft_revision=draft["revision"],
                    draft_payload_hash=draft["payload_hash"],
                    resume_guidance="Review the saved existing case and draft before any further action. This driver does not resume or replay paused runs; do not choose a new report path to bypass existing-case review.",
                )
                self.save()
                return self.report
            self.phase("approve_exact_fictional_submission_once")
            approval = self.request(
                "a",
                "POST",
                "/api/incidents/" + incident_id + "/approve",
                expected=(202,),
                json={
                    "draft_id": draft["id"],
                    "payload_hash": draft["payload_hash"],
                    "publish_consent": True,
                    "share_evidence": False,
                },
            )
            self.report["submission_operation_id"] = approval["operation_id"]
            self.save()
            outcome = self.operation("a", approval["operation_id"])
            if outcome.get("submission_status") != "RECEIPT_CONFIRMED":
                raise JourneyError(
                    "SUBMISSION_REVIEW_REQUIRED",
                    "The submission did not return a confirmed receipt. Do not approve or submit again; inspect and reconcile the existing attempt if appropriate.",
                )
            case = self.request("a", "GET", "/api/incidents/" + incident_id)
            ticket = case.get("ticket") or {}
            receipt_id = ticket.get("receipt_id", "")
            if not re.fullmatch(r"DB-[A-F0-9]{12}", receipt_id) or not ticket.get(
                "steps"
            ):
                raise JourneyError(
                    "BROWSER_EVIDENCE_MISSING",
                    "The case lacks a verified fictional receipt or executed browser step evidence.",
                )
            self.report["receipt_id"] = receipt_id
            self.report["checks"]["browser_submission"] = {
                "receipt_confirmed": True,
                "step_count": len(ticket["steps"]),
                "private_screenshot_available": bool(ticket.get("screenshot_url")),
                "approval_requests_sent": 1,
            }
            self.phase("close_fictional_agency_ticket")
            self.operator(
                stack_name=self.config["stack_name"],
                region=self.config["region"],
                receipt_id=receipt_id,
                status="CLOSED",
                closure_note="Fictional demo: agency ticket closed as duplicate. Physical repair has not been verified.",
                confirm=True,
            )
            until = min(self.deadline, time.monotonic() + 180)
            while time.monotonic() < until:
                case = self.request("a", "GET", "/api/incidents/" + incident_id)
                if case.get("agency_status") == "CLOSED":
                    break
                self.sleep(4)
            if case.get("agency_status") != "CLOSED":
                raise JourneyError(
                    "STATUS_READ_TIMEOUT",
                    "The durable workflow has not yet observed the persisted agency closure. No virtual clock or direct application mutation was used.",
                )
            if case.get("resolution_status") == "RESIDENT_CONFIRMED_FIXED":
                raise JourneyError(
                    "CLOSURE_INCORRECTLY_RESOLVED",
                    "Agency closure incorrectly changed physical resolution without resident verification.",
                )
            self.report["checks"]["closure_does_not_mean_fixed"] = {
                "agency_status": case["agency_status"],
                "resolution_status": case["resolution_status"],
            }
            self.phase("resident_verifies_after_photo")
            after = self.upload("b", "curb-after.png")
            self.request(
                "b",
                "POST",
                "/api/incidents/" + incident_id + "/verify",
                json={
                    "choice": "looks_fixed",
                    "evidence_id": after["id"],
                    "note": "Illustrative synthetic after-photo for the fictional demo. Resident-confirmed appearance only; not a safety certification.",
                    "share_public": True,
                },
            )
            for who in ("a", "b"):
                case = self.request(who, "GET", "/api/incidents/" + incident_id)
                notifications = self.request(who, "GET", "/api/notifications")
                if (
                    case.get("resolution_status") != "RESIDENT_CONFIRMED_FIXED"
                    or case.get("observation_count") != 2
                    or not case.get("following")
                    or not any(
                        n.get("incident_id") == incident_id
                        for n in notifications.get("items", [])
                    )
                ):
                    raise JourneyError(
                        "FOLLOWER_OUTCOME_MISSING",
                        "One resident cannot see the shared persisted outcome or notifications.",
                    )
            self.report["checks"]["both_followers_see_resident_verification"] = True
            self.report.update(
                status="passed",
                phase="complete",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self.save()
            return self.report
        except (JourneyError, OperatorError) as exc:
            self.report.update(
                status="needs_review",
                error={
                    "code": exc.code
                    if isinstance(exc, JourneyError)
                    else "OPERATOR_CONTROL_FAILED",
                    "message": str(exc),
                },
            )
            self.save()
            raise
        except Exception:  # noqa: BLE001 - keep credentials and provider payloads out of reports
            error = JourneyError(
                "UNEXPECTED_FAILURE",
                "Verification stopped unexpectedly. Inspect the saved phase and existing case; no write was automatically retried.",
            )
            self.report.update(
                status="needs_review",
                error={"code": error.code, "message": error.message},
            )
            self.save()
            raise error from None
        finally:
            self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a bounded actual-AWS, two-resident fictional case journey. Tokens remain in an explicit private config file."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--report", type=Path, default=ROOT / ".local" / "aws-journey-report.json"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Authorize the selected bounded billable journey: draft-only with --stop-before-approval, otherwise the full fictional ticket lifecycle",
    )
    parser.add_argument(
        "--stop-before-approval",
        action="store_true",
        help="Run two real analyses, canonical linking, draft and privacy checks, then save paused_before_approval without approving or submitting",
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if not args.confirm:
            actions = [
                "Upload original synthetic fixture photos",
                "Run two real Strands analyses",
                "Create one canonical case and link the second observation",
            ]
            if args.stop_before_approval:
                actions += [
                    "Validate the frozen draft and neighbor privacy boundaries",
                    "Stop before approval or browser submission and preserve the existing case for review",
                ]
            else:
                actions += [
                    "Validate and approve one frozen fictional report",
                    "Capture the actual browser receipt",
                    "Close the fictional ticket through the enabled operator control",
                    "Verify the illustrative repair and both follower outcomes",
                ]
            print(
                json.dumps(
                    {
                        "status": "preview",
                        "api_base": config["api_base"],
                        "actions": actions,
                        "stop_before_approval": args.stop_before_approval,
                        "automatic_write_retries": False,
                        "requires": "Rerun with --confirm after reviewing this bounded test scope.",
                    },
                    indent=2,
                )
            )
            return
        report = Journey(
            config, args.report, stop_before_approval=args.stop_before_approval
        ).run()
        print(
            json.dumps(
                {"status": report["status"], "report": str(args.report.resolve())}
            )
        )
    except (JourneyError, OperatorError) as exc:
        print(
            json.dumps(
                {
                    "status": "needs_review",
                    "code": getattr(exc, "code", "OPERATOR_CONTROL_FAILED"),
                    "message": str(exc),
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
