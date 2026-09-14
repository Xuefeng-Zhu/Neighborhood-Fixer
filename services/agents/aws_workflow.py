"""Step Functions bridge over the same Domain commands used by the local worker.

No HTTP endpoint accepts a task token. The private callback record is correlated
with the exact draft, and approval is committed before wake_approval is called.
The submission job always performs the existing reservation/cancellation checks.
"""

import asyncio
import json
import os
import time


def _client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "stepfunctions",
        config=Config(retries={"total_max_attempts": 2, "mode": "standard"}),
    )


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
        current = tx.get("callback", draft_id)
        if current and current.get("task_token") == token:
            # Retain audit correlation, erase the sensitive token after consumption.
            current.update(consumed=True, decision=decision)
            current.pop("task_token", None)
            tx.put("callback", draft_id, current)
    return {"woken": True}


def handler(event, context):
    from services.api.domain import Domain

    domain = Domain()
    workspace_id = event["workspace_id"]
    phase = event.get("phase", "run")
    if phase == "register_approval":
        incident_id, draft_id = event["incident_id"], event["draft_id"]
        with domain.store.atomic(workspace_id) as tx:
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
