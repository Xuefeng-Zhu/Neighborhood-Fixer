from io import BytesIO
from pathlib import Path
import hashlib
import os
import uuid
import time
from datetime import datetime

from fastapi import FastAPI, Depends, Request, Response, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings
from .domain import Domain, DomainError, ident, iso
from .models import (
    ApprovalInput,
    Category,
    ClockInput,
    DraftInput,
    DuplicateDecision,
    ErrorEnvelope,
    Incident,
    Observation,
    ObservationInput,
    ObservationPatch,
    OperationResponse,
    ScenarioInput,
    SessionRequest,
    SessionResponse,
    SubmissionDraft,
    SubscriptionInput,
    TicketStatusInput,
    VerificationInput,
)

Image.MAX_IMAGE_PIXELS = 20_000_000


def create_app(settings: Settings | None = None, domain: Domain | None = None):
    settings = settings or Settings()
    problem = None
    if domain is None:
        try:
            domain = Domain(settings)
        except Exception as exc:
            problem = str(exc)
    signer = (
        URLSafeTimedSerializer(settings.secret()) if settings.mode == "local" else None
    )
    app = FastAPI(
        title="Neighborhood Fixer API",
        version="0.1.0",
        description="Consent-bound case manager. Local demo is fictional and simulated.",
        responses={
            400: {"model": ErrorEnvelope},
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            429: {"model": ErrorEnvelope},
            503: {"model": ErrorEnvelope},
        },
    )
    app.state.domain, app.state.settings = domain, settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
    )

    def backend():
        if domain is None:
            raise DomainError(
                "AWS_CONFIGURATION_REQUIRED",
                problem or "Backend unavailable. No fixture fallback was used.",
                503,
                True,
            )
        return domain

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        correlation = uuid.uuid4().hex
        request.state.correlation_id = correlation
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                return JSONResponse(
                    {
                        "error": {
                            "code": "ORIGIN_FORBIDDEN",
                            "message": "This request origin is not allowed.",
                            "retryable": False,
                            "correlation_id": correlation,
                        }
                    },
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(DomainError)
    async def handle_domain(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "correlation_id": getattr(request.state, "correlation_id", ident()),
                    **(
                        {"details": exc.details}
                        if getattr(exc, "details", None)
                        else {}
                    ),
                }
            },
            headers={
                "Retry-After": str(
                    max(
                        1,
                        int(
                            datetime.fromisoformat(
                                exc.details["reset_at"].replace("Z", "+00:00")
                            ).timestamp()
                            - time.time()
                        ),
                    )
                )
            }
            if exc.status == 429 and getattr(exc, "details", None)
            else {},
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request, exc):
        # Validation input may contain private contacts; never echo it in errors.
        fields = sorted({str(e["loc"][-1]) for e in exc.errors()})
        return JSONResponse(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Check the required fields: " + ", ".join(fields) + ".",
                    "retryable": False,
                    "correlation_id": getattr(request.state, "correlation_id", ident()),
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request, exc):
        retryable = exc.__class__.__name__ == "ConcurrentUpdate"
        return JSONResponse(
            {
                "error": {
                    "code": "CONCURRENT_UPDATE" if retryable else "INTERNAL_ERROR",
                    "message": "Another update completed first. Refresh and retry."
                    if retryable
                    else "The operation could not complete. Check server configuration and retry.",
                    "retryable": retryable,
                    "correlation_id": getattr(request.state, "correlation_id", ident()),
                }
            },
            status_code=409 if retryable else 500,
        )

    def principal(request: Request):
        if settings.mode == "local":
            token = request.cookies.get("nf_session")
            if not token:
                raise DomainError(
                    "UNAUTHENTICATED", "Choose a local demo resident to continue.", 401
                )
            try:
                value = signer.loads(token, max_age=7 * 86400)
            except (BadSignature, SignatureExpired):
                raise DomainError(
                    "SESSION_EXPIRED",
                    "Your local session expired. Start a new isolated demo.",
                    401,
                )
            if (
                not isinstance(value, dict)
                or not value.get("workspace_id")
                or not value.get("user", {}).get("id")
            ):
                raise DomainError("UNAUTHENTICATED", "Invalid session.", 401)
            return value
        # Only API Gateway's verified JWT claims are trusted. Headers never carry identity.
        event = request.scope.get("aws.event", {})
        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims")
        )
        if not isinstance(claims, dict) or not claims.get("sub"):
            raise DomainError(
                "UNAUTHENTICATED", "A verified Clerk session is required.", 401
            )
        return backend().clerk_principal(claims)

    def demo_guard():
        if not settings.local_controls:
            raise DomainError(
                "DEMO_DISABLED",
                "Scenario controls are restricted to explicit local development.",
                403,
            )

    def dispatch(operation_id, workspace_id):
        if settings.mode == "aws":
            try:
                from services.agents.aws_workflow import start_operation

                start_operation(workspace_id, operation_id)
            except Exception:
                raise DomainError(
                    "WORKFLOW_DISPATCH_FAILED",
                    "The operation is persisted, but AWS workflow dispatch failed. Check state-machine configuration; no local fallback was used.",
                    503,
                    True,
                )
        return {"operation_id": operation_id}

    @app.get("/api/health")
    def health():
        local = settings.mode == "local"

        def component(status, provider, detail):
            return {"status": status, "provider": provider, "detail": detail}

        missing = []
        if not local:
            missing = [
                key
                for key in (
                    "NF_TABLE_NAME",
                    "NF_EVIDENCE_BUCKET",
                    "NF_AGENTCORE_RUNTIME_ARN",
                    "NF_STATE_MACHINE_ARN",
                    "NF_PORTAL_URL",
                )
                if not os.getenv(key)
            ]
        return {
            "status": "ok" if domain else "unavailable",
            "mode": settings.mode,
            "notice": "Local demo — simulated AI and fictional agency."
            if local
            else "AWS demo — fictional agency destination. Cloud execution must be independently verified.",
            "integrations": {
                "model_provider": component(
                    "simulated"
                    if local
                    else (
                        "configured"
                        if os.getenv("NF_BEDROCK_MODEL_ID")
                        else "unavailable"
                    ),
                    "Deterministic fixture" if local else "Amazon Bedrock via Strands",
                    "No model calls in local mode."
                    if local
                    else "Real model errors are surfaced; no fixture fallback.",
                ),
                "agent_runtime": component(
                    "simulated"
                    if local
                    else (
                        "configured"
                        if os.getenv("NF_AGENTCORE_RUNTIME_ARN")
                        else "unavailable"
                    ),
                    "Local fixture functions" if local else "AgentCore Runtime",
                    "Configured is not cloud-tested.",
                ),
                "browser_provider": component(
                    "configured"
                    if local or os.getenv("NF_AGENTCORE_BROWSER_ID")
                    else "unavailable",
                    "Playwright" if local else "AgentCore Browser",
                    "Actual browser step results are recorded on each case.",
                ),
                "storage": component(
                    "configured"
                    if local and domain
                    else ("configured" if domain else "unavailable"),
                    "SQLite + private local images"
                    if local
                    else "DynamoDB + private S3",
                    problem
                    or "Persistent records; cloud smoke tests are separately gated.",
                ),
                "agency_destination": component(
                    "simulated",
                    "Demo Borough Public Works",
                    "Fictional portal. Real municipal submissions are disabled.",
                ),
            },
            "missing_configuration": missing,
        }

    @app.post("/api/demo/session")
    def demo_session(data: SessionRequest, request: Request, response: Response):
        demo_guard()
        existing = None
        try:
            existing = principal(request)
        except DomainError:
            pass
        value = backend().session(data.resident, data.workspace_id, existing)
        response.set_cookie(
            "nf_session",
            signer.dumps(value),
            httponly=True,
            secure=False,
            samesite="strict",
            max_age=7 * 86400,
            path="/",
        )
        return value

    @app.get(
        "/api/session", response_model=SessionResponse, response_model_exclude_none=True
    )
    def session(p=Depends(principal)):
        result = {key: p[key] for key in ("user", "workspace_id", "mode")}
        if settings.mode == "aws":
            result.update(
                generation=p["generation"], quotas=backend().quota_snapshot(p)
            )
        return result

    @app.post("/api/observations", response_model=Observation, status_code=201)
    def observations(data: ObservationInput, request: Request, p=Depends(principal)):
        return backend().create_observation(
            p, data.model_dump(mode="json"), request.headers.get("idempotency-key")
        )

    @app.get("/api/observations/{observation_id}", response_model=Observation)
    def observation(observation_id: str, p=Depends(principal)):
        with backend().authorized(p) as tx:
            item = backend().require(tx, "observation", observation_id)
            backend().owner(item, p)
            return item

    @app.patch("/api/observations/{observation_id}", response_model=Observation)
    def update_observation(
        observation_id: str, data: ObservationPatch, p=Depends(principal)
    ):
        return backend().patch_observation(
            p, observation_id, data.model_dump(mode="json", exclude_unset=True)
        )

    @app.post(
        "/api/observations/{observation_id}/analyze",
        response_model=OperationResponse,
        status_code=202,
    )
    def analyze(observation_id: str, p=Depends(principal)):
        return dispatch(backend().start_analysis(p, observation_id), p["workspace_id"])

    @app.post(
        "/api/observations/{observation_id}/decision",
        response_model=OperationResponse,
        status_code=202,
    )
    def decision(observation_id: str, data: DuplicateDecision, p=Depends(principal)):
        return dispatch(
            backend().decide(p, observation_id, data.incident_id, data.different_issue),
            p["workspace_id"],
        )

    @app.post("/api/observations/{observation_id}/unlink")
    def unlink(observation_id: str, p=Depends(principal)):
        return backend().unlink_observation(p, observation_id)

    @app.get("/api/operations/{operation_id}")
    def operation(operation_id: str, p=Depends(principal)):
        with backend().authorized(p) as tx:
            item = backend().require(tx, "operation", operation_id)
            backend().owner(item, p)
            return {
                k: item.get(k)
                for k in ("id", "status", "result", "error", "created_at")
            }

    @app.post("/api/uploads", status_code=201)
    async def upload(
        request: Request, file: UploadFile = File(...), p=Depends(principal)
    ):
        maximum = (4 if settings.mode == "aws" else 8) * 1024 * 1024
        content = await file.read(maximum + 1)
        await file.close()
        if len(content) > maximum:
            raise DomainError(
                "IMAGE_TOO_LARGE",
                f"Photos must be {maximum // (1024 * 1024)} MB or smaller in this mode.",
                413,
            )
        try:
            with Image.open(BytesIO(content)) as source:
                if source.format not in ("JPEG", "PNG", "WEBP"):
                    raise DomainError(
                        "UNSUPPORTED_IMAGE", "Use a JPEG, PNG, or WebP photo.", 415
                    )
                if source.width * source.height > 20_000_000:
                    raise DomainError(
                        "IMAGE_TOO_LARGE",
                        "Photos must contain at most 20 million pixels.",
                        413,
                    )
                source.verify()
            with Image.open(BytesIO(content)) as source:
                source.load()
                clean = ImageOps.exif_transpose(source).convert("RGB")
                clean.thumbnail((2000, 2000))
                # A new RGB image and no EXIF/ICC serialization remove metadata.
                sanitized = Image.new("RGB", clean.size)
                sanitized.paste(clean)
                out = BytesIO()
                sanitized.save(out, "JPEG", quality=88, optimize=True)
                safe = out.getvalue()
                if len(safe) > maximum:
                    raise DomainError(
                        "IMAGE_TOO_LARGE",
                        "The processed photo exceeds this mode’s upload limit. Choose a smaller image.",
                        413,
                    )
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            raise DomainError(
                "INVALID_IMAGE",
                "The photo could not be safely decoded. Use a smaller JPEG, PNG, or WebP.",
                415,
            )
        evidence_id = ident()
        sha = hashlib.sha256(safe).hexdigest()
        item = {
            "id": evidence_id,
            "owner_id": p["user"]["id"],
            "workspace_id": p["workspace_id"],
            "sha256": sha,
            "content_type": "image/jpeg",
            "size": len(safe),
            "created_at": iso(),
            "sanitized": True,
            "public_approved": False,
            "width": sanitized.width,
            "height": sanitized.height,
        }
        if settings.mode == "local":
            folder = backend().evidence_dir / p["workspace_id"]
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = folder / (evidence_id + ".jpg")
            path.write_bytes(safe)
            path.chmod(0o600)
            item["path"] = str(path)
        else:
            if not settings.evidence_bucket:
                raise DomainError(
                    "AWS_CONFIGURATION_REQUIRED",
                    "NF_EVIDENCE_BUCKET must be configured; no local fallback.",
                    503,
                )
            item = backend().persist_cloud_upload(
                p,
                request.headers.get("idempotency-key"),
                hashlib.sha256(content).hexdigest(),
                item,
                safe,
            )
            return backend().evidence_projection(item)
        with backend().authorized(p) as tx:
            tx.put("evidence", evidence_id, item)
        return backend().evidence_projection(item)

    @app.get("/api/evidence/{evidence_id}")
    def evidence(evidence_id: str, public: bool = False, p=Depends(principal)):
        with backend().authorized(p) as tx:
            item = backend().evidence(tx, evidence_id, p, allow_public=public)
        if public and not item["public_approved"]:
            raise DomainError(
                "EVIDENCE_NOT_SHARED",
                "This photo has not been approved for community sharing.",
                403,
            )
        path = backend().materialize_evidence(item)
        return FileResponse(
            path,
            media_type=item["content_type"],
            headers={"Content-Disposition": 'inline; filename="reviewed-photo.jpg"'},
        )

    @app.get("/api/incidents")
    def incidents(
        mine: bool = False,
        category: str | None = None,
        status: str | None = None,
        following: bool = False,
        cursor: str | None = None,
        p=Depends(principal),
    ):
        return backend().list_incidents(p, mine, category, status, following, cursor)

    @app.get("/api/incidents/{incident_id}", response_model=Incident)
    def incident(incident_id: str, p=Depends(principal)):
        return backend().incident_detail(p, incident_id)

    @app.post(
        "/api/incidents/{incident_id}/draft",
        response_model=SubmissionDraft,
        status_code=201,
    )
    def draft(incident_id: str, data: DraftInput, p=Depends(principal)):
        return backend().create_draft(
            p, incident_id, data.model_dump(mode="json", exclude_none=True)
        )

    @app.post(
        "/api/incidents/{incident_id}/approve",
        response_model=OperationResponse,
        status_code=202,
    )
    def approve(incident_id: str, data: ApprovalInput, p=Depends(principal)):
        operation_id = backend().approve(p, incident_id, data.model_dump())
        result = dispatch(operation_id, p["workspace_id"])
        if settings.mode == "aws":
            from services.agents.aws_workflow import wake_approval

            wake_approval(p["workspace_id"], incident_id, data.draft_id)
        return result

    @app.post("/api/incidents/{incident_id}/cancel")
    def cancel(incident_id: str, p=Depends(principal)):
        return backend().cancel(p, incident_id)

    @app.post(
        "/api/incidents/{incident_id}/reconcile",
        response_model=OperationResponse,
        status_code=202,
    )
    def reconcile(incident_id: str, p=Depends(principal)):
        return dispatch(backend().reconcile(p, incident_id), p["workspace_id"])

    @app.get("/api/incidents/{incident_id}/events")
    def events(incident_id: str, cursor: str | None = None, p=Depends(principal)):
        with backend().authorized(p) as tx:
            inc = backend().require(tx, "incident", incident_id)
            backend().accessible(tx, inc, p)
            source = tx.list("event:" + incident_id, limit=101, after=cursor)
            return {
                "items": [
                    backend().event_projection(e)
                    for e in source[:100]
                    if e["incident_id"] == incident_id
                ],
                "next_cursor": source[99]["id"] if len(source) > 100 else None,
            }

    @app.post("/api/incidents/{incident_id}/subscription")
    def subscription(incident_id: str, data: SubscriptionInput, p=Depends(principal)):
        with backend().authorized(p) as tx:
            inc = backend().require(tx, "incident", incident_id)
            backend().accessible(tx, inc, p)
            backend().subscribe(tx, incident_id, p["user"]["id"], data.following)
        return {"following": data.following}

    @app.post("/api/incidents/{incident_id}/verify")
    def verify(incident_id: str, data: VerificationInput, p=Depends(principal)):
        return backend().verification(p, incident_id, data.model_dump())

    @app.get("/api/incidents/{incident_id}/packet")
    def packet(incident_id: str, p=Depends(principal)):
        detail = backend().incident_detail(p, incident_id)
        if not detail["is_owner"]:
            raise DomainError(
                "FORBIDDEN",
                "Only the reporting resident can download the private report packet.",
                403,
            )
        packet = {
            k: detail.get(k)
            for k in (
                "id",
                "title",
                "description",
                "category",
                "latitude",
                "longitude",
                "location_label",
                "draft",
                "routing",
                "analysis",
                "observations",
            )
        }
        packet.update(
            automatic_submission=False,
            instructions="Review the recipient and requirements before manually sending. This download does not authorize or send any report.",
        )
        return JSONResponse(
            packet,
            headers={
                "Content-Disposition": 'attachment; filename="neighborhood-fixer-report.json"'
            },
        )

    @app.get("/api/notifications")
    def notifications(cursor: str | None = None, p=Depends(principal)):
        with backend().authorized(p) as tx:
            items = [
                {k: v for k, v in n.items() if k not in ("workspace_id", "user_id")}
                for n in tx.list(
                    "notification:" + p["user"]["id"], limit=200, after=cursor
                )
                if n["user_id"] == p["user"]["id"]
            ]
            return {
                "items": sorted(items, key=lambda n: n["created_at"], reverse=True),
                "next_cursor": None,
            }

    @app.post("/api/notifications/{notification_id}/read")
    def read_notification(notification_id: str, p=Depends(principal)):
        with backend().authorized(p) as tx:
            item = backend().require(tx, "notification", notification_id)
            if item["user_id"] != p["user"]["id"]:
                raise DomainError("NOT_FOUND", "Notification not found.", 404)
            item["read"] = True
            tx.put("notification", notification_id, item)
            tx.put("notification:" + p["user"]["id"], notification_id, item)
        return {"read": True}

    @app.get("/api/registry/demo-borough")
    def registry():
        return {
            "id": "demo-borough",
            "name": "Demo Borough Public Works",
            "version": 1,
            "fictional": True,
            "coverage": {
                "south": 47.60,
                "north": 47.63,
                "west": -122.35,
                "east": -122.32,
            },
            "supported_categories": [c.value for c in Category],
            "asset_conditions": [
                "Resident confirmed public walkway or street; ownership remains subject to agency review."
            ],
            "required_fields": ["category", "description", "confirmed location"],
            "capabilities": {
                "submit": True,
                "status": True,
                "attachments": True,
                "idempotency": "unique attempt ID in fictional portal",
            },
            "destination": settings.portal_url,
            "automation_authorized": True,
            "reviewed_at": "2026-09-13",
            "sources": [
                {
                    "title": "Trusted fictional demo configuration",
                    "url": "/api/registry/demo-borough",
                    "kind": "fictional_configuration",
                }
            ],
        }

    @app.post("/api/demo/clock")
    def clock(data: ClockInput, p=Depends(principal)):
        demo_guard()
        with backend().authorized(p) as tx:
            ws = backend().require(tx, "workspace", p["workspace_id"])
            ws["clock_offset"] += data.advance_seconds
            tx.put("workspace", ws["id"], ws)
            return {
                "now": iso(backend().now(tx)),
                "advanced_seconds": data.advance_seconds,
            }

    @app.post("/api/demo/scenario")
    def scenario(data: ScenarioInput, p=Depends(principal)):
        demo_guard()
        with backend().authorized(p) as tx:
            ws = backend().require(tx, "workspace", p["workspace_id"])
            ws["lost_receipt"] = data.lost_receipt
            tx.put("workspace", ws["id"], ws)
        return {"lost_receipt": data.lost_receipt}

    @app.post("/api/demo/tickets/{incident_id}/status")
    async def ticket_status(
        incident_id: str, data: TicketStatusInput, p=Depends(principal)
    ):
        demo_guard()
        with backend().authorized(p) as tx:
            inc = backend().require(tx, "incident", incident_id)
            backend().accessible(tx, inc, p)
            if not inc.get("ticket_id"):
                raise DomainError(
                    "TICKET_REQUIRED",
                    "This incident does not yet have a confirmed ticket.",
                    409,
                )
            ticket = backend().require(tx, "ticket", inc["ticket_id"])
        from services.worker.browser import set_ticket_status

        status = await set_ticket_status(
            ticket["receipt_id"], data.status, data.closure_note
        )
        with backend().authorized(p) as tx:
            backend().record_status(tx, incident_id, status)
        return {"agency_status": status.get("normalized_status", data.status)}

    @app.post("/api/demo/reset")
    def reset(p=Depends(principal)):
        demo_guard()
        with backend().authorized(p) as tx:
            for kind in tx.kinds():
                if kind in ("workspace", "user"):
                    continue
                for record in tx.list(kind, limit=500):
                    if (
                        kind == "job"
                        and record.get("status") == "running"
                        and record.get("lease_until", 0) > backend().now(tx)
                    ):
                        raise DomainError(
                            "WORKSPACE_BUSY",
                            "Wait for active work to finish before resetting this workspace.",
                            409,
                        )
                for record in tx.list(kind, limit=500):
                    tx.delete(kind, record["id"])
            ws = backend().require(tx, "workspace", p["workspace_id"])
            ws.update(clock_offset=0, lost_receipt=False)
            tx.put("workspace", ws["id"], ws)
            backend().seed(tx)
        return {"reset": True, "workspace_id": p["workspace_id"]}

    @app.get("/api/demo/fixtures")
    def fixture_catalog(p=Depends(principal)):
        demo_guard()
        return {
            "before": "/api/demo/fixtures/before",
            "after": "/api/demo/fixtures/after",
            "pothole": "/api/demo/fixtures/pothole",
            "attribution": "Original synthetic illustrations for this fictional demo; not real observations.",
        }

    @app.get("/api/demo/fixtures/{name}")
    def fixture(name: str, p=Depends(principal)):
        demo_guard()
        names = {
            "before": "curb-before.png",
            "after": "curb-after.png",
            "pothole": "pothole.png",
        }
        if name not in names:
            raise DomainError("NOT_FOUND", "Illustration not found.", 404)
        path = Path(__file__).resolve().parents[2] / "fixtures" / "images" / names[name]
        if not path.exists():
            raise DomainError(
                "FIXTURE_UNAVAILABLE",
                "This illustration has not been generated yet.",
                404,
            )
        return FileResponse(path, media_type="image/png")

    return app


app = create_app()

# Mangum supplies the trusted API Gateway event for Clerk JWT authorization.
try:
    from mangum import Mangum

    handler = Mangum(app, lifespan="off")
except ImportError:
    handler = None
