import asyncio
import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from services.api import fixtures, outreach
from services.api.config import Settings
from services.api.domain import Domain, DomainError, digest, iso
from services.api.main import create_app


def _post(client, url, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if any(
        marker in url
        for marker in (
            "/jurisdiction-preview",
            "/contact-research",
            "/contact-selection",
            "/outreach/email/",
            "/outreach/voice/",
        )
    ):
        headers.setdefault("Idempotency-Key", str(uuid.uuid4()))
    if headers:
        kwargs["headers"] = headers
    return client.post(url, **kwargs)


@pytest.fixture
def outreach_system(tmp_path, monkeypatch):
    monkeypatch.setenv("NF_MODE", "local")
    monkeypatch.setenv("NF_ENVIRONMENT", "development")
    settings = Settings(data_dir=tmp_path, lease_seconds=1)
    domain = Domain(settings)
    client = TestClient(create_app(settings, domain))
    principal = _post(client, "/api/demo/session", json={"resident": "alex"}).json()
    observation = domain.create_observation(
        principal,
        {
            "description": "A deep pothole is disrupting the marked public crossing.",
            "category": "pothole",
            "latitude": 47.615,
            "longitude": -122.335,
            "location_label": "Cedar Street at 4th Avenue, Seattle",
            "location_confirmed": True,
            "asset_public": "yes",
            "evidence_ids": [],
            "share_public": True,
            "share_evidence": False,
        },
    )
    analysis_id = domain.start_analysis(principal, observation["id"])
    asyncio.run(domain.run_job(principal["workspace_id"], analysis_id))
    decision_id = domain.decide(principal, observation["id"], None, True)
    asyncio.run(domain.run_job(principal["workspace_id"], decision_id))
    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident_id = tx.get("operation", decision_id)["result"]["incident_id"]
    yield domain, client, principal, incident_id
    client.close()


def _research(client, incident_id):
    candidate_response = _post(
        client, f"/api/incidents/{incident_id}/jurisdiction-preview"
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()
    research_response = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": candidate["id"],
            "context_hash": candidate["context_hash"],
            "confirmed": True,
        },
    )
    assert research_response.status_code == 201, research_response.text
    return candidate, research_response.json()


def _select_contact(client, incident_id):
    candidate, research = _research(client, incident_id)
    contact = research["contacts"][0]
    selected = _post(
        client,
        f"/api/incidents/{incident_id}/contact-selection",
        json={"research_id": research["id"], "contact_id": contact["id"]},
    )
    assert selected.status_code == 201, selected.text
    return candidate, research, contact, selected.json()


def _start_voice(client, incident_id, run_key=None):
    envelope = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/envelope"
    ).json()
    body = {
        "envelope_id": envelope["id"],
        "payload_hash": envelope["payload_hash"],
    }
    approved = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/approve", json=body
    )
    assert approved.status_code == 200, approved.text
    headers = {"Idempotency-Key": run_key} if run_key else {}
    response = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/run",
        json=body,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return envelope, body, response.json()


def _force_voice_generation_state(domain, principal, run_id, deadline_at):
    with domain.store.atomic(principal["workspace_id"]) as tx:
        run = tx.get("voice_run", run_id)
        envelope = tx.get("voice_envelope", run["envelope_id"])
        envelope_request_id = envelope["request_id"]
        run.update(
            status="GENERATING",
            provider_deadline_at=iso(deadline_at),
            provider_claim_id="provider-claim",
            provider_claimed_at=deadline_at - 1,
            playback_token="private-raw-token",
            facts=[{"id": "private", "value": "private fact"}],
            research_reference={"phone": "private phone"},
            selected_contact={"email": "private email"},
            allowed_intents={"private": ["intent"]},
            refusal_rules=["private rule"],
            turns=[{"caption": "private caption"}],
            transcript="private transcript",
            captions=["private caption"],
            audio="private audio",
        )
        envelope["status"] = "GENERATING"
        tx.put("voice_run", run["id"], run)
        tx.put("voice_envelope", envelope["id"], envelope)
    return envelope["id"], envelope_request_id


def _assert_voice_generation_scrubbed(
    domain, principal, run_id, envelope_id, envelope_request_id
):
    private_fields = {
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
    }
    with domain.store.atomic(principal["workspace_id"]) as tx:
        run = tx.get("voice_run", run_id)
        envelope = tx.get("voice_envelope", envelope_id)
        request = tx.get("outreach_request", envelope_request_id)
        incident = tx.get("incident", run["incident_id"])
    assert run["status"] == "FAILED"
    assert private_fields.isdisjoint(run)
    assert set(envelope) == {
        "id",
        "workspace_id",
        "incident_id",
        "owner_id",
        "revision",
        "payload_hash",
        "execution_target",
        "created_at",
        "status",
    }
    assert envelope["status"] == "FAILED"
    assert request["status"] == "ERASED"
    assert "outreach_voice_envelope_id" not in incident
    assert (
        outreach.get_voice_session(
            domain.settings, domain.store, principal["workspace_id"], run_id
        )
        is None
    )


def test_owner_completes_research_email_and_voice_simulations(outreach_system):
    domain, client, principal, incident_id = outreach_system
    snapshot = client.get(f"/api/incidents/{incident_id}/outreach")
    assert snapshot.status_code == 200
    assert snapshot.json()["available"] is True

    candidate, research = _research(client, incident_id)
    assert candidate["display_name"].startswith("Seattle")
    assert candidate["supported"] is True
    assert research["query_scope"] == {
        "jurisdiction": "Seattle, WA",
        "category": "pothole",
    }
    assert research["selected_contact_id"] is None
    assert 1 <= len(research["contacts"]) <= 3
    with domain.store.atomic(principal["workspace_id"]) as tx:
        durable_research = tx.get("contact_research", research["id"])
        assert "contacts" not in durable_research
    assert outreach.get_contact_candidates(
        domain.settings, domain.store, principal["workspace_id"], research["id"]
    )
    contact = research["contacts"][0]
    assert contact["source_url"].startswith("https://")
    assert not contact["source_url"].startswith(("mailto:", "tel:"))

    selected_response = _post(
        client,
        f"/api/incidents/{incident_id}/contact-selection",
        json={"research_id": research["id"], "contact_id": contact["id"]},
    )
    assert selected_response.status_code == 201
    selected = selected_response.json()
    assert selected["contact_id"] == contact["id"]
    retained_research = client.get(f"/api/incidents/{incident_id}/outreach").json()[
        "research"
    ]
    assert [item["id"] for item in retained_research["contacts"]] == [contact["id"]]

    email_draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    assert email_draft["execution_target"] == "internal-email-simulator-v1"
    assert email_draft["research_reference"]["email"] == contact["email"]
    approval = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/email/approve",
        json={
            "draft_id": email_draft["id"],
            "payload_hash": email_draft["payload_hash"],
        },
    )
    assert approval.status_code == 200, approval.text
    run_body = {
        "draft_id": email_draft["id"],
        "payload_hash": email_draft["payload_hash"],
    }
    receipt = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/run", json=run_body
    ).json()
    duplicate = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/run", json=run_body
    ).json()
    assert receipt == duplicate
    assert receipt["status"] == "SIMULATED_NOT_SENT"
    assert receipt["execution_target"] == "internal-email-simulator-v1"

    envelope = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/envelope"
    ).json()
    assert envelope["max_turns"] == 6
    assert envelope["execution_target"] == "internal-voice-simulator-v1"
    assert "reporting_agent" in envelope["allowed_intents"]
    voice_body = {
        "envelope_id": envelope["id"],
        "payload_hash": envelope["payload_hash"],
    }
    assert (
        _post(
            client,
            f"/api/incidents/{incident_id}/outreach/voice/approve",
            json=voice_body,
        ).status_code
        == 200
    )
    voice_run_response = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/run", json=voice_body
    )
    assert voice_run_response.status_code == 200, voice_run_response.text
    voice_run = voice_run_response.json()
    assert voice_run["status"] == "RUNNING"
    playback_token = voice_run.pop("playback_token")
    assert len(playback_token) >= 32
    reloaded = client.get(f"/api/incidents/{incident_id}/outreach").json()
    assert "turns" not in reloaded["voice_run"]
    assert "playback_token" not in reloaded["voice_run"]
    status_url = f"/api/incidents/{incident_id}/outreach/voice/runs/{voice_run['id']}"
    assert client.get(status_url).status_code == 422
    live = client.get(status_url, headers={"X-NF-Playback-Token": playback_token})
    assert live.status_code == 200, live.text
    assert live.headers["cache-control"] == "no-store, max-age=0"
    turns = live.json()["turns"]
    assert len(turns) == 6
    assert all(turn["audio_url"].startswith("/api/") for turn in turns)
    audio = client.get(
        turns[0]["audio_url"],
        headers={"X-NF-Playback-Token": playback_token},
    )
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content.startswith(b"RIFF")
    second_audio = client.get(
        turns[0]["audio_url"],
        headers={"X-NF-Playback-Token": playback_token},
    )
    assert second_audio.status_code == 200
    limited = client.get(
        turns[0]["audio_url"],
        headers={"X-NF-Playback-Token": playback_token},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "VOICE_AUDIO_LIMIT_REACHED"
    for turn in turns[1:]:
        served = client.get(
            turn["audio_url"],
            headers={"X-NF-Playback-Token": playback_token},
        )
        assert served.status_code == 200

    ended = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/runs/{voice_run['id']}/end",
        json={"reason": "completed"},
    )
    assert ended.status_code == 200, ended.text
    voice_receipt = ended.json()
    assert voice_receipt["status"] == "SIMULATED_NOT_DIALED"
    assert voice_receipt["run_status"] == "COMPLETED"
    assert voice_receipt["turn_count"] == 6
    assert "not saved" in voice_receipt["summary"]
    assert (
        client.get(
            turns[0]["audio_url"],
            headers={"X-NF-Playback-Token": playback_token},
        ).status_code
        == 410
    )

    final = client.get(f"/api/incidents/{incident_id}/outreach").json()
    assert final["email_receipt"]["status"] == "SIMULATED_NOT_SENT"
    assert final["voice_receipt"]["status"] == "SIMULATED_NOT_DIALED"
    assert final["voice_receipt"]["run_status"] == "COMPLETED"
    assert "turns" not in final["voice_run"]
    with domain.store.atomic(principal["workspace_id"]) as tx:
        stored_run = tx.get("voice_run", voice_run["id"])
        assert "playback_token" not in stored_run
        assert "playback_token_hash" not in stored_run
        stored_envelope = tx.get("voice_envelope", envelope["id"])
        assert stored_envelope["payload_hash"] == envelope["payload_hash"]
        for private_field in (
            "facts",
            "research_reference",
            "selected_contact",
            "allowed_intents",
            "refusal_rules",
        ):
            assert private_field not in stored_envelope
        event_text = " ".join(
            event["message"] for event in tx.list("event:" + incident_id)
        )
    assert (
        outreach.get_voice_session(
            domain.settings,
            domain.store,
            principal["workspace_id"],
            voice_run["id"],
        )
        is None
    )
    assert contact["email"] not in event_text
    assert contact["phone"] not in event_text


