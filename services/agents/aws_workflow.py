"""Step Functions bridge over the same Domain commands used by the local worker.

No HTTP endpoint accepts a task token. The private callback record is correlated
with the exact draft, and approval is committed before wake_approval is called.
The submission job always performs the existing reservation/cancellation checks.
"""

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone


def _client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "stepfunctions",
        config=Config(retries={"total_max_attempts": 2, "mode": "standard"}),
    )


def _retry_concurrent(operation, attempts=3):
    """Retry only optimistic workspace conflicts, never provider work."""

    from services.agents.aws_storage import ConcurrentUpdate

    for attempt in range(attempts):
        try:
            return operation()
        except ConcurrentUpdate:
            if attempt + 1 == attempts:
                raise
    raise AssertionError("unreachable")


def start_operation(workspace_id, operation_id):
    arn = os.environ.get("NF_STATE_MACHINE_ARN")
    if not arn:
        raise RuntimeError(
            "NF_STATE_MACHINE_ARN is required; cloud execution is unavailable"
        )
    try:
        return _client().start_execution(
            stateMachineArn=arn,
            name=operation_id,
            input=json.dumps(
                {"workspace_id": workspace_id, "operation_id": operation_id}
            ),
        )["executionArn"]
    except Exception as exc:
        if (
            getattr(exc, "response", {}).get("Error", {}).get("Code")
            == "ExecutionAlreadyExists"
        ):
            return None  # Operation identifiers never change on duplicate dispatch.
        raise


def _voice_cleanup_expiry(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("Invalid voice-cleanup expiry") from exc
    if stamp.tzinfo is None:
        raise ValueError("Invalid voice-cleanup expiry")
    return stamp.astimezone(timezone.utc)


def start_voice_cleanup(workspace_id, run_id, expires_at):
    """Schedule one durable, idempotent purge before transient turns are stored."""

    arn = os.environ.get("NF_STATE_MACHINE_ARN")
    if not arn:
        raise RuntimeError(
            "NF_STATE_MACHINE_ARN is required; voice cleanup is unavailable"
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id or ""):
        raise ValueError("Invalid voice-cleanup workspace")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id or ""):
        raise ValueError("Invalid voice-cleanup run")
    expiry = _voice_cleanup_expiry(expires_at)
    if not 0 < expiry.timestamp() - time.time() <= 11 * 60:
        raise ValueError("Voice-cleanup expiry is outside its bounded window")
    try:
        return _client().start_execution(
            stateMachineArn=arn,
            name=f"voice-cleanup-{run_id}",
            input=json.dumps(
                {
                    "phase": "voice_cleanup_wait",
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "expires_at": expires_at,
                },
                separators=(",", ":"),
            ),
        )["executionArn"]
    except Exception as exc:
        if (
            getattr(exc, "response", {}).get("Error", {}).get("Code")
            == "ExecutionAlreadyExists"
        ):
            return None
        raise


def _decision(tx, incident_id, draft_id):
    incident = tx.get("incident", incident_id)
    draft = tx.get("draft", draft_id)
    if not incident or not draft or draft.get("incident_id") != incident_id:
        return "invalid"
    if incident.get("draft_id") != draft_id:
        return "stale"
    if incident.get("submission_status") == "CANCELLED":
        return "cancelled"
    attempt = tx.get("attempt", incident.get("attempt_id", ""))
    if attempt and attempt.get("draft_id") == draft_id:
        approval = tx.get("approval", attempt["approval_id"])
        if (
            approval
            and approval["draft_id"] == draft_id
            and approval["payload_hash"] == draft["payload_hash"]
        ):
            return "approved"
    return "pending"


