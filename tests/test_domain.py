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


def switch_fixture_to_cloud(domain, p):
    domain.settings.mode = "aws"
    domain.settings.shared_workspace_id = p["workspace_id"]
    p.update(
        mode="aws", generation=domain.settings.data_generation, subject="user_test"
    )
    with domain.store.atomic(p["workspace_id"]) as tx:
        control = tx.get("workspace", p["workspace_id"])
        control.update(generation=domain.settings.data_generation, admission="public")
        tx.put("workspace", p["workspace_id"], control)
        for job in tx.list("job"):
            job.update(principal=p, generation=p["generation"])
            tx.put("job", job["id"], job)


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


def test_decisions_dedupe_by_resident_revision_and_outcome(system):
    domain, client, alex = system
    case, _ = make_case(domain, alex)
    sam = client.post(
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": alex["workspace_id"]},
    ).json()

    pending, _ = analyze(
        domain, sam, description="I also found broken pavement at this curb ramp."
    )
    with domain.store.atomic(sam["workspace_id"]) as tx:
        jobs_before = len(tx.list("job", limit=500))
    link_operations = [
        domain.decide(sam, pending["id"], case["id"], False) for _ in range(5)
    ]
    different_outcome = domain.decide(sam, pending["id"], None, True)
    assert len(set(link_operations)) == 1
    assert different_outcome != link_operations[0]
    with domain.store.atomic(sam["workspace_id"]) as tx:
        assert len(tx.list("job", limit=500)) == jobs_before + 2

    domain.patch_observation(
        sam,
        pending["id"],
        {"description": "I found newly widened damage at this same curb ramp."},
    )
    run(domain, sam, domain.start_analysis(sam, pending["id"]))
    revised_operation = domain.decide(sam, pending["id"], case["id"], False)
    assert revised_operation not in {link_operations[0], different_outcome}
    with domain.store.atomic(sam["workspace_id"]) as tx:
        failed = tx.get("operation", revised_operation)
        failed["status"] = "failed"
        tx.put("operation", revised_operation, failed)
    assert domain.decide(sam, pending["id"], case["id"], False) != revised_operation

    distinct, _ = analyze(
        domain, alex, description="A newly reported distinct crack at another corner."
    )
    distinct_operation = domain.decide(alex, distinct["id"], None, True)
    assert domain.decide(alex, distinct["id"], None, True) == distinct_operation
    run(domain, alex, distinct_operation)
    assert domain.decide(alex, distinct["id"], None, True) == distinct_operation

    linked, _ = analyze(
        domain, sam, description="A separate neighbor report at this curb ramp."
    )
    switch_fixture_to_cloud(domain, sam)
    with domain.store.atomic(sam["workspace_id"]) as tx:
        _, quota = domain.quota_record(tx, sam)
        reasoning_before = quota["reasoning"]
    original_operation = domain.decide(sam, linked["id"], case["id"], False)
    with domain.store.atomic(sam["workspace_id"]) as tx:
        _, quota = domain.quota_record(tx, sam)
        assert quota["reasoning"] == reasoning_before + 1
    run(domain, sam, original_operation)
    with domain.store.atomic(sam["workspace_id"]) as tx:
        linked_jobs = len(tx.list("job", limit=500))
    assert domain.decide(sam, linked["id"], case["id"], False) == original_operation
    with domain.store.atomic(sam["workspace_id"]) as tx:
        assert len(tx.list("job", limit=500)) == linked_jobs
        assert not [
            job for job in tx.list("job", limit=500) if job["kind"] == "already_linked"
        ]
        _, quota = domain.quota_record(tx, sam)
        assert quota["reasoning"] == reasoning_before + 1
    with pytest.raises(DomainError) as changed_outcome:
        domain.decide(sam, linked["id"], None, True)
    assert changed_outcome.value.code == "OBSERVATION_LINKED"


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
    sam = client.post(
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": p["workspace_id"]},
    ).json()
    obs, _ = analyze(domain, sam)
    original_link = domain.decide(sam, obs["id"], first["id"], False)
    run(domain, sam, original_link)
    domain.unlink_observation(sam, obs["id"])
    assert domain.incident_detail(p, first["id"])["observation_count"] == 1
    with domain.store.atomic(p["workspace_id"]) as tx:
        current = tx.get("observation", obs["id"])
        assert (
            current["incident_id"] is None and current["version"] == obs["version"] + 1
        )
        assert len(tx.list("attempt")) == 0
        assert any(
            e["type"] == "OBSERVATION_UNLINKED" for e in tx.list("event:" + first["id"])
        )
    relink = domain.decide(sam, obs["id"], first["id"], False)
    assert relink != original_link
    run(domain, sam, relink)
    assert domain.incident_detail(p, first["id"])["observation_count"] == 2
    with domain.store.atomic(p["workspace_id"]) as tx:
        assert len(tx.list("attempt")) == 0


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
    public_card = next(
        item
        for item in client.get("/api/incidents").json()["items"]
        if item["id"] == case["id"]
    )
    assert public_card["thumbnail_url"] == (
        "/api/evidence/" + evidence["id"] + "?public=true"
    )
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