def test_voice_run_idempotency_rotates_only_the_ephemeral_capability(
    outreach_system,
):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    run_key = str(uuid.uuid4())
    _, body, first = _start_voice(client, incident_id, run_key)
    second_response = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/run",
        json=body,
        headers={"Idempotency-Key": run_key},
    )
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert second["id"] == first["id"]
    assert second["playback_token"] != first["playback_token"]
    status_url = f"/api/incidents/{incident_id}/outreach/voice/runs/{first['id']}"
    assert (
        client.get(
            status_url,
            headers={"X-NF-Playback-Token": first["playback_token"]},
        ).status_code
        == 200
    )
    assert (
        client.get(
            status_url,
            headers={"X-NF-Playback-Token": second["playback_token"]},
        ).status_code
        == 200
    )
    with domain.store.atomic(principal["workspace_id"]) as tx:
        assert len(tx.list("voice_run")) == 1
        stored = tx.get("voice_run", first["id"])
        assert "playback_token" not in stored
        assert len(stored["playback_token_hashes"]) == 2
        assert all(len(value) == 64 for value in stored["playback_token_hashes"])


def test_voice_playback_expires_at_server_limit_and_purges(outreach_system):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    _, _, run = _start_voice(client, incident_id)
    token = run["playback_token"]
    status_url = f"/api/incidents/{incident_id}/outreach/voice/runs/{run['id']}"
    live = client.get(status_url, headers={"X-NF-Playback-Token": token}).json()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        stored = tx.get("voice_run", run["id"])
        stored.update(started_epoch=time.time() - 91, started_at="expired")
        tx.put("voice_run", stored["id"], stored)
    expired = client.get(
        live["turns"][0]["audio_url"],
        headers={"X-NF-Playback-Token": token},
    )
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "VOICE_SESSION_EXPIRED"
    snapshot = client.get(f"/api/incidents/{incident_id}/outreach").json()
    assert snapshot["voice_receipt"]["status"] == "SIMULATED_NOT_DIALED"
    assert snapshot["voice_receipt"]["run_status"] == "INTERRUPTED"
    assert (
        outreach.get_voice_session(
            domain.settings, domain.store, principal["workspace_id"], run["id"]
        )
        is None
    )


def test_failed_voice_generation_scrubs_private_state_and_transient_session(
    outreach_system,
):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    _, _, run = _start_voice(client, incident_id)
    envelope_id, request_id = _force_voice_generation_state(
        domain, principal, run["id"], time.time() + 300
    )

    result = domain.fail_voice_generation(principal["workspace_id"], run["id"])

    assert result["status"] == "FAILED"
    _assert_voice_generation_scrubbed(
        domain, principal, run["id"], envelope_id, request_id
    )


def test_voice_generation_snapshot_timeout_scrubs_private_state_and_session(
    outreach_system,
):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    _, _, run = _start_voice(client, incident_id)
    envelope_id, request_id = _force_voice_generation_state(
        domain, principal, run["id"], time.time() - 1
    )

    response = client.get(f"/api/incidents/{incident_id}/outreach")

    assert response.status_code == 200, response.text
    assert response.json()["voice_run"]["status"] == "FAILED"
    assert "voice_envelope" not in response.json()
    _assert_voice_generation_scrubbed(
        domain, principal, run["id"], envelope_id, request_id
    )


def test_in_flight_audio_stops_at_hard_limit_without_serving_last_turn(
    outreach_system, monkeypatch
):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    _, _, run = _start_voice(client, incident_id)
    token = run["playback_token"]
    session = outreach.get_voice_session(
        domain.settings, domain.store, principal["workspace_id"], run["id"]
    )
    turn_id = session["turns"][0]["id"]
    base_time = time.time()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        stored = tx.get("voice_run", run["id"])
        stored.update(started_epoch=base_time - 89, started_at=iso(base_time - 89))
        tx.put("voice_run", stored["id"], stored)

    clock = {"now": base_time}
    source_closed = {"value": False}

    def source():
        try:
            yield b"first-chunk"
            clock["now"] = base_time + 2
            yield b"must-not-be-served"
        finally:
            source_closed["value"] = True

    monkeypatch.setattr(
        outreach,
        "synthesize_audio_stream",
        lambda *args, **kwargs: (source(), "audio/mpeg"),
    )
    monkeypatch.setattr("services.api.domain.time.time", lambda: clock["now"])

    stream, media_type = domain.voice_turn_audio(
        principal, incident_id, run["id"], turn_id, token
    )
    chunks = list(stream)

    assert media_type == "audio/mpeg"
    assert chunks == [b"first-chunk"]
    assert source_closed["value"] is True
    with domain.store.atomic(principal["workspace_id"]) as tx:
        stored = tx.get("voice_run", run["id"])
        receipt = tx.get("simulation_receipt", stored["receipt_id"])
    assert stored["status"] == "INTERRUPTED"
    assert "played_turn_ids" not in stored
    assert receipt["run_status"] == "INTERRUPTED"
    assert receipt["duration_seconds"] == 90
    assert (
        outreach.get_voice_session(
            domain.settings, domain.store, principal["workspace_id"], run["id"]
        )
        is None
    )


def test_research_changes_are_blocked_while_voice_session_is_active(
    outreach_system,
):
    _, client, _, incident_id = outreach_system
    candidate, research, contact, _ = _select_contact(client, incident_id)
    _, _, run = _start_voice(client, incident_id)
    blocked_preview = _post(
        client, f"/api/incidents/{incident_id}/jurisdiction-preview"
    )
    assert blocked_preview.status_code == 409
    assert blocked_preview.json()["error"]["code"] == "VOICE_SIMULATION_ACTIVE"
    blocked_research = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": candidate["id"],
            "context_hash": candidate["context_hash"],
            "confirmed": True,
        },
    )
    assert blocked_research.status_code == 409
    blocked_selection = _post(
        client,
        f"/api/incidents/{incident_id}/contact-selection",
        json={"research_id": research["id"], "contact_id": contact["id"]},
    )
    assert blocked_selection.status_code == 409
    ended = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/runs/{run['id']}/end",
        json={"reason": "resident"},
    )
    assert ended.status_code == 200


