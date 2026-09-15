import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from services.api.config import Settings
from services.api.domain import DomainError
from services.api.main import create_app as create_main_app
from services.audio.main import ClerkJWTVerifier, create_app


def audio_settings(**updates):
    values = {
        "mode": "aws",
        "environment": "test",
        "table_name": "records",
        "clerk_issuer": "https://neighborhood-fixer.clerk.accounts.dev",
        "auth_audience": "neighborhood-fixer-api",
        "authorized_parties": ("https://app.example",),
        "allowed_origins": ("https://app.example",),
        "shared_workspace_id": "demo-borough-v1",
        "data_generation": "audio-tests",
        "contact_research_enabled": True,
        "voice_transcripts_table": "temporary",
    }
    values.update(updates)
    return Settings(**values)


class StaticKeyClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        assert token
        return SimpleNamespace(key=self.public_key)


def claims(settings, **updates):
    now = int(time.time())
    value = {
        "iss": settings.clerk_issuer,
        "aud": settings.auth_audience,
        "azp": "https://app.example",
        "sub": "user_audio_test",
        "sid": "sess_audio_test",
        "scope": "nf:resident",
        "v": "2",
        "exp": now + 300,
        "iat": now,
    }
    value.update(updates)
    return value


def test_clerk_verifier_checks_signature_issuer_audience_and_expiry():
    settings = audio_settings()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = ClerkJWTVerifier(settings, StaticKeyClient(private_key.public_key()))
    token = jwt.encode(claims(settings), private_key, algorithm="RS256")
    assert verifier.verify(f"Bearer {token}")["sub"] == "user_audio_test"

    for bad_claims in (
        claims(settings, iss="https://other.clerk.accounts.dev"),
        claims(settings, aud="another-api"),
        claims(settings, exp=int(time.time()) - 30),
    ):
        bad_token = jwt.encode(bad_claims, private_key, algorithm="RS256")
        with pytest.raises(DomainError) as caught:
            verifier.verify(f"Bearer {bad_token}")
        assert caught.value.status == 401

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims(settings), other_key, algorithm="RS256")
    with pytest.raises(DomainError) as caught:
        verifier.verify(f"Bearer {forged}")
    assert caught.value.code == "UNAUTHENTICATED"


def test_clerk_verifier_rejects_non_clerk_or_ambiguous_issuers():
    for issuer in (
        "http://tenant.clerk.accounts.dev",
        "https://clerk.accounts.dev.evil.example",
        "https://tenant.clerk.accounts.dev/path",
        "https://user@tenant.clerk.accounts.dev",
    ):
        with pytest.raises(ValueError):
            ClerkJWTVerifier(audio_settings(clerk_issuer=issuer), object())


class FakeVerifier:
    def __init__(self, settings):
        self.settings = settings
        self.seen = []

    def verify(self, authorization):
        self.seen.append(authorization)
        if authorization != "Bearer current-jwt":
            raise DomainError("UNAUTHENTICATED", "Sign in.", 401)
        return claims(self.settings)


class FakeDomain:
    def __init__(self):
        self.claims = None
        self.audio_args = None

    def clerk_principal(self, value):
        self.claims = value
        return {
            "workspace_id": "demo-borough-v1",
            "user": {"id": "resident-1"},
        }

    def voice_turn_audio(self, principal, incident_id, run_id, turn_id, playback_token):
        self.audio_args = (
            principal,
            incident_id,
            run_id,
            turn_id,
            playback_token,
        )
        return iter((b"first-", b"second")), "audio/mpeg"


def test_audio_route_reuses_domain_checks_and_streams_with_no_store_headers():
    settings = audio_settings()
    domain = FakeDomain()
    verifier = FakeVerifier(settings)
    client = TestClient(create_app(settings, domain, verifier))

    with client.stream(
        "GET",
        "/api/incidents/case_1/outreach/voice/runs/run_1/turns/turn_1/audio",
        headers={
            "Authorization": "Bearer current-jwt",
            "X-NF-Playback-Token": "header-only-capability",
            "Origin": "https://app.example",
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/mpeg")
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["access-control-allow-origin"] == "https://app.example"
        assert b"".join(response.iter_bytes()) == b"first-second"

    assert domain.claims["sub"] == "user_audio_test"
    assert domain.audio_args == (
        {"workspace_id": "demo-borough-v1", "user": {"id": "resident-1"}},
        "case_1",
        "run_1",
        "turn_1",
        "header-only-capability",
    )


def test_audio_route_requires_bearer_and_header_capability_and_has_exact_cors():
    settings = audio_settings()
    domain = FakeDomain()
    client = TestClient(create_app(settings, domain, FakeVerifier(settings)))
    path = "/api/incidents/case_1/outreach/voice/runs/run_1/turns/turn_1/audio"

    missing = client.get(
        path + "?playback_token=query-capability",
        headers={"Authorization": "Bearer current-jwt"},
    )
    assert missing.status_code == 422
    assert domain.audio_args is None

    unauthorized = client.get(
        path,
        headers={"X-NF-Playback-Token": "header-only-capability"},
    )
    assert unauthorized.status_code == 401

    preflight = client.options(
        path,
        headers={
            "Origin": "https://app.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": ("authorization,x-nf-playback-token"),
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://app.example"
    assert (
        "x-nf-playback-token"
        in preflight.headers["access-control-allow-headers"].lower()
    )

    forbidden = client.get(
        path,
        headers={
            "Authorization": "Bearer current-jwt",
            "X-NF-Playback-Token": "header-only-capability",
            "Origin": "https://evil.example",
        },
    )
    assert forbidden.status_code == 403
    assert "access-control-allow-origin" not in forbidden.headers


def test_audio_route_rejects_non_audio_provider_format_before_streaming():
    settings = audio_settings()
    domain = FakeDomain()
    domain.voice_turn_audio = lambda *args: (iter((b"secret",)), "text/plain")
    client = TestClient(create_app(settings, domain, FakeVerifier(settings)))
    response = client.get(
        "/api/incidents/case_1/outreach/voice/runs/run_1/turns/turn_1/audio",
        headers={
            "Authorization": "Bearer current-jwt",
            "X-NF-Playback-Token": "header-only-capability",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUDIO_FORMAT_REJECTED"
    assert b"secret" not in response.content


def test_main_aws_api_rejects_audio_before_consuming_a_playback_claim():
    settings = audio_settings()
    domain = FakeDomain()
    app = create_main_app(settings, domain)

    @app.middleware("http")
    async def inject_verified_gateway_claims(request, call_next):
        request.scope["aws.event"] = {
            "requestContext": {
                "authorizer": {"jwt": {"claims": {"sub": "user_audio_test"}}}
            }
        }
        return await call_next(request)

    response = TestClient(app).get(
        "/api/incidents/case_1/outreach/voice/runs/run_1/turns/turn_1/audio",
        headers={"X-NF-Playback-Token": "header-only-capability"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEDICATED_AUDIO_ENDPOINT_REQUIRED"
    assert domain.audio_args is None