def wake_approval(workspace_id, incident_id, draft_id, domain=None):
    from services.api.domain import Domain

    domain = domain or Domain()
    with domain.store.atomic(workspace_id) as tx:
        domain.check_admission(tx)
        record = tx.get("callback", draft_id)
        decision = _decision(tx, incident_id, draft_id)
        if not record or record.get("consumed") or decision == "pending":
            return {"woken": False}
        if record["incident_id"] != incident_id or record["draft_id"] != draft_id:
            raise PermissionError("Callback correlation mismatch")
        token = record["task_token"]
    try:
        if decision == "approved":
            _client().send_task_success(
                taskToken=token,
                output=json.dumps({"draft_id": draft_id, "decision": "approved"}),
            )
        else:
            _client().send_task_failure(
                taskToken=token, error="RevisionNoLongerApprovable", cause=decision
            )
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code not in ("TaskDoesNotExist", "TaskTimedOut", "InvalidToken"):
            raise
    with domain.store.atomic(workspace_id) as tx:
        domain.check_admission(tx)
        current = tx.get("callback", draft_id)
        if current and current.get("task_token") == token:
            # Retain audit correlation, erase the sensitive token after consumption.
            current.update(consumed=True, decision=decision)
            current.pop("task_token", None)
            tx.put("callback", draft_id, current)
    return {"woken": True}


def research_handler(event, context):
    from services.api.domain import Domain, DomainError, epoch

    if set(event) != {
        "phase",
        "action",
        "workspace_id",
        "research_id",
    } or (event.get("phase"), event.get("action")) != (
        "outreach_provider",
        "research_contacts",
    ):
        raise ValueError("Unexpected contact-research provider input")
    domain = Domain()
    workspace_id = event["workspace_id"]
    claimant_id = getattr(context, "aws_request_id", None) or uuid.uuid4().hex
    try:
        domain._require_outreach()
    except DomainError:
        _retry_concurrent(
            lambda: domain.fail_contact_research(workspace_id, event["research_id"])
        )
        return {"status": "FAILED"}

    def claim_provider():
        with domain.store.atomic(workspace_id) as tx:
            domain.check_admission(tx)
            research = domain.require(tx, "contact_research", event["research_id"])
            if research.get("status") in (
                "READY",
                "FAILED",
                "STALE",
                "EXPIRED",
            ):
                return {"claimed": False, "status": research["status"]}
            expired = domain.now(tx) >= epoch(research["provider_deadline_at"])
            incident = domain.require(tx, "incident", research["incident_id"])
            if (
                research.get("status") != "PENDING"
                or incident.get("outreach_contact_research_id") != research["id"]
                or research.get("query_scope")
                != {
                    "jurisdiction": "Seattle, WA",
                    "category": incident["category"],
                }
                or domain.outreach_context(incident) != research.get("context_hash")
            ):
                raise PermissionError("Contact research is stale or unauthorized")
            if expired:
                return {
                    "claimed": False,
                    "status": "FAILED",
                    "expired": True,
                    "research_id": research["id"],
                }
            if research.get("provider_claim_id"):
                return {"claimed": False, "status": "PENDING"}
            research.update(
                provider_claim_id=claimant_id,
                provider_claimed_at=domain.now(tx),
            )
            tx.put("contact_research", research["id"], research)
            return {
                "claimed": True,
                "status": "PENDING",
                "research_id": research["id"],
                "category": incident["category"],
            }

    claim = _retry_concurrent(claim_provider)
    if claim.get("expired"):
        _retry_concurrent(
            lambda: domain.fail_contact_research(workspace_id, claim["research_id"])
        )
        return {"status": "FAILED"}
    if not claim["claimed"]:
        return {"status": claim["status"]}
    from services.api.outreach import research_contacts_aws

    try:
        contacts, provider = research_contacts_aws(domain.settings, claim["category"])
        result = _retry_concurrent(
            lambda: domain.complete_contact_research(
                workspace_id,
                claim["research_id"],
                contacts,
                provider,
                claimant_id,
            )
        )
        return {"status": result["status"]}
    # Fail closed without logging provider content or contact data.
    except Exception:  # noqa: BLE001
        _retry_concurrent(
            lambda: domain.fail_contact_research(
                workspace_id, claim["research_id"], claimant_id
            )
        )
        return {"status": "FAILED"}