def test_outreach_is_owner_only_and_requires_explicit_current_selection(
    outreach_system,
):
    _domain, client, principal, incident_id = outreach_system
    _, research = _research(client, incident_id)
    missing = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    switched = _post(
        client,
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": principal["workspace_id"]},
    )
    assert switched.status_code == 200
    forbidden = client.get(f"/api/incidents/{incident_id}/outreach")
    assert forbidden.status_code == 403
    assert research["contacts"][0]["email"] not in forbidden.text


def test_followers_cannot_read_drafts_contacts_captions_or_summaries(
    outreach_system,
):
    _, client, principal, incident_id = outreach_system
    _, _, contact, _ = _select_contact(client, incident_id)
    draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    email_body = {
        "draft_id": draft["id"],
        "payload_hash": draft["payload_hash"],
    }
    assert (
        _post(
            client,
            f"/api/incidents/{incident_id}/outreach/email/approve",
            json=email_body,
        ).status_code
        == 200
    )
    receipt = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/run", json=email_body
    ).json()
    _, _, run = _start_voice(client, incident_id)
    status_url = f"/api/incidents/{incident_id}/outreach/voice/runs/{run['id']}"
    live = client.get(
        status_url,
        headers={"X-NF-Playback-Token": run["playback_token"]},
    ).json()
    audio_url = live["turns"][0]["audio_url"]
    caption = live["turns"][0]["caption"]

    switched = _post(
        client,
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": principal["workspace_id"]},
    )
    assert switched.status_code == 200
    assert client.get(f"/api/incidents/{incident_id}/outreach").status_code == 403
    assert (
        _post(
            client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
        ).status_code
        == 403
    )
    assert (
        client.get(
            status_url,
            headers={"X-NF-Playback-Token": run["playback_token"]},
        ).status_code
        == 403
    )
    assert (
        client.get(
            audio_url,
            headers={"X-NF-Playback-Token": run["playback_token"]},
        ).status_code
        == 403
    )
    public_detail = client.get(f"/api/incidents/{incident_id}")
    assert public_detail.status_code == 200
    exposed = public_detail.text
    for private_value in (
        contact["email"],
        contact["phone"],
        draft["body"],
        caption,
        receipt["summary"],
    ):
        assert private_value not in exposed

    _post(
        client,
        "/api/demo/session",
        json={"resident": "alex", "workspace_id": principal["workspace_id"]},
    )
    ended = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/runs/{run['id']}/end",
        json={"reason": "resident"},
    )
    assert ended.status_code == 200


def test_retry_keys_replay_preview_draft_and_envelope(outreach_system):
    _, client, _, incident_id = outreach_system
    preview_key = str(uuid.uuid4())
    preview_headers = {"Idempotency-Key": preview_key}
    first_preview = _post(
        client,
        f"/api/incidents/{incident_id}/jurisdiction-preview",
        headers=preview_headers,
    )
    second_preview = _post(
        client,
        f"/api/incidents/{incident_id}/jurisdiction-preview",
        headers=preview_headers,
    )
    assert first_preview.status_code == second_preview.status_code == 201
    assert first_preview.json() == second_preview.json()
    candidate = first_preview.json()
    research = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": candidate["id"],
            "context_hash": candidate["context_hash"],
            "confirmed": True,
        },
    ).json()
    contact = research["contacts"][0]
    assert (
        _post(
            client,
            f"/api/incidents/{incident_id}/contact-selection",
            json={"research_id": research["id"], "contact_id": contact["id"]},
        ).status_code
        == 201
    )

    draft_key = str(uuid.uuid4())
    draft_headers = {"Idempotency-Key": draft_key}
    first_draft = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/email/draft",
        json={},
        headers=draft_headers,
    )
    second_draft = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/email/draft",
        json={},
        headers=draft_headers,
    )
    assert first_draft.status_code == second_draft.status_code == 201
    assert first_draft.json() == second_draft.json()
    rejected_override = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/email/draft",
        json={"subject": "A different internal simulation subject"},
        headers=draft_headers,
    )
    assert rejected_override.status_code == 422

    envelope_key = str(uuid.uuid4())
    envelope_headers = {"Idempotency-Key": envelope_key}
    first_envelope = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/envelope",
        headers=envelope_headers,
    )
    second_envelope = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/envelope",
        headers=envelope_headers,
    )
    assert first_envelope.status_code == second_envelope.status_code == 201
    assert first_envelope.json() == second_envelope.json()


def test_fresh_preview_keys_reuse_current_candidate(outreach_system, monkeypatch):
    _, client, _, incident_id = outreach_system
    calls = 0
    original = outreach.reverse_geocode

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(outreach, "reverse_geocode", counted)
    first = _post(
        client,
        f"/api/incidents/{incident_id}/jurisdiction-preview",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    second = _post(
        client,
        f"/api/incidents/{incident_id}/jurisdiction-preview",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert calls == 1


def test_pending_jurisdiction_lookup_coalesces_concurrent_keys(
    outreach_system, monkeypatch
):
    _, client, _, incident_id = outreach_system
    original = outreach.reverse_geocode
    nested_responses = []

    def interleaved(*args, **kwargs):
        pending_snapshot = client.get(f"/api/incidents/{incident_id}/outreach")
        assert pending_snapshot.status_code == 200, pending_snapshot.text
        assert "jurisdiction" not in pending_snapshot.json()
        nested_responses.append(
            _post(
                client,
                f"/api/incidents/{incident_id}/jurisdiction-preview",
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(outreach, "reverse_geocode", interleaved)
    response = _post(
        client,
        f"/api/incidents/{incident_id}/jurisdiction-preview",
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    assert len(nested_responses) == 1
    assert nested_responses[0].status_code == 409
    assert nested_responses[0].json()["error"]["code"] == "JURISDICTION_LOOKUP_PENDING"


def test_failed_jurisdiction_placeholder_is_not_projected(outreach_system, monkeypatch):
    _, client, _, incident_id = outreach_system

    def fail(*args, **kwargs):
        raise outreach.OutreachProviderError(
            "JURISDICTION_LOOKUP_FAILED", "lookup failed"
        )

    monkeypatch.setattr(outreach, "reverse_geocode", fail)
    failed = _post(client, f"/api/incidents/{incident_id}/jurisdiction-preview")
    assert failed.status_code == 503
    snapshot = client.get(f"/api/incidents/{incident_id}/outreach")
    assert snapshot.status_code == 200, snapshot.text
    assert "jurisdiction" not in snapshot.json()


def test_explicit_contact_refresh_replaces_and_scrubs_results(outreach_system):
    domain, client, principal, incident_id = outreach_system
    candidate, research, _, _ = _select_contact(client, incident_id)
    refreshed = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": candidate["id"],
            "context_hash": candidate["context_hash"],
            "confirmed": True,
            "refresh": True,
        },
    )
    assert refreshed.status_code == 201, refreshed.text
    assert refreshed.json()["id"] != research["id"]
    assert refreshed.json()["contacts"]
    snapshot = client.get(f"/api/incidents/{incident_id}/outreach").json()
    assert "selection" not in snapshot
    with domain.store.atomic(principal["workspace_id"]) as tx:
        old = tx.get("contact_research", research["id"])
        assert old["status"] == "STALE"
        assert "contacts" not in old
    assert (
        outreach.get_contact_candidates(
            domain.settings, domain.store, principal["workspace_id"], research["id"]
        )
        == []
    )


def test_contact_selection_delete_failure_commits_no_selection_or_request(
    outreach_system, monkeypatch
):
    domain, client, principal, incident_id = outreach_system
    _, research = _research(client, incident_id)
    contact = research["contacts"][0]

    def fail_delete(*args, **kwargs):
        raise RuntimeError("transient deletion failed")

    monkeypatch.setattr(outreach, "delete_contact_candidates", fail_delete)
    with pytest.raises(RuntimeError, match="transient deletion failed"):
        domain.select_contact(
            principal,
            incident_id,
            {"research_id": research["id"], "contact_id": contact["id"]},
            str(uuid.uuid4()),
        )

    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident = tx.get("incident", incident_id)
        selection_requests = [
            request
            for request in tx.list("outreach_request")
            if request.get("action") == "contact_selection"
        ]
        selections = tx.list("contact_selection")
    assert "outreach_contact_selection_id" not in incident
    assert selection_requests == []
    assert selections == []


def test_contact_selection_replay_purges_any_lingering_candidates(outreach_system):
    domain, client, principal, incident_id = outreach_system
    _, research = _research(client, incident_id)
    contact = research["contacts"][0]
    key = str(uuid.uuid4())
    data = {"research_id": research["id"], "contact_id": contact["id"]}

    selected = domain.select_contact(principal, incident_id, data, key)
    assert (
        outreach.get_contact_candidates(
            domain.settings, domain.store, principal["workspace_id"], research["id"]
        )
        == []
    )
    outreach.put_contact_candidates(
        domain.settings,
        domain.store,
        principal["workspace_id"],
        research["id"],
        research["contacts"],
        int(time.time() + 3600),
    )

    replay = domain.select_contact(principal, incident_id, data, key)

    assert replay == selected
    assert (
        outreach.get_contact_candidates(
            domain.settings, domain.store, principal["workspace_id"], research["id"]
        )
        == []
    )


def test_conflicting_idempotency_payload_is_rejected(outreach_system):
    domain, _, principal, incident_id = outreach_system
    key = str(uuid.uuid4())
    with domain.authorized(principal) as tx:
        domain.begin_outreach_request(
            tx, principal, "test_action", incident_id, key, {"revision": 1}
        )
    with pytest.raises(DomainError) as conflict:
        with domain.authorized(principal) as tx:
            domain.begin_outreach_request(
                tx, principal, "test_action", incident_id, key, {"revision": 2}
            )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_email_and_voice_payload_hashes_bind_the_exact_revision(outreach_system):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)

    draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        stored_draft = tx.get("outreach_email_draft", draft["id"])
        stored_draft["revision"] += 1
        assert (
            digest(
                domain._frozen_outreach_payload("outreach_email_draft", stored_draft)
            )
            != draft["payload_hash"]
        )
        tx.put("outreach_email_draft", stored_draft["id"], stored_draft)
    rejected_email = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/email/approve",
        json={"draft_id": draft["id"], "payload_hash": draft["payload_hash"]},
    )
    assert rejected_email.status_code == 409
    assert rejected_email.json()["error"]["code"] == "STALE_OUTREACH_APPROVAL"

    envelope = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/envelope"
    ).json()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        stored_envelope = tx.get("voice_envelope", envelope["id"])
        stored_envelope["revision"] += 1
        assert (
            digest(domain._frozen_outreach_payload("voice_envelope", stored_envelope))
            != envelope["payload_hash"]
        )
        tx.put("voice_envelope", stored_envelope["id"], stored_envelope)
    rejected_voice = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/approve",
        json={
            "envelope_id": envelope["id"],
            "payload_hash": envelope["payload_hash"],
        },
    )
    assert rejected_voice.status_code == 409
    assert rejected_voice.json()["error"]["code"] == "STALE_OUTREACH_APPROVAL"


