"""Authenticated audio-only FastAPI surface for Lambda response streaming.

API Gateway deliberately does not project identity into this service. The
service verifies the Clerk bearer JWT against the configured issuer's JWKS,
then reuses the shared domain policy for application-specific claim checks and
all owner, run, turn, capability, Polly, and retention decisions.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Annotated, Any
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, FastAPI, Header, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from jwt import ExpiredSignatureError, InvalidTokenError, PyJWKClient, PyJWKClientError

from services.api.config import Settings
from services.api.domain import Domain, DomainError

OPAQUE_ID = r"^[A-Za-z0-9_-]{1,128}$"


class ClerkJWTVerifier:
    """Verify Clerk JWT cryptography and registered claims with a bounded JWKS client."""

    def __init__(self, settings: Settings, key_client: Any | None = None):
        issuer = settings.clerk_issuer.rstrip("/")
        parsed = urlsplit(issuer)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.hostname
            or not parsed.hostname.endswith(".clerk.accounts.dev")
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("NF_CLERK_ISSUER must be an exact Clerk HTTPS issuer")
        if not settings.auth_audience:
            raise ValueError("NF_AUTH_AUDIENCE is required")
        self.issuer = issuer
        self.audience = settings.auth_audience
        self.key_client = key_client or PyJWKClient(
            f"{issuer}/.well-known/jwks.json",
            cache_keys=True,
            max_cached_keys=16,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
            cooldown_duration=30,
        )

    def verify(self, authorization: str | None) -> dict[str, Any]:
        if not authorization:
            raise DomainError(
                "UNAUTHENTICATED", "A verified Clerk session is required.", 401
            )
        scheme, separator, token = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not token
            or len(token) > 16_384
            or re.search(r"\s", token)
        ):
            raise DomainError(
                "UNAUTHENTICATED", "A verified Clerk session is required.", 401
            )
        try:
            signing_key = self.key_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=5,
                options={"require": ["iss", "aud", "exp", "sub"]},
            )
        except ExpiredSignatureError as exc:
            raise DomainError(
                "SESSION_EXPIRED", "Your session expired. Sign in again.", 401
            ) from exc
        except (InvalidTokenError, PyJWKClientError, OSError, ValueError) as exc:
            raise DomainError(
                "UNAUTHENTICATED", "A verified Clerk session is required.", 401
            ) from exc
        if not isinstance(claims, dict):
            raise DomainError(
                "UNAUTHENTICATED", "A verified Clerk session is required.", 401
            )
        return claims


def _error(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "correlation_id": getattr(
                    request.state, "correlation_id", uuid.uuid4().hex
                ),
            }
        },
        status_code=exc.status,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


def create_app(
    settings: Settings | None = None,
    domain: Domain | None = None,
    verifier: ClerkJWTVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings()
    problem: str | None = None
    if domain is None:
        try:
            domain = Domain(settings)
        except Exception as exc:  # noqa: BLE001 - surface only a safe readiness state
            problem = exc.__class__.__name__
    if verifier is None:
        try:
            verifier = ClerkJWTVerifier(settings)
        except ValueError as exc:  # readiness remains available for diagnosis
            problem = problem or exc.__class__.__name__

    app = FastAPI(
        title="Neighborhood Fixer transient audio",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.domain = domain
    app.state.verifier = verifier
    app.state.configuration_problem = problem
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization", "X-NF-Playback-Token"],
        max_age=300,
    )

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        request.state.correlation_id = uuid.uuid4().hex
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return _error(
                request,
                DomainError(
                    "ORIGIN_FORBIDDEN",
                    "This request origin is not allowed.",
                    403,
                ),
            )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(DomainError)
    async def handle_domain(request: Request, exc: DomainError):
        return _error(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        del exc
        return _error(
            request,
            DomainError(
                "VALIDATION_ERROR",
                "The requested temporary audio turn is invalid.",
                422,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        del exc
        return _error(
            request,
            DomainError(
                "AUDIO_UNAVAILABLE",
                "Temporary demo audio is unavailable.",
                503,
                True,
            ),
        )

    def backend() -> Domain:
        current = app.state.domain
        if current is None:
            raise DomainError(
                "AUDIO_CONFIGURATION_REQUIRED",
                "Temporary demo audio is not configured.",
                503,
                True,
            )
        return current

    def principal(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ):
        current = app.state.verifier
        if current is None:
            raise DomainError(
                "AUTH_CONFIGURATION_REQUIRED",
                "Clerk verification is not configured.",
                503,
            )
        claims = current.verify(authorization)
        return backend().clerk_principal(claims)

    @app.get("/healthz", include_in_schema=False)
    def health():
        return JSONResponse(
            {
                "status": "ready"
                if app.state.domain is not None and app.state.verifier is not None
                else "configuration_required",
                "checked_at": int(time.time()),
            },
            status_code=200,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get(
        "/api/incidents/{incident_id}/outreach/voice/runs/{run_id}/turns/{turn_id}/audio",
        response_class=StreamingResponse,
    )
    def turn_audio(
        incident_id: Annotated[str, Path(pattern=OPAQUE_ID)],
        run_id: Annotated[str, Path(pattern=OPAQUE_ID)],
        turn_id: Annotated[str, Path(pattern=OPAQUE_ID)],
        playback_token: Annotated[
            str, Header(alias="X-NF-Playback-Token", max_length=512)
        ],
        resident=Depends(principal),  # noqa: B008 - FastAPI dependency marker
    ):
        stream, media_type = backend().voice_turn_audio(
            resident,
            incident_id,
            run_id,
            turn_id,
            playback_token,
        )
        if media_type not in ("audio/mpeg", "audio/wav"):
            raise DomainError(
                "AUDIO_FORMAT_REJECTED",
                "Temporary demo audio returned an unsupported format.",
                503,
            )
        return StreamingResponse(
            stream,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Content-Disposition": 'inline; filename="temporary-demo-turn"',
            },
        )

    return app


app = create_app()