def test_nonowners_receive_only_approximate_location_and_public_safe_agent_data(system):
    domain, client, alex = system
    latitude, longitude = 47.615432, -122.335432
    case, _ = make_case(domain, alex, latitude=latitude, longitude=longitude)
    private_marker = "PRIVATE PHOTO DETAIL"
    private_tools = {
        "private_visual_read",
        "private_route_trace",
        "private_coordinator_trace",
    }
    with domain.store.atomic(alex["workspace_id"]) as tx:
        record = tx.get("incident", case["id"])
        record["analysis"]["observed_facts"] = [private_marker]
        record["analysis"]["agent_activity"] = [{"tool": "private_visual_read"}]
        record["routing"]["agent_activity"] = [{"tool": "private_route_trace"}]
        record["coordinator"] = record.get("coordinator") or {}
        record["coordinator"]["agent_activity"] = [
            {"tool": "private_coordinator_trace"}
        ]
        tx.put("incident", case["id"], record)

    sam = client.post(
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": alex["workspace_id"]},
    ).json()
    public_detail = domain.incident_detail(sam, case["id"])
    public_list = next(
        item for item in domain.list_incidents(sam)["items"] if item["id"] == case["id"]
    )
    assert public_detail["analysis"] is None
    assert "agent_activity" not in public_detail["routing"]
    assert private_marker not in json.dumps(public_detail)
    assert private_tools.isdisjoint(
        activity.get("tool") for activity in public_detail["agent_activity"]
    )
    assert public_detail["events"] and public_detail["routing"]["recipient"]
    assert (public_detail["latitude"], public_detail["longitude"]) == (
        round(latitude, 3),
        round(longitude, 3),
    )
    assert (public_list["latitude"], public_list["longitude"]) == (
        round(latitude, 3),
        round(longitude, 3),
    )

    neighbor, _ = analyze(
        domain,
        sam,
        description="I also found broken pavement at this exact curb ramp.",
        latitude=latitude,
        longitude=longitude,
    )
    run(domain, sam, domain.decide(sam, neighbor["id"], case["id"], False))
    member_detail = domain.incident_detail(sam, case["id"])
    member_list = next(
        item
        for item in domain.list_incidents(sam, mine=True)["items"]
        if item["id"] == case["id"]
    )
    assert member_detail["analysis"] is None
    assert private_marker not in json.dumps(member_detail)
    assert (member_detail["latitude"], member_detail["longitude"]) == (
        round(latitude, 3),
        round(longitude, 3),
    )
    assert (member_list["latitude"], member_list["longitude"]) == (
        round(latitude, 3),
        round(longitude, 3),
    )

    owner_detail = domain.incident_detail(alex, case["id"])
    owner_list = next(
        item
        for item in domain.list_incidents(alex, mine=True)["items"]
        if item["id"] == case["id"]
    )
    assert owner_detail["analysis"]["observed_facts"] == [private_marker]
    assert private_tools.issubset(
        activity.get("tool") for activity in owner_detail["agent_activity"]
    )
    assert (owner_detail["latitude"], owner_detail["longitude"]) == (
        latitude,
        longitude,
    )
    assert (owner_list["latitude"], owner_list["longitude"]) == (
        latitude,
        longitude,
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


def test_model_only_question_cannot_block_complete_resident_input(system, monkeypatch):
    domain, _, p = system

    class ExtraQuestionModel:
        def analyze(self, *args):
            return {
                "observed_facts": ["A broken pavement edge is visible."],
                "resident_claims": ["The resident described the same nearby defect."],
                "unknowns": ["Exact dimensions remain unknown."],
                "candidate_category": "damaged_sidewalk",
                "missing_information": [
                    "Confirm whether this is the same defect as the nearby candidate."
                ],
                "provenance": "test model",
            }

    obs = domain.create_observation(p, observation())
    switch_fixture_to_cloud(domain, p)
    monkeypatch.setattr(domain, "engine", lambda: ExtraQuestionModel())
    result = run(domain, p, domain.start_analysis(p, obs["id"]))
    analysis = result["result"]["analysis"]
    assert analysis["missing_information"] == []
    assert "same defect" in analysis["unknowns"][-1]
    assert domain.decide(p, obs["id"], None, True)


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


def test_legacy_cognito_claims_are_no_longer_accepted(tmp_path):
    from services.api.store import SQLiteStore

    settings = Settings(
        mode="aws",
        environment="demo",
        data_dir=tmp_path,
        clerk_issuer="https://nf.clerk.accounts.dev",
        authorized_parties=("https://app.example",),
    )
    domain = Domain(settings, store=SQLiteStore(tmp_path / "auth-test.sqlite3"))
    app = create_app(settings, domain)

    class LegacyGateway:
        async def __call__(self, scope, receive, send):
            scope["aws.event"] = {
                "requestContext": {"authorizer": {"claims": {"sub": "cognito-subject"}}}
            }
            await app(scope, receive, send)

    assert TestClient(LegacyGateway()).get("/api/session").status_code == 401


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
    switch_fixture_to_cloud(domain, p)
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
        switch_fixture_to_cloud(domain, p)

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