def test_expired_jurisdiction_never_starts_contact_search(outreach_system):
    domain, client, principal, incident_id = outreach_system
    candidate = _post(
        client, f"/api/incidents/{incident_id}/jurisdiction-preview"
    ).json()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        workspace = tx.get("workspace", principal["workspace_id"])
        workspace["clock_offset"] = 901
        tx.put("workspace", workspace["id"], workspace)
    expired = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": candidate["id"],
            "context_hash": candidate["context_hash"],
            "confirmed": True,
        },
    )
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "JURISDICTION_EXPIRED"
    with domain.store.atomic(principal["workspace_id"]) as tx:
        assert tx.list("contact_research") == []


def test_expired_unselected_research_is_scrubbed(outreach_system):
    domain, client, principal, incident_id = outreach_system
    _, research = _research(client, incident_id)
    assert research["contacts"]
    with domain.store.atomic(principal["workspace_id"]) as tx:
        workspace = tx.get("workspace", principal["workspace_id"])
        workspace["clock_offset"] = 24 * 60 * 60 + 1
        tx.put("workspace", workspace["id"], workspace)
    snapshot = client.get(f"/api/incidents/{incident_id}/outreach")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["research"]["status"] == "EXPIRED"
    assert snapshot.json()["research"]["contacts"] == []


def test_non_seattle_and_stale_case_context_fail_closed(outreach_system):
    domain, client, principal, incident_id = outreach_system
    stale_candidate = _post(
        client, f"/api/incidents/{incident_id}/jurisdiction-preview"
    ).json()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident = tx.get("incident", incident_id)
        incident["description"] = "Case facts changed after jurisdiction lookup."
        tx.put("incident", incident["id"], incident)
    stale = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": stale_candidate["id"],
            "context_hash": stale_candidate["context_hash"],
            "confirmed": True,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_JURISDICTION"

    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident = tx.get("incident", incident_id)
        incident.update(latitude=47.0, longitude=-122.0)
        tx.put("incident", incident["id"], incident)
    outside = _post(client, f"/api/incidents/{incident_id}/jurisdiction-preview")
    assert outside.status_code == 201, outside.text
    assert outside.json()["supported"] is False
    unsupported = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": outside.json()["id"],
            "context_hash": outside.json()["context_hash"],
            "confirmed": True,
        },
    )
    assert unsupported.status_code == 409
    assert unsupported.json()["error"]["code"] == "UNSUPPORTED_JURISDICTION"


def test_snapshot_invalidates_downstream_records_when_case_hash_changes(
    outreach_system,
):
    domain, client, principal, incident_id = outreach_system
    _, research, _, selection = _select_contact(client, incident_id)
    draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    approval = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/email/approve",
        json={"draft_id": draft["id"], "payload_hash": draft["payload_hash"]},
    ).json()
    envelope = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/envelope"
    ).json()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident = tx.get("incident", incident_id)
        incident["description"] = "The resident updated the approved case facts."
        tx.put("incident", incident["id"], incident)
    response = client.get(f"/api/incidents/{incident_id}/outreach")
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["jurisdiction"]["status"] == "STALE"
    assert snapshot["research"]["status"] == "STALE"
    assert "selection" not in snapshot
    assert "email_draft" not in snapshot
    assert "voice_envelope" not in snapshot
    with domain.store.atomic(principal["workspace_id"]) as tx:
        assert tx.get("contact_selection", selection["id"])["status"] == "STALE"
        assert tx.get("outreach_email_draft", draft["id"])["status"] == "STALE"
        assert tx.get("outreach_approval", approval["id"])["status"] == "STALE"
        assert tx.get("voice_envelope", envelope["id"])["status"] == "STALE"
        assert "contacts" not in tx.get("contact_research", research["id"])


def _change_case_facts(domain, principal, incident_id):
    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident = tx.get("incident", incident_id)
        incident["description"] = "The resident changed the case facts after review."
        tx.put("incident", incident_id, incident)


def test_direct_email_approval_and_run_reject_stale_case_context(outreach_system):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    body = {"draft_id": draft["id"], "payload_hash": draft["payload_hash"]}
    _change_case_facts(domain, principal, incident_id)
    stale_approval = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/approve", json=body
    )
    assert stale_approval.status_code == 409
    assert (
        client.get(f"/api/incidents/{incident_id}/outreach").json().get("email_receipt")
        is None
    )


def test_direct_approved_runs_recheck_current_context(outreach_system):
    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    email_body = {"draft_id": draft["id"], "payload_hash": draft["payload_hash"]}
    assert (
        _post(
            client,
            f"/api/incidents/{incident_id}/outreach/email/approve",
            json=email_body,
        ).status_code
        == 200
    )
    envelope = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/envelope"
    ).json()
    voice_body = {
        "envelope_id": envelope["id"],
        "payload_hash": envelope["payload_hash"],
    }
    assert (
        _post(
            client,
            f"/api/incidents/{incident_id}/outreach/voice/approve",
            json=voice_body,
        ).status_code
        == 200
    )
    _change_case_facts(domain, principal, incident_id)
    email_run = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/run", json=email_body
    )
    voice_run = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/run", json=voice_body
    )
    assert email_run.status_code == 409
    assert voice_run.status_code == 409
    with domain.store.atomic(principal["workspace_id"]) as tx:
        assert tx.list("simulation_receipt") == []


