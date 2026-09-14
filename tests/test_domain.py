"""Domain regression tests use labeled deterministic models and browser doubles.
Real browser execution is independently exercised in test_browser.py and e2e.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from services.api.config import Settings
from services.api.domain import Domain, DomainError
from services.api.main import create_app


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("NF_MODE", "local")
    monkeypatch.setenv("NF_ENVIRONMENT", "development")
    settings = Settings(data_dir=tmp_path, lease_seconds=1)
    domain = Domain(settings)
    client = TestClient(create_app(settings, domain))
    principal = client.post("/api/demo/session", json={"resident": "alex"}).json()
    yield domain, client, principal
    client.close()


def observation(
    description="The curb ramp has broken pavement along the crossing.", **kwargs
):
    return {
        "description": description,
        "category": "damaged_sidewalk",
        "latitude": 47.615,
        "longitude": -122.335,
        "location_label": "Cedar Street at 4th Avenue, Demo Borough",
        "location_confirmed": True,
        "asset_public": "yes",
        "evidence_ids": [],
        "share_public": True,
        **kwargs,
    }


def run(domain, p, op):
    asyncio.run(domain.run_job(p["workspace_id"], op))
    with domain.store.atomic(p["workspace_id"]) as tx:
        return tx.get("operation", op)


def analyze(domain, p, **kwargs):
    obs = domain.create_observation(p, observation(**kwargs))
    op = domain.start_analysis(p, obs["id"])
    result = run(domain, p, op)
    assert result["status"] == "completed", result
    return obs, result["result"]


def make_case(domain, p, **kwargs):
    obs, _ = analyze(domain, p, **kwargs)
    op = domain.decide(p, obs["id"], None, True)
    result = run(domain, p, op)
    assert result["status"] == "completed", result
    return domain.incident_detail(p, result["result"]["incident_id"]), obs


def approve(domain, p, case, share_evidence=False):
    d = case["draft"]
    return domain.approve(
        p,
        case["id"],
        {
            "draft_id": d["id"],
            "payload_hash": d["payload_hash"],
            "publish_consent": True,
            "share_evidence": share_evidence,
        },
    )


@pytest.fixture
def browser_double(monkeypatch):
    import services.worker.browser as browser

    state = {"submissions": [], "receipts": {}, "closed": False}

    async def submit(
        payload, attempt_id, evidence_paths, screenshot_path=None, before_write=None
    ):
        if before_write:
            before_write()
        state["submissions"].append(
            {
                "payload": payload,
                "attempt_id": attempt_id,
                "evidence_paths": evidence_paths,
            }
        )
        receipt = {
            "receipt_id": "DB-" + attempt_id[:12],
            "url": "http://127.0.0.1:8001/receipt/private?access=opaque",
            "raw_status": "Received",
            "normalized_status": "RECEIVED",
            "steps": ["Real browser mocked in domain test"],
        }
        state["receipts"][attempt_id] = receipt
        if payload.get("simulate_lost_receipt"):
            raise browser.OutcomeUnknown("simulated connection failure")
        return receipt

    async def lookup(attempt_id):
        return state["receipts"].get(attempt_id)

    async def status(receipt_id):
        return {
            "receipt_id": receipt_id,
            "normalized_status": "CLOSED" if state["closed"] else "OPEN",
            "raw_status": "Closed as duplicate" if state["closed"] else "Open",
            "closure_note": "Duplicate" if state["closed"] else "",
        }

    monkeypatch.setattr(browser, "submit_report", submit)
    monkeypatch.setattr(browser, "lookup_receipt", lookup)
    monkeypatch.setattr(browser, "get_ticket_status", status)
    return state


def test_full_journey_second_neighbor_restart_closure_verification(
    system, browser_double
):
    domain, client, alex = system
    case, obs = make_case(domain, alex)
    sam = client.post(
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": alex["workspace_id"]},
    ).json()
    second, result = analyze(
        domain, sam, description="I also found broken pavement at this curb ramp."
    )
    assert result["duplicate_candidates"][0]["id"] == case["id"]
    linked = run(domain, sam, domain.decide(sam, second["id"], case["id"], False))
    assert linked["result"]["incident_id"] == case["id"]
    assert domain.incident_detail(sam, case["id"])["observation_count"] == 2
    with domain.store.atomic(alex["workspace_id"]) as tx:
        assert len(tx.list("draft")) == 1
    op = approve(domain, alex, case)
    restarted = Domain(domain.settings)
    assert (
        run(restarted, alex, op)["result"]["submission_status"] == "RECEIPT_CONFIRMED"
    )
    assert len(browser_double["submissions"]) == 1
    browser_double["closed"] = True
    with restarted.store.atomic(alex["workspace_id"]) as tx:
        ws = tx.get("workspace", alex["workspace_id"])
        ws["clock_offset"] = 120
        tx.put("workspace", ws["id"], ws)
    asyncio.run(restarted.run_job(alex["workspace_id"]))
    detail = restarted.incident_detail(sam, case["id"])
    assert detail["agency_status"] == "CLOSED"
    assert detail["resolution_status"] == "VERIFICATION_REQUESTED"
    assert detail["ticket"]["closure_note"] == "Duplicate"
    assert detail["ticket"].get("url") is None
    restarted.verification(sam, case["id"], {"choice": "looks_fixed"})
    assert (
        restarted.incident_detail(alex, case["id"])["resolution_status"]
        == "RESIDENT_CONFIRMED_FIXED"
    )
    with restarted.store.atomic(alex["workspace_id"]) as tx:
        notes = tx.list("notification")
        assert {n["user_id"] for n in notes if "looks fixed" in n["message"]} == {
            alex["user"]["id"],
            sam["user"]["id"],
        }


def test_missing_information_is_clarification_not_invention(system):
    domain, _, p = system
    obs, result = analyze(domain, p, location_confirmed=False, asset_public="unknown")
    assert len(result["analysis"]["missing_information"]) == 2
    assert "Exact dimensions" in result["analysis"]["unknowns"][0]
    with pytest.raises(DomainError, match="Answer"):
        domain.decide(p, obs["id"], None, True)
    domain.patch_observation(
        p, obs["id"], {"location_confirmed": True, "asset_public": "yes"}
    )
    run(domain, p, domain.start_analysis(p, obs["id"]))
    assert (
        run(domain, p, domain.decide(p, obs["id"], None, True))["status"] == "completed"
    )


def test_nearby_distinct_and_reversible_link_never_starts_submission(system):
    domain, client, p = system
    first, _ = make_case(domain, p)
    distinct, _ = make_case(
        domain, p, description="Another separate curb ramp on the opposite corner."
    )
    assert distinct["id"] != first["id"]
    sam = client.post("/api/demo/session", json={"resident": "sam"}).json()
    obs, _ = analyze(domain, sam)
    run(domain, sam, domain.decide(sam, obs["id"], first["id"], False))
    domain.unlink_observation(sam, obs["id"])
    assert domain.incident_detail(p, first["id"])["observation_count"] == 1
    with domain.store.atomic(p["workspace_id"]) as tx:
        assert tx.get("observation", obs["id"])["incident_id"] is None
        assert len(tx.list("attempt")) == 0
        assert any(
            e["type"] == "OBSERVATION_UNLINKED" for e in tx.list("event:" + first["id"])
        )


def test_parallel_approval_reserves_one_frozen_external_action(system, browser_double):
    domain, _, p = system
    case, _ = make_case(domain, p)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: approve(domain, p, case), range(16)))
    assert len(set(results)) == 1
    with domain.store.atomic(p["workspace_id"]) as tx:
        assert len(tx.list("attempt")) == 1
        assert len(tx.list("approval")) == 1
    run(domain, p, results[0])
    run(domain, p, results[0])
    assert len(browser_double["submissions"]) == 1
    frozen = browser_double["submissions"][0]["payload"]
    assert frozen["description"] == case["draft"]["description"]
    assert frozen["payload_hash"] == case["draft"]["payload_hash"]


def test_edited_expired_and_wrong_hash_cannot_use_stale_approval(system):
    domain, _, p = system
    case, _ = make_case(domain, p)
    old = case["draft"]
    domain.create_draft(
        p,
        case["id"],
        {"description": "Updated resident wording about the damaged curb ramp."},
    )
    with pytest.raises(DomainError) as stale:
        approve(domain, p, case)
    assert stale.value.code == "STALE_APPROVAL"
    case = domain.incident_detail(p, case["id"])
    with pytest.raises(DomainError) as wrong:
        domain.approve(
            p,
            case["id"],
            {
                "draft_id": case["draft"]["id"],
                "payload_hash": "0" * 64,
                "publish_consent": False,
            },
        )
    assert wrong.value.code == "PAYLOAD_CHANGED"
    with domain.store.atomic(p["workspace_id"]) as tx:
        ws = tx.get("workspace", p["workspace_id"])
        ws["clock_offset"] = 7200
        tx.put("workspace", ws["id"], ws)
        assert tx.get("draft", old["id"])["description"] == old["description"]
    with pytest.raises(DomainError) as expired:
        approve(domain, p, case)
    assert expired.value.code == "APPROVAL_EXPIRED"


def test_lost_receipt_no_blind_retry_then_lookup(system, browser_double):
    domain, _, p = system
    case, _ = make_case(domain, p)
    with domain.store.atomic(p["workspace_id"]) as tx:
        ws = tx.get("workspace", p["workspace_id"])
        ws["lost_receipt"] = True
        tx.put("workspace", ws["id"], ws)
    op = approve(domain, p, case)
    assert run(domain, p, op)["result"]["submission_status"] == "OUTCOME_UNKNOWN"
    assert approve(domain, p, case) == op
    run(domain, p, op)
    assert len(browser_double["submissions"]) == 1
    result = run(domain, p, domain.reconcile(p, case["id"]))
    assert result["result"]["submission_status"] == "RECEIPT_CONFIRMED"
    assert len(browser_double["submissions"]) == 1


def test_worker_restart_after_write_never_repeats_unknown_action(
    system, browser_double
):
    domain, _, p = system
    case, _ = make_case(domain, p)
    op = approve(domain, p, case)
    claimed = domain.claim_job(p["workspace_id"], op)
    with domain.store.atomic(p["workspace_id"]) as tx:
        attempt = tx.get("attempt", claimed["payload"]["attempt_id"])
        attempt["write_started"] = True
        tx.put("attempt", attempt["id"], attempt)
        job = tx.get("job", op)
        job["lease_until"] = 0
        tx.put("job", op, job)
    assert not asyncio.run(Domain(domain.settings).run_job(p["workspace_id"], op))
    assert (
        domain.incident_detail(p, case["id"])["submission_status"] == "OUTCOME_UNKNOWN"
    )
    assert not browser_double["submissions"]


def test_worker_restart_before_write_and_cancel_before_click(
    system, browser_double, monkeypatch
):
    domain, _, p = system
    case, _ = make_case(domain, p)
    op = approve(domain, p, case)
    domain.claim_job(p["workspace_id"], op)
    with domain.store.atomic(p["workspace_id"]) as tx:
        job = tx.get("job", op)
        job["lease_until"] = 0
        tx.put("job", op, job)
    import services.worker.browser as browser

    async def cancel_when_ready(
        payload, attempt_id, evidence_paths, screenshot_path=None, before_write=None
    ):
        domain.cancel(p, case["id"])
        before_write()
        pytest.fail("Cancellation must prevent the agency click")

    monkeypatch.setattr(browser, "submit_report", cancel_when_ready)
    assert (
        run(Domain(domain.settings), p, op)["result"]["submission_status"]
        == "CANCELLED"
    )
    assert domain.incident_detail(p, case["id"])["submission_status"] == "CANCELLED"


def test_still_present_never_resubmits_or_resolves_from_agency_closure(
    system, browser_double
):
    domain, _, p = system
    case, _ = make_case(domain, p)
    run(domain, p, approve(domain, p, case))
    with domain.store.atomic(p["workspace_id"]) as tx:
        domain.record_status(
            tx,
            case["id"],
            {
                "normalized_status": "CLOSED",
                "raw_status": "Closed - duplicate",
                "closure_note": "Duplicate request",
            },
        )
    domain.verification(p, case["id"], {"choice": "still_present"})
    assert domain.incident_detail(p, case["id"])["resolution_status"] == "STILL_PRESENT"
    with domain.store.atomic(p["workspace_id"]) as tx:
        assert len(tx.list("attempt")) == 1
    assert len(browser_double["submissions"]) == 1


def test_evidence_sanitized_scoped_and_public_projection(system):
    domain, client, alex = system
    image = Image.new("RGB", (24, 20), "red")
    exif = Image.Exif()
    exif[315] = "Private author metadata"
    data = BytesIO()
    image.save(data, "JPEG", exif=exif)
    uploaded = client.post(
        "/api/uploads", files={"file": ("private.jpg", data.getvalue(), "image/jpeg")}
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence = uploaded.json()
    assert "path" not in evidence and evidence["sanitized"]
    sanitized = client.get("/api/evidence/" + evidence["id"])
    assert not Image.open(BytesIO(sanitized.content)).getexif()
    case, _ = make_case(domain, alex, evidence_ids=[evidence["id"]], share_public=False)
    client.post("/api/demo/session", json={"resident": "sam"}).json()
    assert client.get("/api/evidence/" + evidence["id"]).status_code == 403
    assert (
        client.get("/api/evidence/" + evidence["id"] + "?public=true").status_code
        == 403
    )
    assert client.get("/api/incidents/" + case["id"]).status_code == 404
    assert (
        client.post(
            "/api/incidents/" + case["id"] + "/approve",
            json={
                "draft_id": case["draft"]["id"],
                "payload_hash": case["draft"]["payload_hash"],
                "publish_consent": True,
            },
        ).status_code
        == 403
    )
    # Separate reviewed-photo publication approval exposes only the sanitized derivative.
    approve(domain, alex, case, share_evidence=True)
    detail = client.get("/api/incidents/" + case["id"]).json()
    assert detail["draft"] is None
    assert detail["owner_id"] == ""
    assert (
        client.get("/api/evidence/" + evidence["id"] + "?public=true").status_code
        == 200
    )
    public = client.get("/api/incidents").text
    assert (
        "Private author" not in public
        and "contact" not in public
        and str(domain.settings.data_dir) not in public
    )


def test_workspace_cookie_cannot_be_spoofed_or_joined_by_id(system):
    domain, client, p = system
    case, _ = make_case(domain, p)
    outsider = TestClient(create_app(domain.settings, domain))
    assert (
        outsider.get(
            "/api/incidents/" + case["id"],
            headers={"x-user-id": p["user"]["id"], "x-workspace-id": p["workspace_id"]},
        ).status_code
        == 401
    )
    assert (
        outsider.post(
            "/api/demo/session",
            json={"resident": "sam", "workspace_id": p["workspace_id"]},
        ).status_code
        == 403
    )
    other = outsider.post("/api/demo/session", json={"resident": "alex"}).json()
    assert other["workspace_id"] != p["workspace_id"]
    assert outsider.get("/api/incidents/" + case["id"]).status_code == 404
    assert (
        client.post(
            "/api/demo/reset", headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )


def test_demo_controls_rejected_outside_local_development_and_aws_missing_visible(
    tmp_path,
):
    app = create_app(Settings(data_dir=tmp_path, environment="production"))
    client = TestClient(app)
    assert (
        client.post("/api/demo/session", json={"resident": "alex"}).status_code == 403
    )
    aws = TestClient(create_app(Settings(mode="aws", data_dir=tmp_path, table_name="")))
    health = aws.get("/api/health").json()
    assert health["mode"] == "aws" and health["status"] == "unavailable"
    assert health["integrations"]["model_provider"]["status"] != "simulated"
    assert aws.post("/api/demo/session", json={"resident": "alex"}).status_code == 403
    assert aws.get("/api/session", headers={"x-user-id": "spoof"}).status_code == 401


def test_unsupported_routing_handoff_and_no_external_write(system):
    domain, _, p = system
    case, _ = make_case(domain, p, latitude=48.2, longitude=-123.1)
    assert case["submission_status"] == "HANDOFF_REQUIRED"
    assert case["draft"] is None
    assert case["routing"]["recipient"] is None
    with pytest.raises(DomainError):
        domain.create_draft(p, case["id"], {})


def test_geo_index_finds_candidates_after_first_200_other_incidents(system):
    domain, _, p = system
    with domain.store.atomic(p["workspace_id"]) as tx:
        for i in range(220):
            tx.put(
                "incident",
                f"000{i:04}",
                {
                    "id": f"000{i:04}",
                    "shared_public": True,
                    "owner_id": "fixture",
                    "category": "pothole",
                    "latitude": 40,
                    "longitude": -110,
                },
            )
    case, _ = make_case(domain, p)
    obs, result = analyze(domain, p)
    assert any(c["id"] == case["id"] for c in result["duplicate_candidates"])


def test_case_events_and_notifications_index_are_not_lost_among_other_cases(system):
    domain, _, p = system
    with domain.store.atomic(p["workspace_id"]) as tx:
        for i in range(520):
            tx.put("event", f"000{i:04}", {"incident_id": "other"})
    case, _ = make_case(domain, p)
    assert any(e["type"] == "AWAITING_APPROVAL" for e in case["events"])


def test_non_image_rejected_and_error_envelope_hides_payload(system):
    _, client, _ = system
    response = client.post(
        "/api/uploads",
        files={"file": ("evil.svg", b'<svg onload="alert(1)"></svg>', "image/svg+xml")},
    )
    assert response.status_code == 415
    invalid = client.post(
        "/api/observations", json={"description": "private secret contact text"}
    )
    assert invalid.status_code == 422
    assert set(invalid.json()["error"]) == {
        "code",
        "message",
        "retryable",
        "correlation_id",
    }
    assert "private secret" not in invalid.text


def test_task_tokens_and_callbacks_not_exposed_or_http_writable(system):
    domain, client, p = system
    case, _ = make_case(domain, p)
    with domain.store.atomic(p["workspace_id"]) as tx:
        tx.put(
            "callback",
            case["draft"]["id"],
            {"task_token": "secret-token", "incident_id": case["id"]},
        )
    assert "secret-token" not in client.get("/api/incidents/" + case["id"]).text
    assert (
        client.post(
            "/api/callback", json={"task_token": "secret-token", "approved": True}
        ).status_code
        == 404
    )


def test_model_cannot_override_trusted_routing_or_required_clarification(
    system, monkeypatch
):
    domain, _, p = system

    class DishonestModel:
        def analyze(self, *args):
            return {
                "observed_facts": [],
                "resident_claims": [],
                "unknowns": [],
                "candidate_category": "damaged_sidewalk",
                "missing_information": [],
                "provenance": "test adversarial model",
            }

        def route(self, *args):
            return {
                "supported": True,
                "recipient": "Demo Borough Public Works",
                "category": "damaged_sidewalk",
                "unresolved_questions": [],
            }

        def prepare(self, *args):
            pytest.fail(
                "Unsupported deterministic route must prevent coordinator execution"
            )

    monkeypatch.setattr(domain, "engine", lambda: DishonestModel())
    obs, result = analyze(domain, p, location_confirmed=False)
    assert result["analysis"]["missing_information"]
    obs, result = analyze(domain, p, latitude=0, longitude=0)
    outcome = run(domain, p, domain.decide(p, obs["id"], None, True))
    assert (
        domain.incident_detail(p, outcome["result"]["incident_id"])["submission_status"]
        == "HANDOFF_REQUIRED"
    )


def test_unknown_mode_does_not_fall_back_to_fixture(tmp_path):
    with pytest.raises(ValueError, match="never fall back"):
        Settings(mode="unrecognized", data_dir=tmp_path)


def test_latest_timeline_survives_more_than_500_events(system):
    domain, _, p = system
    case, _ = make_case(domain, p)
    with domain.store.atomic(p["workspace_id"]) as tx:
        for i in range(510):
            domain.event(tx, case["id"], "READ_CHECK", f"Safe read {i}")
    assert (
        domain.incident_detail(p, case["id"])["events"][-1]["message"]
        == "Safe read 509"
    )
    assert len(domain.incident_detail(p, case["id"])["events"]) == 100


def test_after_photo_remains_private_without_separate_public_consent(system):
    domain, client, p = system
    case, _ = make_case(domain, p)
    image = BytesIO()
    Image.new("RGB", (20, 20), "green").save(image, "PNG")
    eid = client.post(
        "/api/uploads", files={"file": ("after.png", image.getvalue(), "image/png")}
    ).json()["id"]
    domain.verification(
        p,
        case["id"],
        {
            "choice": "looks_fixed",
            "evidence_id": eid,
            "note": "After-photo from the public path.",
        },
    )
    assert (
        domain.incident_detail(p, case["id"])["verifications"][0]["evidence"]["id"]
        == eid
    )
    sam = client.post("/api/demo/session", json={"resident": "sam"}).json()
    other = domain.incident_detail(sam, case["id"])["verifications"][0]
    assert (
        other["choice"] == "looks_fixed"
        and "evidence" not in other
        and other["note"] == ""
    )


def test_integrity_failure_before_browser_write_releases_for_fresh_review(
    system, browser_double
):
    domain, client, p = system
    image = BytesIO()
    Image.new("RGB", (20, 20), "green").save(image, "PNG")
    eid = client.post(
        "/api/uploads", files={"file": ("before.png", image.getvalue(), "image/png")}
    ).json()["id"]
    case, _ = make_case(domain, p, evidence_ids=[eid])
    op = approve(domain, p, case)
    with domain.store.atomic(p["workspace_id"]) as tx:
        Path(tx.get("evidence", eid)["path"]).write_bytes(b"corrupted data")
    run(domain, p, op)
    assert (
        domain.incident_detail(p, case["id"])["submission_status"]
        == "FAILED_BEFORE_SUBMISSION"
    )
    assert not browser_double["submissions"]


def test_cloud_membership_is_server_provisioned_and_subject_scoped(tmp_path):
    from services.api.store import SQLiteStore

    settings = Settings(mode="aws", environment="demo", data_dir=tmp_path)
    domain = Domain(settings, store=SQLiteStore(tmp_path / "auth-test.sqlite3"))
    app = create_app(settings, domain)

    class TrustedGatewayEvent:
        def __init__(self, subject):
            self.subject = subject

        async def __call__(self, scope, receive, send):
            scope["aws.event"] = {
                "requestContext": {
                    "authorizer": {
                        "jwt": {
                            "claims": {
                                "sub": self.subject,
                                "name": "Authorized resident",
                            }
                        }
                    }
                }
            }
            await app(scope, receive, send)

    alice = TestClient(TrustedGatewayEvent("cognito-alice"))
    bob = TestClient(TrustedGatewayEvent("cognito-bob"))
    assert (
        alice.get("/api/session", headers={"x-workspace-id": "victim"}).json()[
            "workspace_id"
        ]
        == "cognito-alice"
    )
    with domain.store.atomic("auth") as tx:
        tx.put(
            "membership",
            "cognito-alice",
            {
                "target_workspace_id": "approved-shared-demo",
                "scope": "demo",
                "disabled": False,
            },
        )
        tx.put(
            "membership",
            "cognito-bob",
            {
                "target_workspace_id": "approved-shared-demo",
                "scope": "demo",
                "disabled": False,
            },
        )
    assert (
        alice.get("/api/session").json()["workspace_id"]
        == bob.get("/api/session").json()["workspace_id"]
        == "approved-shared-demo"
    )
    with domain.store.atomic("auth") as tx:
        m = tx.get("membership", "cognito-bob")
        m["disabled"] = True
        tx.put("membership", "cognito-bob", m)
    assert bob.get("/api/session").status_code == 403
    assert alice.get("/api/session").status_code == 200


def test_reset_isolated_workspace_clears_indexes_and_preserves_other_workspace(system):
    domain, client, p = system
    old, _ = make_case(domain, p)
    outsider = TestClient(create_app(domain.settings, domain))
    other = outsider.post("/api/demo/session", json={"resident": "alex"}).json()
    independent, _ = make_case(domain, other)
    assert client.post("/api/demo/reset").status_code == 200
    assert client.get("/api/incidents/" + old["id"]).status_code == 404
    assert outsider.get("/api/incidents/" + independent["id"]).status_code == 200
    with domain.store.atomic(p["workspace_id"]) as tx:
        assert not tx.list("user_incident:" + p["user"]["id"])
        assert len(tx.list("incident")) == 1


def test_aws_authorization_cors_preflight_allows_bearer_only_configured_origin(system):
    _, client, _ = system
    allowed = client.options(
        "/api/session",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert allowed.status_code == 200
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    blocked = client.options(
        "/api/session",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert (
        blocked.status_code == 400
        and "access-control-allow-origin" not in blocked.headers
    )
    spoof = client.options(
        "/api/session",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-user-id",
        },
    )
    assert spoof.status_code == 400


def test_summary_consent_does_not_publish_photos_without_explicit_photo_review(system):
    domain, client, alex = system
    image = BytesIO()
    Image.new("RGB", (20, 20), "green").save(image, "PNG")
    eid = client.post(
        "/api/uploads", files={"file": ("before.png", image.getvalue(), "image/png")}
    ).json()["id"]
    case, _ = make_case(domain, alex, evidence_ids=[eid], share_public=True)
    client.post("/api/demo/session", json={"resident": "sam"}).json()
    assert client.get("/api/incidents/" + case["id"]).status_code == 200
    assert client.get("/api/evidence/" + eid + "?public=true").status_code == 403
    approve(domain, alex, case)
    assert client.get("/api/evidence/" + eid + "?public=true").status_code == 403
    with domain.store.atomic(alex["workspace_id"]) as tx:
        approval = tx.list("approval")[0]
        assert approval["publish_consent"] and not approval["share_evidence"]


def test_aws_report_aggregate_limit_requires_fresh_smaller_draft(system):
    domain, client, p = system
    image = BytesIO()
    Image.new("RGB", (20, 20), "green").save(image, "PNG")
    eid = client.post(
        "/api/uploads", files={"file": ("before.png", image.getvalue(), "image/png")}
    ).json()["id"]
    case, _ = make_case(domain, p, evidence_ids=[eid])
    with domain.store.atomic(p["workspace_id"]) as tx:
        e = tx.get("evidence", eid)
        e["size"] = 4 * 1024 * 1024 + 1
        tx.put("evidence", eid, e)
    domain.settings.mode = "aws"
    with pytest.raises(DomainError) as approval:
        approve(domain, p, case)
    assert approval.value.code == "ATTACHMENTS_TOO_LARGE"
    with pytest.raises(DomainError):
        domain.create_draft(p, case["id"], {"attachment_ids": [eid]})
    fresh = domain.create_draft(p, case["id"], {"attachment_ids": []})
    assert fresh["revision"] == 2 and fresh["attachment_ids"] == []


def test_private_case_database_permissions(system):
    import stat

    domain, _, _ = system
    assert stat.S_IMODE(domain.store.path.stat().st_mode) == 0o600


@pytest.mark.parametrize("provider", ["local", "aws"])
def test_optional_receipt_storage_failure_preserves_confirmed_ticket(
    system, monkeypatch, provider
):
    import sys
    from types import SimpleNamespace
    import services.worker.browser as browser

    domain, _, p = system
    case, _ = make_case(domain, p)
    op = approve(domain, p, case)
    screenshot = domain.settings.data_dir / "optional-receipt.png"
    Image.new("RGB", (20, 20), "white").save(screenshot)

    async def submit(
        payload, attempt_id, evidence_paths, screenshot_path=None, before_write=None
    ):
        before_write()
        return {
            "receipt_id": "DB-CAPTURE-TEST",
            "url": "http://127.0.0.1:8001/receipt/private?access=opaque",
            "normalized_status": "RECEIVED",
            "raw_status": "Received",
            "screenshot_path": str(screenshot),
            "steps": ["Receipt captured"],
        }

    monkeypatch.setattr(browser, "submit_report", submit)
    if provider == "aws":
        domain.settings.mode = "aws"

        class FailingS3:
            def put_object(self, **kwargs):
                raise OSError("sensitive-provider-error-must-not-appear")

        monkeypatch.setitem(
            sys.modules,
            "boto3",
            SimpleNamespace(client=lambda *args, **kwargs: FailingS3()),
        )
    else:
        original_read = Path.read_bytes

        def failing_read(path):
            if path == screenshot:
                raise OSError("sensitive-local-path-must-not-appear")
            return original_read(path)

        monkeypatch.setattr(Path, "read_bytes", failing_read)
    result = run(domain, p, op)
    assert result["status"] == "completed"
    assert result["result"]["submission_status"] == "RECEIPT_CONFIRMED"
    detail = domain.incident_detail(p, case["id"])
    assert detail["submission_status"] == "RECEIPT_CONFIRMED"
    assert detail["ticket"]["receipt_id"] == "DB-CAPTURE-TEST"
    assert "screenshot_url" not in detail["ticket"]
    assert any(e["type"] == "RECEIPT_CAPTURE_UNAVAILABLE" for e in detail["events"])
    assert "sensitive-" not in json.dumps(detail)
    with domain.store.atomic(p["workspace_id"]) as tx:
        assert (
            tx.get("attempt", tx.get("incident", case["id"])["attempt_id"])["status"]
            == "RECEIPT_CONFIRMED"
        )
        assert len(tx.list("ticket")) == 1
        assert not tx.list("evidence")


def test_saved_receipt_screenshot_has_private_file_permissions(system, monkeypatch):
    import stat
    import services.worker.browser as browser

    domain, _, p = system
    case, _ = make_case(domain, p)
    op = approve(domain, p, case)
    screenshot = domain.settings.data_dir / "saved-receipt.png"
    Image.new("RGB", (20, 20), "white").save(screenshot)
    screenshot.chmod(0o644)

    async def submit(
        payload, attempt_id, evidence_paths, screenshot_path=None, before_write=None
    ):
        before_write()
        return {
            "receipt_id": "DB-PRIVATE-CAPTURE",
            "url": "http://127.0.0.1:8001/receipt/private?access=opaque",
            "normalized_status": "RECEIVED",
            "raw_status": "Received",
            "screenshot_path": str(screenshot),
        }

    monkeypatch.setattr(browser, "submit_report", submit)
    assert run(domain, p, op)["result"]["submission_status"] == "RECEIPT_CONFIRMED"
    assert stat.S_IMODE(screenshot.stat().st_mode) == 0o600
    assert domain.incident_detail(p, case["id"])["ticket"]["screenshot_url"].startswith(
        "/api/evidence/"
    )
