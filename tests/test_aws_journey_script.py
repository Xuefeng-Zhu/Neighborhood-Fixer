"""No live AWS execution: validate the gated journey driver with HTTP/SDK doubles."""

import json

import httpx
import pytest

from scripts.verify_aws_journey import (
    Journey,
    JourneyError,
    approved_hash,
    load_config,
    main,
)


def configuration():
    return {
        "api_base": "https://example.execute-api.us-west-2.amazonaws.com",
        "region": "us-west-2",
        "stack_name": "NeighborhoodFixer",
        "expected_workspace_id": "approved-workspace",
        "token_a": "private-token-a",
        "token_b": "private-token-b",
    }


def clients_and_operator(*, lost_approval_response=False):
    state = {
        "uploads": 0,
        "created": False,
        "linked": False,
        "submitted": False,
        "closed": False,
        "fixed": False,
        "approvals": 0,
        "operator_writes": 0,
    }
    iid = "case-one"
    draft = {
        "id": "draft-one",
        "revision": 1,
        "recipient": "Demo Borough Public Works",
        "category": "damaged_sidewalk",
        "description": "Reviewed illustrative curb ramp report.",
        "location_label": "Maple & Alder · fictional Demo Borough",
        "latitude": 47.615,
        "longitude": -122.335,
        "contact": {},
        "attachment_ids": ["evidence-1"],
        "attachment_hashes": ["a" * 64],
    }
    draft["payload_hash"] = approved_hash(draft)

    def analysis(who):
        return {
            "analysis": {
                "provenance": "Amazon Bedrock through Strands; not a fixture",
                "agent_activity": [{"tool": "read_authorized_evidence"}],
                "missing_information": [],
                "unknowns": ["Actual measurements are unknown."],
            },
            "duplicate_candidates": [{"id": iid}] if who == "b" else [],
        }

    def current_case():
        return {
            "id": iid,
            "observation_count": 2 if state["linked"] else 1,
            "following": True,
            "draft": draft,
            "submission_status": "RECEIPT_CONFIRMED"
            if state["submitted"]
            else "AWAITING_APPROVAL",
            "agency_status": "CLOSED" if state["closed"] else "RECEIVED",
            "resolution_status": "RESIDENT_CONFIRMED_FIXED"
            if state["fixed"]
            else "VERIFICATION_REQUESTED"
            if state["closed"]
            else "UNVERIFIED",
            "ticket": {
                "receipt_id": "DB-123456789ABC",
                "url": "https://private-receipt?access=secret-capability",
                "steps": ["Exact approved form submitted", "Receipt captured"],
                "screenshot_url": "/api/evidence/private-screenshot",
            }
            if state["submitted"]
            else None,
        }

    def handler(request):
        who = (
            "a"
            if request.headers.get("authorization") == "Bearer private-token-a"
            else "b"
        )
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(
                200,
                json={
                    "mode": "aws",
                    "status": "ok",
                    "integrations": {
                        key: {"provider": provider, "status": "configured"}
                        for key, provider in {
                            "model_provider": "Amazon Bedrock via Strands",
                            "agent_runtime": "AgentCore Runtime",
                            "browser_provider": "AgentCore Browser",
                            "storage": "DynamoDB + private S3",
                        }.items()
                    },
                },
            )
        if path.startswith("/api/evidence/"):
            assert who == "b"
            return httpx.Response(403, json={"error": {"code": "EVIDENCE_FORBIDDEN"}})
        if path == "/api/session":
            return httpx.Response(
                200,
                json={
                    "mode": "aws",
                    "workspace_id": "approved-workspace",
                    "user": {"id": "resident-" + who},
                },
            )
        if path == "/api/incidents":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": iid,
                            "category": "damaged_sidewalk",
                            "latitude": 47.615,
                            "longitude": -122.335,
                        }
                    ]
                    if state["created"]
                    else [],
                    "next_cursor": None,
                },
            )
        if path == "/api/uploads":
            state["uploads"] += 1
            return httpx.Response(
                201,
                json={"id": "evidence-" + str(state["uploads"]), "sha256": "a" * 64},
            )
        if path == "/api/observations":
            return httpx.Response(201, json={"id": "observation-" + who})
        if path.endswith("/analyze"):
            return httpx.Response(202, json={"operation_id": "analysis-" + who})
        if path.endswith("/decision"):
            if who == "a":
                state["created"] = True
            else:
                state["linked"] = True
            return httpx.Response(202, json={"operation_id": "decision-" + who})
        if path.endswith("/approve"):
            state["approvals"] += 1
            state["submitted"] = True
            payload = json.loads(request.content)
            assert (
                payload["payload_hash"] == draft["payload_hash"]
                and not payload["share_evidence"]
            )
            if lost_approval_response:
                raise httpx.ReadTimeout(
                    "private-token-a must never leak", request=request
                )
            return httpx.Response(202, json={"operation_id": "submission"})
        if path.startswith("/api/operations/"):
            op = path.rsplit("/", 1)[-1]
            result = (
                analysis(op[-1])
                if op.startswith("analysis-")
                else {"incident_id": iid, "submission_status": "RECEIPT_CONFIRMED"}
                if op == "submission"
                else {"incident_id": iid}
            )
            return httpx.Response(200, json={"status": "completed", "result": result})
        if path == "/api/incidents/" + iid:
            case = current_case()
            if who == "b":
                case["draft"] = None
            return httpx.Response(200, json=case)
        if path.endswith("/verify"):
            payload = json.loads(request.content)
            assert payload["evidence_id"] == "evidence-3"
            state["fixed"] = True
            return httpx.Response(
                200, json={"resolution_status": "RESIDENT_CONFIRMED_FIXED"}
            )
        if path == "/api/notifications":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "incident_id": iid,
                            "message": "A resident confirmed this looks fixed.",
                        }
                    ]
                },
            )
        pytest.fail("Unexpected endpoint " + path)

    clients = {
        who: httpx.Client(
            base_url=configuration()["api_base"],
            headers={"Authorization": "Bearer private-token-" + who},
            transport=httpx.MockTransport(handler),
        )
        for who in ("a", "b")
    }

    def operator(**kwargs):
        if not kwargs.get("confirm"):
            return {"portal": "https://portal.lambda-url.us-west-2.on.aws"}
        state["operator_writes"] += 1
        state["closed"] = True
        return {"agency_status": "CLOSED"}

    def portal(request):
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "mode": "aws",
                "environment": "demo",
                "destination": "fictional",
                "status_management_enabled": True,
            },
        )

    return clients, operator, httpx.Client(transport=httpx.MockTransport(portal)), state


