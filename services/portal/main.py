"""Fictional receiving agency. Private grants and receipts; no municipal integration."""

from __future__ import annotations
import hashlib
import hmac
import html
import io
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

app = FastAPI(title="Demo Borough fictional agency portal", version="1.0.0")
ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = {"damaged_sidewalk", "pothole", "walkway_obstruction"}
PORTAL_RECIPIENT = "Demo Borough Public Works"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_dir() -> Path:
    path = Path(os.getenv("NF_DATA_DIR", str(ROOT / ".local"))) / "portal"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def db():
    path = data_dir() / "portal.sqlite3"
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    path.chmod(0o600)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind,id))"
    )
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def get(kind: str, key: str) -> dict | None:
    if os.getenv("NF_MODE", "local") == "aws":
        import boto3

        table = boto3.resource("dynamodb").Table(os.environ["NF_PORTAL_TABLE"])
        item = table.get_item(
            Key={"pk": f"PORTAL#{kind}#{key}", "sk": "META"}, ConsistentRead=True
        ).get("Item")
        return json.loads(item["data"]) if item else None
    with db() as con:
        row = con.execute(
            "SELECT data FROM records WHERE kind=? AND id=?", (kind, key)
        ).fetchone()
        return json.loads(row[0]) if row else None


def put(kind: str, key: str, value: dict, only_new=False):
    raw = json.dumps(value, sort_keys=True)
    if os.getenv("NF_MODE", "local") == "aws":
        import boto3

        args = {"Item": {"pk": f"PORTAL#{kind}#{key}", "sk": "META", "data": raw}}
        if only_new:
            args["ConditionExpression"] = "attribute_not_exists(pk)"
        boto3.resource("dynamodb").Table(os.environ["NF_PORTAL_TABLE"]).put_item(**args)
        return
    with db() as con:
        command = "INSERT" if only_new else "INSERT OR REPLACE"
        con.execute(
            f"{command} INTO records(kind,id,data) VALUES(?,?,?)", (kind, key, raw)
        )


