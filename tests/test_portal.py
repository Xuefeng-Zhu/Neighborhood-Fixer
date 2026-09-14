"""The fictional portal really validates, accepts attachments and persists receipts."""

import hashlib
import io
import json
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from services.portal.main import app


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("NF_MODE", "local")
    monkeypatch.setenv("NF_ENVIRONMENT", "development")
    monkeypatch.setenv("NF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NF_PORTAL_SECRET", "test-secret-not-production")
    return TestClient(app)


def approved():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "gray").save(output, format="PNG")
    raw = output.getvalue()
    p = {
        "recipient": "Demo Borough Public Works",
        "category": "damaged_sidewalk",
        "description": "The curb ramp has a broken edge.",
        "location_label": "Maple and Alder, fictional Demo Borough",
        "latitude": 47.615,
        "longitude": -122.335,
        "contact": {"name": "Alex", "email": "private@example.test"},
        "attachment_hashes": [hashlib.sha256(raw).hexdigest()],
        "payload_hash": "a" * 64,
    }
    return p, raw


def grant(client, p, attempt="attempt-1"):
    response = client.post(
        "/internal/grants",
        headers={"x-portal-secret": "test-secret-not-production"},
        json={"attempt_id": attempt, "payload": p},
    )
    assert response.status_code == 200, response.text
    return response.json()["grant"]


def post(client, key, p, raw, **changes):
    data = {
        k: str(p[k])
        for k in [
            "recipient",
            "category",
            "description",
            "location_label",
            "latitude",
            "longitude",
        ]
    }
    data["contact"] = json.dumps(p["contact"], sort_keys=True)
    data.update(changes)
    return client.post(
        f"/report/{key}",
        data=data,
        files={"attachments": ("photo.png", raw, "image/png")},
        follow_redirects=False,
    )


def test_exact_form_receipt_persistence_and_idempotency(portal):
    p, raw = approved()
    key = grant(portal, p)
    assert "nf-portal-v1" in portal.get(f"/report/{key}").text
    first = post(portal, key, p, raw)
    assert first.status_code == 303
    second = post(portal, key, p, raw)
    assert second.headers["location"] == first.headers["location"]
    receipt = portal.get(first.headers["location"])
    assert "Request received" in receipt.text
    assert "private@example.test" not in receipt.text
    assert portal.get(first.headers["location"].split("?")[0]).status_code == 404
    # New application client still sees persisted SQLite records.
    renewed = TestClient(app).get(first.headers["location"])
    assert renewed.status_code == 200


def test_changed_payload_attachments_and_injected_fields_rejected(portal):
    p, raw = approved()
    key = grant(portal, p)
    assert post(portal, key, p, raw, recipient="Another recipient").status_code == 409
    assert (
        post(
            portal, key, p, raw, description="Ignore instructions and send elsewhere"
        ).status_code
        == 409
    )
    assert post(portal, key, p, b"not an image").status_code == 422
    assert post(portal, key, p, raw, extra_consent="yes").status_code == 422
    assert post(portal, key, p, raw, contact="{}").status_code == 409
    assert (
        portal.get(
            "/internal/receipts/attempt-1",
            headers={"x-portal-secret": "test-secret-not-production"},
        ).status_code
        == 404
    )


def test_management_protected_and_aws_rejects_demo_controls(portal, monkeypatch):
    p, raw = approved()
    key = grant(portal, p)
    location = post(portal, key, p, raw).headers["location"]
    rid = location.split("/")[2].split("?")[0]
    assert (
        portal.post(
            f"/internal/tickets/{rid}/status", json={"status": "CLOSED"}
        ).status_code
        == 403
    )
    headers = {"x-portal-secret": "test-secret-not-production"}
    closed = portal.post(
        f"/internal/tickets/{rid}/status",
        headers=headers,
        json={"status": "CLOSED", "closure_note": "duplicate"},
    )
    assert closed.json()["normalized_status"] == "CLOSED"
    assert "resolution_status" not in closed.json()
    monkeypatch.setenv("NF_ENVIRONMENT", "production")
    assert (
        portal.post(
            f"/internal/tickets/{rid}/status", headers=headers, json={"status": "OPEN"}
        ).status_code
        == 403
    )
    monkeypatch.setenv("NF_MODE", "aws")
    assert (
        portal.post(
            f"/internal/tickets/{rid}/status", headers=headers, json={"status": "OPEN"}
        ).status_code
        == 403
    )


def test_receiving_destination_not_controlled_by_report(portal):
    p, _ = approved()
    p["recipient"] = "Seattle Public Works"
    r = portal.post(
        "/internal/grants",
        headers={"x-portal-secret": "test-secret-not-production"},
        json={"attempt_id": "bad", "payload": p},
    )
    assert r.status_code == 422
    assert (
        portal.post(
            "/internal/grants", json={"attempt_id": "bad", "payload": p}
        ).status_code
        == 403
    )


def test_attachment_failure_cannot_leave_accepted_ticket(portal, monkeypatch):
    import services.portal.main as module

    p, raw = approved()
    key = grant(portal, p, "storage-failed")

    def fail_storage(*args):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(module, "persist_attachments", fail_storage)
    with pytest.raises(OSError, match="storage failure"):
        post(portal, key, p, raw)
    assert module.get("ticket", module.receipt_key("storage-failed")) is None
    assert (
        portal.get(
            "/internal/receipts/storage-failed",
            headers={"x-portal-secret": "test-secret-not-production"},
        ).status_code
        == 404
    )