def test_gated_driver_completes_exact_single_report_and_redacts_report(tmp_path):
    clients, operator, portal, state = clients_and_operator()
    path = tmp_path / "result.json"
    result = Journey(
        configuration(),
        path,
        clients=clients,
        operator=operator,
        portal_client=portal,
        sleeper=lambda _: None,
    ).run()
    assert (
        result["status"] == "passed"
        and state["approvals"] == state["operator_writes"] == 1
    )
    assert (
        result["checks"]["closure_does_not_mean_fixed"]["resolution_status"]
        == "VERIFICATION_REQUESTED"
    )
    assert result["checks"]["both_followers_see_resident_verification"]
    report = path.read_text()
    assert (
        "private-token" not in report
        and "secret-capability" not in report
        and "private-screenshot" not in report
    )
    assert path.stat().st_mode & 0o777 == 0o600


def test_uncertain_approval_response_stops_without_retry_or_second_report(tmp_path):
    clients, operator, portal, state = clients_and_operator(lost_approval_response=True)
    path = tmp_path / "uncertain.json"
    with pytest.raises(JourneyError) as error:
        Journey(
            configuration(),
            path,
            clients=clients,
            operator=operator,
            portal_client=portal,
            sleeper=lambda _: None,
        ).run()
    assert error.value.code == "WRITE_RESPONSE_UNCERTAIN"
    assert state["approvals"] == 1 and state["operator_writes"] == 0
    report = json.loads(path.read_text())
    assert report["status"] == "needs_review" and report["incident_id"] == "case-one"
    assert "private-token" not in path.read_text()