def test_research_expiry_snapshot_invalidates_selection_and_approvals(outreach_system):
    domain, client, principal, incident_id = outreach_system
    _, research, _, selection = _select_contact(client, incident_id)
    draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    approval = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/email/approve",
        json={"draft_id": draft["id"], "payload_hash": draft["payload_hash"]},
    ).json()
    with domain.store.atomic(principal["workspace_id"]) as tx:
        workspace = tx.get("workspace", principal["workspace_id"])
        workspace["clock_offset"] = 24 * 60 * 60 + 1
        tx.put("workspace", workspace["id"], workspace)
    snapshot = client.get(f"/api/incidents/{incident_id}/outreach")
    assert snapshot.status_code == 200, snapshot.text
    body = snapshot.json()
    assert body["research"]["status"] == "EXPIRED"
    assert "selection" not in body and "email_draft" not in body
    with domain.store.atomic(principal["workspace_id"]) as tx:
        assert tx.get("contact_selection", selection["id"])["status"] == "STALE"
        assert tx.get("outreach_approval", approval["id"])["status"] == "STALE"
        assert "contacts" not in tx.get("contact_research", research["id"])
    assert (
        outreach.get_contact_candidates(
            domain.settings, domain.store, principal["workspace_id"], research["id"]
        )
        == []
    )


def test_completed_reason_requires_every_turn_to_be_served(outreach_system):
    _, client, _, incident_id = outreach_system
    _select_contact(client, incident_id)
    _, _, run = _start_voice(client, incident_id)
    ended = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/runs/{run['id']}/end",
        json={"reason": "completed"},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["run_status"] == "ENDED"


def test_erased_voice_envelope_idempotency_replay_is_safe(outreach_system):
    _, client, _, incident_id = outreach_system
    _select_contact(client, incident_id)
    envelope_key = str(uuid.uuid4())
    envelope = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/envelope",
        headers={"Idempotency-Key": envelope_key},
    ).json()
    body = {
        "envelope_id": envelope["id"],
        "payload_hash": envelope["payload_hash"],
    }
    assert (
        _post(
            client, f"/api/incidents/{incident_id}/outreach/voice/approve", json=body
        ).status_code
        == 200
    )
    run = _post(
        client, f"/api/incidents/{incident_id}/outreach/voice/run", json=body
    ).json()
    assert (
        _post(
            client,
            f"/api/incidents/{incident_id}/outreach/voice/runs/{run['id']}/end",
            json={"reason": "resident"},
        ).status_code
        == 200
    )
    replay = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/envelope",
        headers={"Idempotency-Key": envelope_key},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "OUTREACH_REVISION_ERASED"


def test_outreach_kill_switch_blocks_actions_but_allows_voice_cleanup(
    outreach_system, monkeypatch
):
    _, client, _, incident_id = outreach_system
    _, research, contact, _ = _select_contact(client, incident_id)
    draft = _post(
        client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}
    ).json()
    email_body = {"draft_id": draft["id"], "payload_hash": draft["payload_hash"]}
    assert (
        _post(
            client,
            f"/api/incidents/{incident_id}/outreach/email/approve",
            json=email_body,
        ).status_code
        == 200
    )
    _, voice_body, run = _start_voice(client, incident_id)
    monkeypatch.setattr(Settings, "outreach_enabled", property(lambda self: False))
    attempts = [
        _post(
            client,
            f"/api/incidents/{incident_id}/contact-selection",
            json={"research_id": research["id"], "contact_id": contact["id"]},
        ),
        _post(client, f"/api/incidents/{incident_id}/outreach/email/draft", json={}),
        _post(
            client, f"/api/incidents/{incident_id}/outreach/email/run", json=email_body
        ),
        _post(client, f"/api/incidents/{incident_id}/outreach/voice/envelope"),
        _post(
            client, f"/api/incidents/{incident_id}/outreach/voice/run", json=voice_body
        ),
    ]
    assert all(response.status_code == 503 for response in attempts)
    ended = _post(
        client,
        f"/api/incidents/{incident_id}/outreach/voice/runs/{run['id']}/end",
        json={"reason": "resident"},
    )
    assert ended.status_code == 200, ended.text


@pytest.mark.parametrize(
    "url",
    [
        "http://www.seattle.gov/contact",
        "https://seattle.gov.example.com/contact",
        "https://127.0.0.1/contact",
        "https://www.seattle.gov:444/contact",
        "https://user@www.seattle.gov/contact",
    ],
)
def test_official_url_validator_rejects_unsafe_sources(url):
    with pytest.raises(ValueError):
        outreach.validate_official_url(url, resolve=False)


def test_official_url_dns_and_redirect_guards(monkeypatch):
    monkeypatch.setattr(
        outreach.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    assert (
        outreach.validate_official_url("https://www.seattle.gov/contact")
        == "https://www.seattle.gov/contact"
    )
    monkeypatch.setattr(
        outreach.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, ("8.8.8.8", 443)),
            (None, None, None, None, ("10.0.0.4", 443)),
        ],
    )
    with pytest.raises(ValueError, match="public IP"):
        outreach.validate_official_url("https://www.seattle.gov/contact")

    monkeypatch.setattr(
        outreach.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )

    class Response:
        encoding = "utf-8"

        def __init__(self, status_code, headers, body=b""):
            self.status_code = status_code
            self.headers = headers
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self.body

    class Client:
        def __init__(self, responses):
            self.responses = iter(responses)

        def stream(self, *args, **kwargs):
            return next(self.responses)

    html, final_url = outreach._read_html(
        Client(
            [
                Response(302, {"location": "/final"}),
                Response(200, {"content-type": "text/html"}, b"official"),
            ]
        ),
        "https://www.seattle.gov/start",
        (),
    )
    assert html == "official"
    assert final_url == "https://www.seattle.gov/final"
    tail = b"<p>Customer service: 206-684-7623</p>"
    total = 313_554
    start = total - len(tail)
    ranged = Response(
        206,
        {
            "content-type": "text/html",
            "content-range": f"bytes {start}-{total - 1}/{total}",
            "content-encoding": "identity",
        },
        tail,
    )
    ranged_html, _ = outreach._read_html(
        Client([ranged]), "https://www.seattle.gov/contact", ()
    )
    assert "Customer service" in ranged_html
    with pytest.raises(ValueError, match="Cross-host"):
        outreach._read_html(
            Client(
                [
                    Response(
                        302,
                        {"location": "https://transportation.seattle.gov/final"},
                    )
                ]
            ),
            "https://www.seattle.gov/start",
            (),
        )


def test_suffix_range_finds_shared_contact_after_first_256_kib():
    contact = (
        b"<section><h2>General Information</h2>"
        b'<p>Email: <a href="mailto:road-info@seattle.gov">'
        b"road-info@seattle.gov</a></p>"
        b"<p>Phone: (206) 555-0142</p></section></body></html>"
    )
    full_page = (
        b"<html><head><title>Transportation contact</title></head><body>"
        + b"x" * 287_800
        + contact
    )
    assert full_page.index(contact) > 256 * 1024
    transferred = full_page[-256 * 1024 :]
    start = len(full_page) - len(transferred)
    captured = {}

    class Response:
        def __init__(self):
            self.status_code = 206
            self.encoding = "utf-8"
            self.headers = {
                "content-type": "text/html; charset=utf-8",
                "content-encoding": "identity",
                "content-range": (
                    f"bytes {start}-{len(full_page) - 1}/{len(full_page)}"
                ),
            }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            for offset in range(0, len(transferred), 16 * 1024):
                yield transferred[offset : offset + 16 * 1024]

    class Client:
        def stream(self, method, url, *, headers):
            captured.update(method=method, url=url, headers=headers)
            return Response()

    html, final_url = outreach._read_html(
        Client(),
        "https://www.seattle.gov/transportation/about-us/contact-us",
        (),
    )
    assert captured["headers"]["Range"] == "bytes=-262144"
    assert captured["headers"]["Accept-Encoding"] == "identity"
    assert len(html.encode()) == 256 * 1024
    result = outreach.extract_contact(html, final_url, "pothole")
    assert result is not None
    assert result["email"] == "road-info@seattle.gov"
    assert result["phone"] == "206-555-0142"
    assert result["source_title"] == "Official government contact page"