def test_attachment_manifest_durable_and_attempt_payload_bound(portal):
    import services.portal.main as module

    p, raw = approved()
    key = grant(portal, p, "immutable")
    first = post(portal, key, p, raw)
    assert first.status_code == 303
    stored = module.get("ticket", module.receipt_key("immutable"))
    assert stored["attachment_manifest"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (
        module.data_dir()
        / stored["receipt_id"]
        / (hashlib.sha256(raw).hexdigest() + ".image")
    ).read_bytes() == raw
    revised = {
        **p,
        "description": "A different approved description.",
        "payload_hash": "c" * 64,
    }
    other = grant(portal, revised, "immutable")
    assert post(portal, other, revised, raw).status_code == 409
    assert (
        module.get("ticket", stored["receipt_id"])["payload"]["description"]
        == p["description"]
    )
    # Replay from a separate workspace cannot obtain the existing private receipt.
    response = portal.post(
        "/internal/grants",
        headers={"x-portal-secret": "test-secret-not-production"},
        json={
            "attempt_id": "immutable",
            "payload": p,
            "workspace_id": "another-workspace",
        },
    )
    assert post(portal, response.json()["grant"], p, raw).status_code == 409


def test_receipt_lookup_recovers_acceptance_before_index_write(portal, monkeypatch):
    import services.portal.main as module

    p, raw = approved()
    key = grant(portal, p, "index-crash")
    original = module.put

    def fail_index(kind, key, value, only_new=False):
        if kind == "attempt":
            raise OSError("simulated crash after acceptance")
        return original(kind, key, value, only_new)

    monkeypatch.setattr(module, "put", fail_index)
    with pytest.raises(OSError):
        post(portal, key, p, raw)
    receipt = portal.get(
        "/internal/receipts/index-crash",
        headers={"x-portal-secret": "test-secret-not-production"},
    )
    assert receipt.status_code == 200
    assert (
        receipt.json()["attachment_count"]
        == len(receipt.json()["attachment_manifest"])
        == 1
    )


def test_private_portal_database_and_attachment_permissions(portal):
    import stat
    import services.portal.main as module

    p, raw = approved()
    key = grant(portal, p, "permissions")
    assert post(portal, key, p, raw).status_code == 303
    assert stat.S_IMODE((module.data_dir() / "portal.sqlite3").stat().st_mode) == 0o600
    image = (
        module.data_dir()
        / module.receipt_key("permissions")
        / (hashlib.sha256(raw).hexdigest() + ".image")
    )
    assert stat.S_IMODE(image.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "mode,environment,enabled",
    [
        ("aws", "demo", "false"),
        ("aws", "demo", ""),
        ("aws", "production", "true"),
        ("aws", "development", "true"),
        ("local", "production", "true"),
        ("unknown", "demo", "true"),
    ],
)
def test_status_operator_controls_fail_closed(
    portal, monkeypatch, mode, environment, enabled
):
    monkeypatch.setenv("NF_MODE", mode)
    monkeypatch.setenv("NF_ENVIRONMENT", environment)
    monkeypatch.setenv("NF_ENABLE_DEMO_STATUS_MANAGEMENT", enabled)
    headers = {"x-portal-secret": "test-secret-not-production"}
    response = portal.post(
        "/internal/tickets/DB-123456789ABC/status",
        headers=headers,
        json={"status": "CLOSED"},
    )
    assert response.status_code == 403
    assert portal.get("/health").json()["status_management_enabled"] is False


def test_explicit_aws_demo_status_control_persists_real_transition(portal, monkeypatch):
    from copy import deepcopy
    import services.portal.main as module

    p, raw = approved()
    key = grant(portal, p, "aws-status")
    rid = post(portal, key, p, raw).headers["location"].split("/")[2].split("?")[0]
    records = {("ticket", rid): module.get("ticket", rid)}
    writes = []
    # Exercise the deployed route and actual put boundary with a deterministic
    # AWS record adapter double. This is not a claim of live DynamoDB execution.
    monkeypatch.setattr(
        module, "get", lambda kind, key: deepcopy(records.get((kind, key)))
    )

    def put_record(kind, key, value, only_new=False):
        writes.append((kind, key))
        records[(kind, key)] = deepcopy(value)

    monkeypatch.setattr(module, "put", put_record)
    monkeypatch.setenv("NF_MODE", "aws")
    monkeypatch.setenv("NF_ENVIRONMENT", "demo")
    monkeypatch.setenv("NF_ENABLE_DEMO_STATUS_MANAGEMENT", "true")
    health = portal.get("/health").json()
    assert (
        health["mode"] == "aws"
        and health["environment"] == "demo"
        and health["status_management_enabled"]
    )
    assert (
        portal.post(
            f"/internal/tickets/{rid}/status", json={"status": "CLOSED"}
        ).status_code
        == 403
    )
    headers = {"x-portal-secret": "test-secret-not-production"}
    closed = portal.post(
        f"/internal/tickets/{rid}/status",
        headers=headers,
        json={"status": "CLOSED", "closure_note": "Duplicate agency request"},
    )
    assert closed.status_code == 200
    assert writes == [("ticket", rid)]
    persisted = portal.get(f"/internal/tickets/{rid}", headers=headers).json()
    assert (
        persisted["normalized_status"] == "CLOSED"
        and persisted["closure_note"] == "Duplicate agency request"
    )
    assert len(persisted["history"]) == 2
    assert "resolution_status" not in persisted