def test_stop_before_approval_preserves_case_without_submission_or_closure(tmp_path):
    clients, operator, portal, state = clients_and_operator()
    path = tmp_path / "paused.json"
    result = Journey(
        configuration(),
        path,
        clients=clients,
        operator=operator,
        portal_client=portal,
        sleeper=lambda _: None,
        stop_before_approval=True,
    ).run()
    assert result["status"] == result["phase"] == "paused_before_approval"
    assert state["created"] and state["linked"] and state["uploads"] == 2
    assert state["approvals"] == state["operator_writes"] == 0
    assert not state["submitted"] and not state["closed"] and not state["fixed"]
    assert result["checks"]["real_analysis_a"]["provenance_verified"]
    assert result["checks"]["real_analysis_b"]["provenance_verified"]
    assert result["checks"]["neighbor_cannot_read_private_photo_or_draft"]
    assert result["checks"]["two_observations_one_immutable_draft"]
    assert result["incident_id"] == "case-one"
    assert result["observation_a"] == "observation-a"
    assert result["observation_b"] == "observation-b"
    assert result["draft_id"] == "draft-one" and result["draft_revision"] == 1
    assert result["draft_payload_hash"]
    assert "approved_payload_hash" not in result
    assert "submission_operation_id" not in result and "receipt_id" not in result
    assert not any(
        item["action"] in {"approve", "verify"} for item in result["write_requests"]
    )
    assert "does not resume or replay" in result["resume_guidance"]
    assert "private-token" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600

    # A fresh filename is not a way to bypass the persisted-case review boundary.
    with pytest.raises(JourneyError) as error:
        Journey(
            configuration(),
            tmp_path / "fresh-output.json",
            clients=clients,
            operator=operator,
            portal_client=portal,
            stop_before_approval=True,
        ).run()
    assert error.value.code == "EXISTING_CASE_REQUIRES_REVIEW"
    assert state["uploads"] == 2 and state["approvals"] == state["operator_writes"] == 0


def test_stop_before_approval_cli_remains_preview_without_confirm(tmp_path, capsys):
    config_path = tmp_path / "private.json"
    config_path.write_text(json.dumps(configuration()))
    config_path.chmod(0o600)
    report_path = tmp_path / "not-created.json"
    main(
        [
            "--config",
            str(config_path),
            "--report",
            str(report_path),
            "--stop-before-approval",
        ]
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview" and preview["stop_before_approval"] is True
    assert not report_path.exists()
    assert not any(
        "Capture the actual browser receipt" in action
        or "Close the fictional ticket" in action
        for action in preview["actions"]
    )


def test_existing_report_cannot_be_automatically_replayed(tmp_path):
    path = tmp_path / "existing.json"
    path.write_text('{"incident_id":"existing-case"}')
    clients, operator, portal, state = clients_and_operator()
    with pytest.raises(JourneyError, match="already exists"):
        Journey(
            configuration(),
            path,
            clients=clients,
            operator=operator,
            portal_client=portal,
        ).run()
    assert json.loads(path.read_text()) == {"incident_id": "existing-case"}
    assert state["approvals"] == 0


def test_private_cognito_session_files_are_scope_and_expiry_checked(tmp_path):
    config = configuration()
    config.pop("token_a")
    config.pop("token_b")
    for who in ("a", "b"):
        session = {
            "schema_version": 1,
            "api_origin": config["api_base"],
            "workspace_id": config["expected_workspace_id"],
            "user_id": "resident-" + who,
            "access_token": "private-" + who,
            "expires_at": "2099-01-01T00:00:00Z",
        }
        path = tmp_path / (who + ".json")
        path.write_text(json.dumps(session))
        path.chmod(0o600)
        config["session_" + who + "_file"] = path.name
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    loaded = load_config(path)
    assert loaded["token_a"] == "private-a" and loaded["token_b"] == "private-b"
    session = json.loads((tmp_path / "b.json").read_text())
    session["workspace_id"] = "unauthorized-workspace"
    (tmp_path / "b.json").write_text(json.dumps(session))
    with pytest.raises(JourneyError, match="scoped"):
        load_config(path)


def test_config_rejects_insecure_permissions_and_destination(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(configuration()))
    path.chmod(0o644)
    with pytest.raises(JourneyError, match="0600"):
        load_config(path)
    config = configuration()
    config["api_base"] = "https://arbitrary.example"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    with pytest.raises(JourneyError, match="API Gateway"):
        load_config(path)