def test_pinned_https_uses_validated_ip_sni_and_wall_clock(monkeypatch):
    captured = {}

    class Socket:
        pass

    class Context:
        def wrap_socket(self, sock, server_hostname):
            captured["sni"] = server_hostname
            return sock

    monkeypatch.setattr(outreach.ssl, "create_default_context", Context)
    monkeypatch.setattr(
        outreach.socket,
        "create_connection",
        lambda address, timeout, source: (
            captured.update(address=address, timeout=timeout) or Socket()
        ),
    )
    connection = outreach._PinnedHTTPSConnection("www.seattle.gov", "8.8.8.8", 3)
    connection.connect()
    assert captured["address"] == ("8.8.8.8", 443)
    assert captured["sni"] == "www.seattle.gov"
    assert outreach.validate_official_url(
        "https://official.example/contact",
        ("official.example",),
        resolve=False,
    )

    times = iter((0, 0, 11))
    monkeypatch.setattr(outreach.time, "monotonic", lambda: next(times))

    class Trickle:
        def iter_bytes(self):
            yield b"a"
            yield b"b"

    with pytest.raises(TimeoutError):
        outreach._bounded_body(Trickle(), seconds=10)


def test_contact_extractor_discards_personal_email_and_keeps_shared_channel():
    result = outreach.extract_contact(
        """
        <html><title>Seattle contact</title><body>
        <a href="mailto:Jane.Doe@seattle.gov">staff</a>
        <a href="mailto:684-Road@seattle.gov">transportation help</a>
        Transportation customer service: (206) 684-7623
        </body></html>
        """,
        "https://www.seattle.gov/transportation/contact",
        "pothole",
    )
    assert result["email"] == "684-road@seattle.gov"
    assert result["phone"] == "206-684-7623"
    assert "Jane.Doe" not in str(result)
    actual_style = outreach.extract_contact(
        """
        <html><body><h3>General Information</h3>
        <p>Email: <a href="mailto:684-road@seattle.gov">684-road@seattle.gov</a></p>
        <p>Phone: (206) 684-7623</p></body></html>
        """,
        "https://www.seattle.gov/transportation/about-us/contact-us",
        "pothole",
    )
    assert actual_style["email"] == "684-road@seattle.gov"
    assert actual_style["phone"] == "206-684-7623"


def test_contact_extractor_rejects_named_staff_channels():
    assert (
        outreach.extract_contact(
            """
            <html><title>Transportation Department Contact</title><body>
            <p>Jane Doe, project manager</p>
            <a href="mailto:jane.doe.transportation@seattle.gov">Email Jane</a>
            <p>Phone 206-555-1234</p>
            </body></html>
            """,
            "https://www.seattle.gov/transportation/staff/jane-doe",
            "pothole",
        )
        is None
    )
    assert (
        outreach.extract_contact(
            """
            <html><body><script>
            const email = 'service@seattle.gov';
            const phone = 'Customer service: 206-555-1234';
            </script><style>.x{content:'info@seattle.gov'}</style></body></html>
            """,
            "https://www.seattle.gov/transportation/contact",
            "pothole",
        )
        is None
    )
    assert (
        outreach.extract_contact(
            """
            <html><title>Department directory</title><body>
            <p>Customer service directory</p>
            <p>Jane Doe, project manager: 206-555-1234</p>
            </body></html>
            """,
            "https://www.seattle.gov/transportation/staff",
            "pothole",
        )
        is None
    )


def test_contact_completion_revalidates_worker_channels(outreach_system, monkeypatch):
    _, client, _, incident_id = outreach_system
    candidate = _post(
        client, f"/api/incidents/{incident_id}/jurisdiction-preview"
    ).json()
    monkeypatch.setattr(
        outreach,
        "research_contacts",
        lambda *args, **kwargs: (
            [
                {
                    "agency": "City of Seattle",
                    "role": "Public contact",
                    "email": "jane2@seattle.gov",
                    "phone": "not-a-phone",
                    "source_title": "Official page",
                    "source_url": "https://www.seattle.gov/contact",
                    "match_reason": "Worker supplied malformed channels.",
                    "validated_channels": ["email", "phone"],
                }
            ],
            "test provider",
        ),
    )
    response = _post(
        client,
        f"/api/incidents/{incident_id}/contact-research",
        json={
            "candidate_id": candidate["id"],
            "context_hash": candidate["context_hash"],
            "confirmed": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["contacts"] == []


def test_aws_reverse_geocode_uses_storage_admin_names(monkeypatch):
    import boto3

    captured = {}

    class Places:
        def reverse_geocode(self, **kwargs):
            captured.update(kwargs)
            return {
                "ResultItems": [
                    {
                        "Address": {
                            "Municipality": "Seattle",
                            "Region": "Washington",
                            "Country": {"Code2": "US"},
                        }
                    }
                ]
            }

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: Places())
    settings = Settings(
        mode="aws",
        environment="demo",
        table_name="test",
        contact_research_enabled=True,
    )
    result = outreach.reverse_geocode(settings, 47.615, -122.335)
    assert result["supported"] is True
    assert captured["QueryPosition"] == [-122.335, 47.615]
    assert captured["IntendedUse"] == "Storage"
    assert captured["AddressNamesMode"] == "Administrative"

    class MalformedPlaces:
        def reverse_geocode(self, **kwargs):
            return {"ResultItems": ["not-an-address"]}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: MalformedPlaces())
    with pytest.raises(outreach.OutreachProviderError) as malformed:
        outreach.reverse_geocode(settings, 47.615, -122.335)
    assert malformed.value.code == "JURISDICTION_LOOKUP_FAILED"

    class NestedMalformedPlaces:
        def reverse_geocode(self, **kwargs):
            return {
                "ResultItems": [
                    {
                        "Address": {
                            "Municipality": {"Name": 7},
                            "Region": "Washington",
                            "Country": {"Code2": "US"},
                        }
                    }
                ]
            }

    monkeypatch.setattr(
        boto3, "client", lambda *args, **kwargs: NestedMalformedPlaces()
    )
    with pytest.raises(outreach.OutreachProviderError) as nested:
        outreach.reverse_geocode(settings, 47.615, -122.335)
    assert nested.value.code == "JURISDICTION_LOOKUP_FAILED"

    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(outreach.OutreachProviderError) as failure:
        outreach.reverse_geocode(settings, 47.615, -122.335)
    assert failure.value.code == "JURISDICTION_LOOKUP_FAILED"


def test_aws_polly_is_bounded_and_closes_stream(monkeypatch):
    import boto3

    captured = {}

    class Audio:
        closed = False
        emitted = False

        def read(self, limit):
            captured["read_limit"] = limit
            if self.emitted:
                return b""
            self.emitted = True
            return b"mp3"

        def close(self):
            self.closed = True

    audio = Audio()

    class Polly:
        def synthesize_speech(self, **kwargs):
            captured.update(kwargs)
            return {"AudioStream": audio}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: Polly())
    settings = Settings(mode="aws", environment="demo", table_name="test")
    stream, media_type = outreach.synthesize_audio_stream(
        settings, "Approved bounded caption.", "reporting_agent"
    )
    assert b"".join(stream) == b"mp3"
    assert media_type == "audio/mpeg"
    assert captured["Text"] == "Approved bounded caption."
    assert captured["TextType"] == "text"
    assert captured["OutputFormat"] == "mp3"
    assert captured["read_limit"] == 16 * 1024
    assert audio.closed is True

    class WrongAudio(Audio):
        def read(self, limit):
            return "not-bytes"

    wrong_audio = WrongAudio()

    class MalformedPolly:
        def synthesize_speech(self, **kwargs):
            return {"AudioStream": wrong_audio}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: MalformedPolly())
    with pytest.raises(outreach.OutreachProviderError) as malformed:
        stream, _ = outreach.synthesize_audio_stream(
            settings, "Approved bounded caption.", "reporting_agent"
        )
        list(stream)
    assert malformed.value.code == "VOICE_AUDIO_FAILED"
    assert wrong_audio.closed is True

    class FailedPolly:
        def synthesize_speech(self, **kwargs):
            raise TimeoutError()

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FailedPolly())
    with pytest.raises(outreach.OutreachProviderError) as failure:
        outreach.synthesize_audio_stream(
            settings, "Approved bounded caption.", "reporting_agent"
        )
    assert failure.value.code == "VOICE_AUDIO_FAILED"


