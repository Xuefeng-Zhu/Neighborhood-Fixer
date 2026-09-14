"""Operator command unit tests; AWS service clients are explicit doubles."""

import json
from types import SimpleNamespace

import httpx
import pytest

from scripts.set_demo_ticket_status import OperatorError, operate

PORTAL = "https://abcdef.lambda-url.us-west-2.on.aws"
RECEIPT = "DB-123456789ABC"
SECRET = "private-portal-secret-value"


def configured_clients(handler, enabled=True, portal=PORTAL):
    accesses = []

    def describe_stacks(**kwargs):
        accesses.append("stack")
        return {
            "Stacks": [
                {
                    "Outputs": [
                        {"OutputKey": "PortalUrl", "OutputValue": portal},
                        {
                            "OutputKey": "PortalSecretArn",
                            "OutputValue": "arn:aws:secretsmanager:us-west-2:123456789012:secret:portal-private",
                        },
                    ]
                }
            ]
        }

    def secret(**kwargs):
        accesses.append("secret")
        return {"SecretString": SECRET}

    def request(req):
        if req.url.path == "/health":
            accesses.append("health")
            return httpx.Response(
                200,
                json={
                    "destination": "fictional",
                    "mode": "aws",
                    "environment": "demo",
                    "status_management_enabled": enabled,
                },
            )
        return handler(req)

    return {
        "cloudformation": SimpleNamespace(describe_stacks=describe_stacks),
        "secrets_manager": SimpleNamespace(get_secret_value=secret),
        "http_client": httpx.Client(transport=httpx.MockTransport(request)),
    }, accesses


def arguments():
    return {
        "stack_name": "NeighborhoodFixer",
        "region": "us-west-2",
        "receipt_id": RECEIPT,
        "status": "CLOSED",
        "closure_note": "Duplicate request",
    }


def test_operator_preview_never_reads_secret_or_contacts_portal():
    clients, accesses = configured_clients(
        lambda req: pytest.fail("No portal request before confirmation")
    )
    result = operate(**arguments(), **clients)
    assert result["action"] == "preview" and not result["write_performed"]
    assert result["closure_note"] == "Duplicate request"
    assert accesses == ["stack"]


def test_confirmed_operator_uses_exact_stack_origin_and_hides_private_response():
    writes = []

    def handle(req):
        assert (
            req.method == "POST"
            and str(req.url) == PORTAL + "/internal/tickets/" + RECEIPT + "/status"
        )
        assert req.headers["x-portal-secret"] == SECRET
        writes.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "receipt_id": RECEIPT,
                "normalized_status": "CLOSED",
                "raw_status": "Closed",
                "updated_at": "2026-09-13T00:00:00Z",
                "payload": {"contact": "private@example.test"},
                "access": "private-receipt-token",
            },
        )

    clients, accesses = configured_clients(handle)
    result = operate(**arguments(), **clients, confirm=True)
    assert writes == [{"status": "CLOSED", "closure_note": "Duplicate request"}]
    assert accesses == ["stack", "health", "secret"]
    assert result["write_performed"] and not result["physical_resolution_changed"]
    assert "private" not in json.dumps(result)


def test_disabled_cloud_operator_never_fetches_secret():
    clients, accesses = configured_clients(
        lambda req: pytest.fail("Disabled operator must not write"), enabled=False
    )
    with pytest.raises(OperatorError, match="disabled"):
        operate(**arguments(), **clients, confirm=True)
    assert accesses == ["stack", "health"]


def test_untrusted_stack_destination_rejected_before_secret():
    clients, accesses = configured_clients(
        lambda req: pytest.fail("Untrusted destination"),
        portal="https://attacker.example",
    )
    with pytest.raises(OperatorError, match="configured HTTPS"):
        operate(**arguments(), **clients, confirm=True)
    assert accesses == ["stack"]


def test_ambiguous_status_write_not_retried_and_inspection_is_read_only():
    writes = []

    def broken(req):
        writes.append(req.method)
        raise httpx.ReadTimeout("Sensitive provider error " + SECRET, request=req)

    clients, _ = configured_clients(broken)
    with pytest.raises(OperatorError, match="outcome is uncertain") as error:
        operate(**arguments(), **clients, confirm=True)
    assert writes == ["POST"] and SECRET not in str(error.value)
    reads = []

    def inspect(req):
        reads.append(req.method)
        return httpx.Response(
            200,
            json={
                "receipt_id": RECEIPT,
                "normalized_status": "CLOSED",
                "raw_status": "Closed",
            },
        )

    clients, _ = configured_clients(inspect, enabled=False)
    result = operate(**arguments(), **clients, inspect=True)
    assert reads == ["GET"] and result["write_performed"] is False