def admin(request: Request):
    secret = os.getenv("NF_PORTAL_SECRET", "")
    supplied = request.headers.get("x-portal-secret", "")
    if not secret or not hmac.compare_digest(secret, supplied):
        raise HTTPException(403, "Portal service authorization required")


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Demo Borough</title><style>
    *{{box-sizing:border-box}}body{{background:#f5f4ed;color:#243e3a;font:16px/1.6 system-ui;margin:0}}header{{padding:24px max(24px,calc((100% - 780px)/2));background:#164f47;color:white}}main{{max-width:780px;margin:40px auto;padding:0 24px}}h1{{line-height:1.2;font-size:32px}}.notice{{padding:12px 18px;background:#fbebc4;border-left:3px solid #9b7029}}form{{display:grid;gap:18px}}label{{display:grid;gap:6px;font-weight:600}}input,textarea,select,button{{font:inherit;padding:12px;border:1px solid #8caaa2;border-radius:5px;background:white;color:#243e3a}}input[readonly]{{background:#eaece5}}textarea{{min-height:120px}}button{{background:#17695d;color:white;border:0;cursor:pointer;font-weight:650}}:focus-visible{{outline:3px solid #b17d17;outline-offset:3px}}dl{{display:grid;grid-template-columns:140px 1fr;gap:12px}}dt{{font-weight:600}}dd{{margin:0;overflow-wrap:anywhere}}a{{color:#17695d}}code{{word-break:break-all}}</style></head><body><header><strong>Demo Borough Public Works</strong> · Fictional agency</header><main><p class="notice">Demonstration portal — no request reaches a real municipality.</p><h1>{esc(title)}</h1>{body}</main></body></html>"""


@app.middleware("http")
async def security(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def status_management_allowed() -> bool:
    mode = os.getenv("NF_MODE", "local")
    environment = os.getenv("NF_ENVIRONMENT", "development")
    return (mode == "local" and environment == "development") or (
        mode == "aws"
        and environment == "demo"
        and os.getenv("NF_ENABLE_DEMO_STATUS_MANAGEMENT") == "true"
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "destination": "fictional",
        "mode": os.getenv("NF_MODE", "local"),
        "environment": os.getenv("NF_ENVIRONMENT", "development"),
        "status_management_enabled": status_management_allowed(),
    }


@app.get("/", response_class=HTMLResponse)
def home():
    return page(
        "A small repair. A more accessible neighborhood.",
        "<p>This is a functional fictional receiving portal for Neighborhood Fixer. Reports arrive only through an authorized, resident-approved transfer. Receipt links are private.</p>",
    )


class Grant(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]
    workspace_id: str = Field(default="local", max_length=128)


@app.post("/internal/grants")
def create_grant(body: Grant, request: Request):
    admin(request)
    p = body.payload
    if p.get("recipient") != PORTAL_RECIPIENT or p.get("category") not in CATEGORIES:
        raise HTTPException(422, "Unsupported destination or category")
    if (
        not p.get("description")
        or not p.get("location_label")
        or not p.get("payload_hash")
    ):
        raise HTTPException(422, "An exact approved report is required")
    key = secrets.token_urlsafe(32)
    put(
        "grant",
        key,
        {
            "id": key,
            "attempt_id": body.attempt_id,
            "workspace_id": body.workspace_id,
            "payload": p,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        },
    )
    return {"grant": key, "expires_in": 600}


def grant_for(key: str):
    grant = get("grant", key)
    if not grant or datetime.fromisoformat(grant["expires_at"]) < datetime.now(
        timezone.utc
    ):
        raise HTTPException(410, "Transfer expired. Return to Neighborhood Fixer.")
    return grant


@app.get("/report/{key}", response_class=HTMLResponse)
def report(key: str):
    g = grant_for(key)
    p = g["payload"]
    options = "".join(
        f'<option value="{esc(c)}">{esc(c.replace("_", " ").title())}</option>'
        for c in sorted(CATEGORIES)
    )
    return page(
        "Submit a maintenance request",
        f'''<p>The sending application has a time-limited transfer for this exact report. Changed fields will be rejected.</p>
    <form method="post" action="/report/{esc(key)}" enctype="multipart/form-data" data-contract="nf-portal-v1">
    <label>Recipient<input name="recipient" value="{esc(PORTAL_RECIPIENT)}" readonly required></label>
    <label>Category<select name="category" required>{options}</select></label>
    <label>What needs attention?<textarea name="description" required maxlength="5000"></textarea></label>
    <label>Confirmed location<input name="location_label" required maxlength="300"></label>
    <input name="latitude" type="hidden"><input name="longitude" type="hidden">
    <label>Approved contact details<input name="contact" readonly value="{esc(json.dumps(p.get("contact", {}), sort_keys=True))}"></label>
    <label>Approved photos<input type="file" name="attachments" accept="image/png,image/jpeg,image/webp" multiple></label>
    <p>Only the approved report and selected photos will be stored by this fictional portal.</p>
    <button type="submit">Submit approved report</button></form>''',
    )


async def read_safe_upload(upload) -> bytes:
    maximum = (4 if os.getenv("NF_MODE", "local") == "aws" else 8) * 1024 * 1024
    raw = await upload.read(maximum + 1)
    if len(raw) > maximum:
        raise HTTPException(413, "Photo exceeds this mode’s upload limit")
    try:
        img = Image.open(io.BytesIO(raw))
        if (
            img.format not in {"PNG", "JPEG", "WEBP"}
            or img.width * img.height > 25_000_000
        ):
            raise ValueError()
        img.verify()
    except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, "Invalid image")
    return raw


def receipt_key(attempt_id: str) -> str:
    return "DB-" + hashlib.sha256(attempt_id.encode()).hexdigest()[:12].upper()


def same_submission(ticket: dict, grant: dict) -> bool:
    fields = (
        "recipient",
        "category",
        "description",
        "location_label",
        "latitude",
        "longitude",
        "contact",
        "attachment_ids",
        "attachment_hashes",
        "payload_hash",
    )
    return (
        ticket.get("attempt_id") == grant["attempt_id"]
        and ticket.get("workspace_id") == grant["workspace_id"]
        and all(
            ticket["payload"].get(field) == grant["payload"].get(field)
            for field in fields
        )
    )


def persist_attachments(receipt_id: str, photos: list[bytes]) -> list[dict]:
    """Persist immutable content before making an acceptance receipt observable.

    A failed write may leave an unreferenced private object, but cannot leave a
    received ticket claiming evidence that was never retained. Hash-addressed
    objects also prevent racing requests from overwriting accepted evidence.
    """
    manifest = []
    for raw in photos:
        sha = hashlib.sha256(raw).hexdigest()
        key = f"portal/{receipt_id}/{sha}.image"
        if os.getenv("NF_MODE", "local") == "aws":
            import boto3

            boto3.client("s3").put_object(
                Bucket=os.environ["NF_EVIDENCE_BUCKET"],
                Key=key,
                Body=raw,
                ContentType="application/octet-stream",
                ServerSideEncryption="AES256",
            )
        else:
            target = data_dir() / receipt_id
            target.mkdir(mode=0o700, exist_ok=True)
            destination = target / (sha + ".image")
            temporary = target / (sha + "." + secrets.token_hex(8) + ".pending")
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        manifest.append({"sha256": sha, "size": len(raw), "object_key": key})
    return manifest


@app.post("/report/{key}")
async def submit(key: str, request: Request):
    g = grant_for(key)
    p = g["payload"]
    form = await request.form(max_files=5, max_fields=12, max_part_size=8 * 1024 * 1024)
    allowed = {
        "recipient",
        "category",
        "description",
        "location_label",
        "latitude",
        "longitude",
        "contact",
        "attachments",
    }
    if set(form.keys()) - allowed:
        raise HTTPException(422, "Unexpected fields require review")
    for name in ["recipient", "category", "description", "location_label"]:
        if str(form.get(name, "")) != str(p.get(name, "")):
            raise HTTPException(409, f"Approved {name} changed")
    for name in ["latitude", "longitude"]:
        try:
            if float(form.get(name, "nan")) != float(p[name]):
                raise ValueError()
        except (ValueError, KeyError):
            raise HTTPException(409, "Approved coordinates changed")
    if form.get("contact") != json.dumps(p.get("contact", {}), sort_keys=True):
        raise HTTPException(409, "Approved contacts changed")
    photos = []
    for upload in form.getlist("attachments"):
        if getattr(upload, "filename", None):
            photos.append(await read_safe_upload(upload))
    if (
        os.getenv("NF_MODE", "local") == "aws"
        and sum(map(len, photos)) > 4 * 1024 * 1024
    ):
        raise HTTPException(413, "AWS demo supports at most 4 MB of photos per report")
    hashes = [hashlib.sha256(raw).hexdigest() for raw in photos]
    if sorted(hashes) != sorted(p.get("attachment_hashes", [])):
        raise HTTPException(409, "Approved attachments changed")
    attempt_id = g["attempt_id"]
    receipt_id = receipt_key(attempt_id)
    existing = get("ticket", receipt_id)
    if existing:
        if not same_submission(existing, g):
            raise HTTPException(
                409, "This attempt is already bound to a different approved report"
            )
        return RedirectResponse(
            f"/receipt/{receipt_id}?access={existing['access']}", 303
        )
    # Durable attachment writes precede acceptance. Never claim a received ticket
    # when its approved evidence could not be stored.
    manifest = persist_attachments(receipt_id, photos)
    access = secrets.token_urlsafe(32)
    ticket = {
        "id": receipt_id,
        "receipt_id": receipt_id,
        "attempt_id": attempt_id,
        "workspace_id": g["workspace_id"],
        "access": access,
        "payload": p,
        "raw_status": "Received",
        "normalized_status": "RECEIVED",
        "closure_note": "",
        "created_at": now(),
        "updated_at": now(),
        "history": [{"status": "Received", "at": now()}],
        "attachment_count": len(photos),
        "attachment_manifest": manifest,
    }
    try:
        put("ticket", receipt_id, ticket, only_new=True)
    except Exception:
        ticket = get("ticket", receipt_id)
        if not ticket:
            raise
        if not same_submission(ticket, g):
            raise HTTPException(
                409, "This attempt is already bound to a different approved report"
            )
    # This lookup index is repairable: receipt lookup also derives the canonical
    # key after a crash between accepting the ticket and writing this index.
    put("attempt", attempt_id, {"receipt_id": receipt_id})
    return RedirectResponse(f"/receipt/{receipt_id}?access={ticket['access']}", 303)


@app.get("/receipt/{receipt_id}", response_class=HTMLResponse)
def receipt(receipt_id: str, access: str = ""):
    t = get("ticket", receipt_id)
    if not t or not access or not hmac.compare_digest(t["access"], access):
        raise HTTPException(404, "Receipt not found")
    p = t["payload"]
    return page(
        "Request received",
        f"""<p>Keep this private receipt link to follow your fictional request.</p><dl><dt>Receipt</dt><dd id="receipt-id">{esc(t["id"])}</dd><dt>Agency status</dt><dd id="ticket-status">{esc(t["raw_status"])}</dd><dt>Recipient</dt><dd>{esc(p["recipient"])}</dd><dt>Location</dt><dd>{esc(p["location_label"])}</dd><dt>Report</dt><dd>{esc(p["description"])}</dd><dt>Attachments</dt><dd>{t["attachment_count"]}</dd><dt>Closure notes</dt><dd>{esc(t["closure_note"] or "None")}</dd></dl><p>Agency closure does not establish that a physical problem was repaired.</p>""",
    )


@app.get("/internal/receipts/{attempt_id}")
def lookup(attempt_id: str, request: Request):
    admin(request)
    a = get("attempt", attempt_id)
    # A crash after remote acceptance can precede the attempt index; derive the strongly consistent key.
    rid = a["receipt_id"] if a else receipt_key(attempt_id)
    ticket = get("ticket", rid)
    if not ticket or ticket.get("attempt_id") != attempt_id:
        raise HTTPException(404, "Receipt not found")
    return ticket


@app.get("/internal/tickets/{receipt_id}")
def status(receipt_id: str, request: Request):
    admin(request)
    t = get("ticket", receipt_id)
    if not t:
        raise HTTPException(404, "Ticket not found")
    return t


class StatusChange(BaseModel):
    status: str
    closure_note: str = Field(default="", max_length=1000)


@app.post("/internal/tickets/{receipt_id}/status")
def change_status(receipt_id: str, body: StatusChange, request: Request):
    admin(request)
    if not status_management_allowed():
        raise HTTPException(
            403, "Fictional ticket status management is disabled in this environment"
        )
    if body.status not in {"OPEN", "IN_PROGRESS", "CLOSED"}:
        raise HTTPException(422, "Unsupported ticket status")
    t = get("ticket", receipt_id)
    if not t:
        raise HTTPException(404, "Ticket not found")
    t.update(
        normalized_status=body.status,
        raw_status=body.status.replace("_", " ").title(),
        closure_note=body.closure_note,
        updated_at=now(),
    )
    t["history"].append(
        {"status": t["raw_status"], "note": body.closure_note, "at": now()}
    )
    put("ticket", receipt_id, t)
    return t


@app.exception_handler(HTTPException)
async def portal_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/internal/"):
        return JSONResponse(
            {
                "error": {
                    "code": "PORTAL_REJECTED",
                    "message": str(exc.detail),
                    "retryable": False,
                }
            },
            status_code=exc.status_code,
        )
    return HTMLResponse(
        page("This request needs attention", f"<p>{esc(exc.detail)}</p>"),
        status_code=exc.status_code,
    )


# Supported ASGI-to-Lambda adapter. Deployment must provide persistent AWS storage.
from mangum import Mangum

handler = Mangum(app, lifespan="off")