def test_brave_query_contains_only_server_owned_scope(monkeypatch):
    captured = {}

    class Response:
        def __init__(self):
            self.headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b'{"web":{"results":[{"url":"https://www.seattle.gov/a"}]}}'

    class Client:
        def __init__(self, exceptions=(), overall_deadline=None):
            self.exceptions = exceptions

        def stream(self, method, url, headers):
            captured.update(url=url, headers=headers)
            return Response()

    monkeypatch.setattr(outreach, "_brave_token", lambda *args: "secret")
    monkeypatch.setattr(outreach, "_PinnedPageClient", Client)
    monkeypatch.setattr(
        outreach,
        "validate_official_url",
        lambda value, exceptions=(), **kwargs: value,
    )
    monkeypatch.setattr(
        outreach,
        "_read_html",
        lambda client, url, exceptions: (
            '<a href="mailto:roads@seattle.gov">Roads</a> '
            "Customer service: 206-684-7623",
            url,
        ),
    )
    settings = Settings(
        mode="aws",
        environment="demo",
        brave_search_secret_arn="secret-arn",
        table_name="test",
    )
    contacts, _ = outreach.research_contacts_aws(settings, "pothole")
    assert contacts
    params = parse_qs(urlsplit(captured["url"]).query)
    assert params["q"] == [
        "Seattle WA pothole road government contact site:seattle.gov"
    ]
    assert params["spellcheck"] == ["false"]
    serialized = str(captured)
    assert "description" not in serialized
    assert "latitude" not in serialized
    assert "resident" not in serialized
    assert captured["headers"]["X-Subscription-Token"] == "secret"


@pytest.mark.parametrize(
    ("media_type", "chunks", "http_failure"),
    [
        ("text/html", [b"{}"], False),
        ("application/json", [b"x" * (256 * 1024 + 1)], False),
        ("application/json", [b"{"], False),
        ("application/json", [b"[]"], False),
        ("application/json", [b'{"web":"bad"}'], False),
        ("application/json", [], True),
    ],
)
def test_brave_response_failures_are_bounded(
    monkeypatch, media_type, chunks, http_failure
):
    class Response:
        def __init__(self):
            self.headers = {"content-type": media_type}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            if http_failure:
                raise outreach.httpx.HTTPError("provider failure")

        def iter_bytes(self):
            yield from chunks

    class Client:
        def __init__(self, exceptions=(), overall_deadline=None):
            self.exceptions = exceptions

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(outreach, "_brave_token", lambda *args: "secret")
    monkeypatch.setattr(outreach, "_PinnedPageClient", Client)
    settings = Settings(
        mode="aws",
        environment="demo",
        brave_search_secret_arn="secret-arn",
        table_name="test",
    )
    with pytest.raises(outreach.OutreachProviderError) as failure:
        outreach.research_contacts_aws(settings, "pothole")
    assert failure.value.code == "CONTACT_RESEARCH_FAILED"


def test_voice_validator_rejects_freeform_contact_claim(outreach_system):
    domain, _, principal, incident_id = outreach_system
    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident = tx.get("incident", incident_id)
        envelope = {
            "max_turns": 6,
            "facts": [
                {"id": "category", "value": "Pothole"},
                {"id": "description", "value": incident["description"]},
                {"id": "location", "value": incident["location_label"]},
                {"id": "jurisdiction", "value": "Seattle, Washington, US"},
            ],
        }
    turns = fixtures.simulate_voice(envelope, principal)["turns"]
    turns[0]["variant_id"] = "The email is roads@seattle.gov"
    with pytest.raises(DomainError) as invalid:
        domain.validate_voice_script(envelope, {"turns": turns})
    assert invalid.value.code == "VOICE_SCRIPT_INVALID"


def test_voice_validator_requires_exact_fact_bindings(outreach_system):
    domain, _, principal, incident_id = outreach_system
    with domain.store.atomic(principal["workspace_id"]) as tx:
        incident = tx.get("incident", incident_id)
    envelope = {
        "max_turns": 6,
        "facts": [
            {"id": "category", "value": "Pothole"},
            {"id": "description", "value": incident["description"]},
            {"id": "location", "value": incident["location_label"]},
            {"id": "jurisdiction", "value": "Seattle, Washington, US"},
        ],
    }
    turns = fixtures.simulate_voice(envelope, principal)["turns"]
    turns[0]["fact_ids"] = []
    with pytest.raises(DomainError) as invented:
        domain.validate_voice_script(envelope, {"turns": turns})
    assert invented.value.code == "VOICE_SCRIPT_INVALID"


def test_voice_cleanup_schedule_and_worker_contract(monkeypatch):
    import services.api.domain as domain_module
    from services.agents import aws_workflow

    captured = {}

    class StepFunctions:
        def start_execution(self, **kwargs):
            captured.update(kwargs)
            return {"executionArn": "cleanup-execution"}

    monkeypatch.setenv("NF_STATE_MACHINE_ARN", "state-machine")
    monkeypatch.setattr(aws_workflow, "_client", lambda: StepFunctions())
    run_id = "a" * 32
    expires_at = iso(time.time() + 600)
    assert (
        aws_workflow.start_voice_cleanup("workspace_1", run_id, expires_at)
        == "cleanup-execution"
    )
    assert captured["name"] == f"voice-cleanup-{run_id}"
    assert json.loads(captured["input"]) == {
        "phase": "voice_cleanup_wait",
        "workspace_id": "workspace_1",
        "run_id": run_id,
        "expires_at": expires_at,
    }

    calls = []

    class FakeDomain:
        settings = object()
        store = object()

        def expire_voice_run(self, workspace_id, target_run_id):
            calls.append(("finalize", workspace_id, target_run_id))

    monkeypatch.setattr(domain_module, "Domain", FakeDomain)
    monkeypatch.setattr(
        outreach,
        "delete_voice_session",
        lambda settings, store, workspace_id, target_run_id: calls.append(
            ("purge", workspace_id, target_run_id)
        ),
    )
    event = {
        "phase": "voice_cleanup",
        "workspace_id": "workspace_1",
        "run_id": run_id,
        "expires_at": expires_at,
    }
    assert aws_workflow.handler(event, None) == {"status": "PURGED"}
    assert calls == [
        ("finalize", "workspace_1", run_id),
        ("purge", "workspace_1", run_id),
    ]
    with pytest.raises(ValueError):
        aws_workflow.handler({**event, "unexpected": True}, None)


def test_research_dispatch_uses_isolated_function(monkeypatch):
    import boto3

    captured = {}

    class Lambda:
        def invoke(self, **kwargs):
            captured.update(kwargs)
            return {"StatusCode": 202}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: Lambda())
    settings = Settings(
        mode="aws",
        environment="demo",
        table_name="records",
        contact_research_function="isolated-research",
        outreach_provider_function="voice-worker",
    )
    outreach.dispatch_outreach_worker(
        settings,
        {
            "action": "research_contacts",
            "workspace_id": "workspace",
            "research_id": "b" * 32,
        },
    )
    assert captured["FunctionName"] == "isolated-research"
    payload = json.loads(captured["Payload"])
    assert payload == {
        "phase": "outreach_provider",
        "action": "research_contacts",
        "workspace_id": "workspace",
        "research_id": "b" * 32,
    }