def handler(event, context):
    from services.api.domain import Domain, DomainError, epoch

    domain = Domain()
    phase = event.get("phase", "run")
    if phase == "voice_cleanup":
        if set(event) != {"phase", "workspace_id", "run_id", "expires_at"}:
            raise ValueError("Unexpected voice-cleanup input")
        workspace_id, run_id = event["workspace_id"], event["run_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id or ""):
            raise ValueError("Invalid voice-cleanup workspace")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id or ""):
            raise ValueError("Invalid voice-cleanup run")
        _voice_cleanup_expiry(event["expires_at"])
        from services.api import outreach

        try:
            domain.expire_voice_run(workspace_id, run_id)
        finally:
            outreach.delete_voice_session(
                domain.settings, domain.store, workspace_id, run_id
            )
        return {"status": "PURGED"}
    workspace_id = event["workspace_id"]
    if phase == "outreach_provider":
        action = event.get("action")
        if action == "research_contacts":
            raise ValueError("Contact research requires the isolated research worker")
        if action == "simulate_voice":
            if set(event) != {
                "phase",
                "action",
                "workspace_id",
                "envelope_id",
                "payload_hash",
            }:
                raise ValueError("Unexpected voice provider input")
            claimant_id = getattr(context, "aws_request_id", None) or uuid.uuid4().hex

            def claim_provider():
                with domain.store.atomic(workspace_id) as tx:
                    domain.check_admission(tx)
                    envelope = domain.require(
                        tx, "voice_envelope", event["envelope_id"]
                    )
                    incident = domain.require(tx, "incident", envelope["incident_id"])
                    run = domain.require(tx, "voice_run", envelope.get("run_id", ""))
                    try:
                        domain._require_outreach()
                    except DomainError:
                        disabled = True
                    else:
                        disabled = domain.now(tx) >= epoch(run["provider_deadline_at"])
                    if run.get("status") in (
                        "RUNNING",
                        "FAILED",
                        "STALE",
                        "EXPIRED",
                        "ENDED",
                        "COMPLETED",
                        "INTERRUPTED",
                    ):
                        return {"claimed": False, "status": run["status"]}
                    if (
                        envelope.get("status") != "GENERATING"
                        or envelope.get("payload_hash") != event["payload_hash"]
                        or incident.get("outreach_voice_envelope_id") != envelope["id"]
                        or run.get("status") != "GENERATING"
                        or run.get("envelope_id") != envelope["id"]
                    ):
                        raise PermissionError(
                            "Voice simulation is stale or unauthorized"
                        )
                    if disabled:
                        return {
                            "claimed": False,
                            "status": "FAILED",
                            "disabled_run_id": run["id"],
                        }
                    if run.get("provider_claim_id"):
                        return {"claimed": False, "status": "GENERATING"}
                    run.update(
                        provider_claim_id=claimant_id,
                        provider_claimed_at=domain.now(tx),
                    )
                    tx.put("voice_run", run["id"], run)
                    return {
                        "claimed": True,
                        "status": "GENERATING",
                        "run": run,
                        "envelope": envelope,
                        "provider_envelope": {
                            "id": envelope["id"],
                            "incident_id": envelope["incident_id"],
                            "workspace_id": workspace_id,
                            "owner_id": incident["owner_id"],
                            "facts": envelope["facts"],
                            "max_turns": envelope["max_turns"],
                        },
                        "principal": {
                            "id": incident["owner_id"],
                            "user": {"id": incident["owner_id"]},
                            "workspace_id": workspace_id,
                        },
                    }

            claim = _retry_concurrent(claim_provider)
            if claim.get("disabled_run_id"):
                _retry_concurrent(
                    lambda: domain.fail_voice_generation(
                        workspace_id, claim["disabled_run_id"]
                    )
                )
                return {"status": "FAILED"}
            if not claim["claimed"]:
                return {"status": claim["status"]}
            from services.agents import runtime_client

            run = claim["run"]
            envelope = claim["envelope"]
            turns = None
            try:
                for _ in range(2):
                    if epoch(run["provider_deadline_at"]) - time.time() < 95:
                        break
                    proposal = runtime_client.simulate_voice(
                        claim["provider_envelope"], claim["principal"]
                    )
                    try:
                        turns = domain.validate_voice_script(envelope, proposal)
                        break
                    except DomainError:  # noqa: S112 - deterministic retry, no content logs.
                        continue
                if turns is None:
                    raise ValueError("Voice output failed deterministic validation")
                result = _retry_concurrent(
                    lambda: domain.complete_voice_generation(
                        workspace_id,
                        run["id"],
                        turns,
                        claimant_id,
                    )
                )
                return {"status": result["status"]}
            # Fail closed without logging model output, facts, or captions.
            except Exception:  # noqa: BLE001
                _retry_concurrent(
                    lambda: domain.fail_voice_generation(
                        workspace_id, run["id"], claimant_id
                    )
                )
                return {"status": "FAILED"}
        raise ValueError("Unsupported outreach provider action")
    if phase == "register_approval":
        incident_id, draft_id = event["incident_id"], event["draft_id"]
        with domain.store.atomic(workspace_id) as tx:
            domain.check_admission(tx)
            decision = _decision(tx, incident_id, draft_id)
            if decision == "invalid":
                raise PermissionError("Unknown callback revision")
            previous = tx.get("callback", draft_id)
            if (
                previous
                and not previous.get("consumed")
                and previous.get("task_token") != event["task_token"]
            ):
                raise RuntimeError("A different workflow already awaits this revision")
            tx.put(
                "callback",
                draft_id,
                {
                    "id": draft_id,
                    "incident_id": incident_id,
                    "draft_id": draft_id,
                    "task_token": event["task_token"],
                    "consumed": False,
                    "generation": domain.settings.data_generation,
                    "expires_at": int(time.time()) + 3600,
                },
            )
        # Reconcile approval-before-registration without exposing token to clients.
        wake_approval(workspace_id, incident_id, draft_id, domain)
        return {"registered": True}
    if phase != "run":
        raise ValueError("Unsupported durable workflow phase")
    operation_id = event["operation_id"]
    asyncio.run(domain.run_job(workspace_id, operation_id))
    with domain.store.atomic(workspace_id) as tx:
        domain.check_admission(tx)
        operation = tx.get("operation", operation_id)
        job = tx.get("job", operation_id)
        if not operation or not job:
            raise ValueError("Unknown persisted operation")
        if operation["status"] == "failed":
            raise RuntimeError(
                "Persisted operation failed; consult sanitized application activity"
            )
        result = {"await_approval": False, "has_next_job": False}
        if job["status"] in ("pending", "running"):
            result.update(
                has_next_job=True,
                next_operation_id=operation_id,
                wait_seconds=max(
                    1,
                    min(
                        300,
                        int(
                            max(job.get("due_at", 0), job.get("lease_until", 0))
                            - time.time()
                        )
                        + 1,
                    ),
                ),
            )
            return result
        incident_id = (operation.get("result") or {}).get("incident_id")
        if incident_id:
            incident = tx.get("incident", incident_id)
            if (
                job["kind"] == "decide"
                and incident
                and incident.get("submission_status") == "AWAITING_APPROVAL"
            ):
                result.update(
                    await_approval=True,
                    incident_id=incident_id,
                    draft_id=incident["draft_id"],
                )
            if job["kind"] in ("submit", "reconcile", "check_status"):
                indexed = [
                    tx.get("job", row["job_id"])
                    for row in tx.list("incident_jobs:" + incident_id, limit=500)
                ]
                pending = [
                    j
                    for j in indexed
                    if j and j["kind"] == "check_status" and j["status"] == "pending"
                ]
                if pending:
                    next_job = min(pending, key=lambda j: j["due_at"])
                    result.update(
                        has_next_job=True,
                        next_operation_id=next_job["id"],
                        wait_seconds=max(
                            1, min(300, int(next_job["due_at"] - time.time()) + 1)
                        ),
                    )
        return result