def test_provider_claims_coalesce_duplicates_and_settlement_retries(
    outreach_system, monkeypatch
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import services.api.domain as domain_module
    from services.agents import aws_workflow
    from services.agents.aws_storage import ConcurrentUpdate

    domain, client, principal, incident_id = outreach_system
    _, research = _research(client, incident_id)
    with domain.store.atomic(principal["workspace_id"]) as tx:
        pending = tx.get("contact_research", research["id"])
        pending.update(
            status="PENDING",
            provider="pending",
            provider_deadline_at=iso(time.time() + 300),
        )
        pending.pop("provider_claim_id", None)
        pending.pop("provider_claimed_at", None)
        tx.put("contact_research", pending["id"], pending)

    monkeypatch.setattr(domain_module, "Domain", lambda: domain)
    entered = threading.Event()
    release = threading.Event()
    provider_calls = 0

    def provider(*args):
        nonlocal provider_calls
        provider_calls += 1
        entered.set()
        assert release.wait(2)
        return (
            [
                {
                    "agency": "Seattle Department of Transportation",
                    "role": "General information",
                    "email": "684-road@seattle.gov",
                    "phone": "206-684-7623",
                    "source_title": "SDOT contact",
                    "source_url": "https://www.seattle.gov/transportation/contact",
                    "match_reason": "Shared public contact channel.",
                    "validated_channels": ["email", "phone"],
                }
            ],
            "Brave Search with bounded official-page fetch",
        )

    monkeypatch.setattr(outreach, "research_contacts_aws", provider)
    original_complete = domain.complete_contact_research
    completion_calls = 0

    def settle(*args):
        nonlocal completion_calls
        completion_calls += 1
        if completion_calls < 3:
            raise ConcurrentUpdate("test workspace conflict")
        return original_complete(*args)

    monkeypatch.setattr(domain, "complete_contact_research", settle)
    event = {
        "phase": "outreach_provider",
        "action": "research_contacts",
        "workspace_id": principal["workspace_id"],
        "research_id": research["id"],
    }

    class Context:
        def __init__(self, request_id):
            self.aws_request_id = request_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(aws_workflow.research_handler, event, Context("winner"))
        assert entered.wait(2)
        duplicate = pool.submit(
            aws_workflow.research_handler, event, Context("duplicate")
        )
        assert duplicate.result(timeout=2) == {"status": "PENDING"}
        release.set()
        assert winner.result(timeout=2) == {"status": "READY"}
    assert provider_calls == 1
    assert completion_calls == 3
    with domain.store.atomic(principal["workspace_id"]) as tx:
        completed = tx.get("contact_research", research["id"])
        assert completed["status"] == "READY"
        assert "provider_claim_id" not in completed


def test_voice_provider_claim_prevents_duplicate_and_retries_settlement(
    outreach_system, monkeypatch
):
    import services.api.domain as domain_module
    from services.agents import aws_workflow, runtime_client
    from services.agents.aws_storage import ConcurrentUpdate

    domain, client, principal, incident_id = outreach_system
    _select_contact(client, incident_id)
    envelope, body, run = _start_voice(client, incident_id)
    outreach.delete_voice_session(
        domain.settings, domain.store, principal["workspace_id"], run["id"]
    )
    with domain.store.atomic(principal["workspace_id"]) as tx:
        stored_run = tx.get("voice_run", run["id"])
        stored_run.update(
            status="GENERATING",
            provider_deadline_at=iso(time.time() + 300),
        )
        for key in (
            "ready_at",
            "transcript_expires_at",
            "turn_count",
            "provider_claim_id",
            "provider_claimed_at",
        ):
            stored_run.pop(key, None)
        stored_envelope = tx.get("voice_envelope", envelope["id"])
        stored_envelope["status"] = "GENERATING"
        tx.put("voice_run", stored_run["id"], stored_run)
        tx.put("voice_envelope", stored_envelope["id"], stored_envelope)

    monkeypatch.setattr(domain_module, "Domain", lambda: domain)
    provider_calls = 0

    def provider(provider_envelope, provider_principal):
        nonlocal provider_calls
        provider_calls += 1
        return fixtures.simulate_voice(provider_envelope, provider_principal)

    monkeypatch.setattr(runtime_client, "simulate_voice", provider)
    original_complete = domain.complete_voice_generation
    completion_calls = 0

    def settle(*args):
        nonlocal completion_calls
        completion_calls += 1
        if completion_calls < 3:
            raise ConcurrentUpdate("test workspace conflict")
        return original_complete(*args)

    monkeypatch.setattr(domain, "complete_voice_generation", settle)
    event = {
        "phase": "outreach_provider",
        "action": "simulate_voice",
        "workspace_id": principal["workspace_id"],
        "envelope_id": envelope["id"],
        "payload_hash": body["payload_hash"],
    }

    class Context:
        aws_request_id = "voice-winner"

    with domain.store.atomic(principal["workspace_id"]) as tx:
        claimed = tx.get("voice_run", run["id"])
        claimed["provider_claim_id"] = "other-worker"
        claimed["provider_claimed_at"] = time.time()
        tx.put("voice_run", claimed["id"], claimed)
    assert aws_workflow.handler(event, Context()) == {"status": "GENERATING"}
    assert provider_calls == 0
    with domain.store.atomic(principal["workspace_id"]) as tx:
        claimed = tx.get("voice_run", run["id"])
        claimed.pop("provider_claim_id", None)
        claimed.pop("provider_claimed_at", None)
        tx.put("voice_run", claimed["id"], claimed)

    assert aws_workflow.handler(event, Context()) == {"status": "RUNNING"}
    assert provider_calls == 1
    assert completion_calls == 3
    assert aws_workflow.handler(event, Context()) == {"status": "RUNNING"}
    assert provider_calls == 1
    with domain.store.atomic(principal["workspace_id"]) as tx:
        completed = tx.get("voice_run", run["id"])
        assert completed["status"] == "RUNNING"
        assert "provider_claim_id" not in completed


def test_outreach_headers_are_required_and_audio_is_typed(outreach_system):
    _, client, _, incident_id = outreach_system
    missing = client.request(
        "POST", f"/api/incidents/{incident_id}/jurisdiction-preview"
    )
    assert missing.status_code == 422

    document = client.get("/openapi.json").json()
    mutations = (
        "/api/incidents/{incident_id}/jurisdiction-preview",
        "/api/incidents/{incident_id}/contact-research",
        "/api/incidents/{incident_id}/contact-selection",
        "/api/incidents/{incident_id}/outreach/email/draft",
        "/api/incidents/{incident_id}/outreach/email/approve",
        "/api/incidents/{incident_id}/outreach/email/run",
        "/api/incidents/{incident_id}/outreach/voice/envelope",
        "/api/incidents/{incident_id}/outreach/voice/approve",
        "/api/incidents/{incident_id}/outreach/voice/run",
        "/api/incidents/{incident_id}/outreach/voice/runs/{run_id}/end",
    )
    for path in mutations:
        headers = [
            parameter
            for parameter in document["paths"][path]["post"]["parameters"]
            if parameter["in"] == "header"
        ]
        assert headers == [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "title": "Idempotency-Key"},
            }
        ]
    audio_path = (
        "/api/incidents/{incident_id}/outreach/voice/runs/{run_id}/"
        "turns/{turn_id}/audio"
    )
    audio_operation = document["paths"][audio_path]["get"]
    playback = next(
        parameter
        for parameter in audio_operation["parameters"]
        if parameter["name"] == "X-NF-Playback-Token"
    )
    assert playback["required"] is True
    content = audio_operation["responses"]["200"]["content"]
    assert content["audio/mpeg"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
    assert content["audio/wav"]["schema"] == {
        "type": "string",
        "format": "binary",
    }


def test_aws_voice_cleanup_deletes_session_and_claims_atomically(monkeypatch):
    captured = []

    class Dynamo:
        def get_item(self, **kwargs):
            return {
                "Item": {
                    "data": {"S": json.dumps({"turns": [{"id": "one"}, {"id": "two"}]})}
                }
            }

        def transact_write_items(self, **kwargs):
            captured.append(kwargs["TransactItems"])

    monkeypatch.setattr(outreach, "_voice_table", lambda settings: Dynamo())
    settings = Settings(
        mode="aws",
        environment="demo",
        table_name="records",
        voice_transcripts_table="temporary",
    )
    outreach.delete_voice_session(settings, object(), "workspace", "c" * 32)
    deleted = [item["Delete"]["Key"]["sk"]["S"] for item in captured[0]]
    assert deleted == [
        f"RUN#{'c' * 32}",
        f"AUDIO#{'c' * 32}#one",
        f"AUDIO#{'c' * 32}#two",
    ]

    captured.clear()
    outreach.claim_voice_audio(
        settings,
        object(),
        "workspace",
        "c" * 32,
        "one",
        int(time.time() + 600),
    )
    transaction = captured[0]
    assert transaction[0]["ConditionCheck"]["Key"]["sk"]["S"] == f"RUN#{'c' * 32}"
    assert transaction[1]["Update"]["Key"]["sk"]["S"] == f"AUDIO#{'c' * 32}#one"


def test_local_transient_store_is_memory_only_and_actively_expires(tmp_path):
    settings = Settings(data_dir=tmp_path)
    domain = Domain(settings)
    expires = time.time() + 0.03
    outreach.put_voice_session(
        settings,
        domain.store,
        "workspace",
        "d" * 32,
        {"turns": [], "expires_epoch": expires},
    )
    assert outreach.get_voice_session(settings, domain.store, "workspace", "d" * 32)
    time.sleep(0.06)
    assert (
        outreach.get_voice_session(settings, domain.store, "workspace", "d" * 32)
        is None
    )
    with domain.store.atomic("workspace") as tx:
        assert tx.get("voice_session", "d" * 32) is None
