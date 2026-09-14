"""Bounded, deterministic agency adapter. Models cannot choose URLs or write fields."""

from __future__ import annotations
import asyncio
import hashlib
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright


class OutcomeUnknown(RuntimeError):
    """An external write may have happened; reconciliation, never automatic resubmission."""


class HandoffRequired(RuntimeError):
    """No write: destination, form or authorization needs human review."""


class FailedBeforeSubmission(RuntimeError):
    """No external write was begun."""


class AgencyAdapter(Protocol):
    async def get_service_catalog(self) -> dict: ...
    async def get_required_fields(self, category: str) -> list[str]: ...
    async def validate_report(self, report: dict) -> None: ...
    async def prepare_submission(self, report: dict) -> dict: ...
    async def submit_approved_report(
        self, report: dict, attempt_id: str, evidence_paths: list[str]
    ) -> dict: ...
    async def get_receipt(self, attempt_id: str) -> dict | None: ...
    async def get_status(self, receipt_id: str) -> dict: ...
    async def prepare_followup(self, report: dict, message: str) -> dict: ...


def portal_url() -> str:
    base = os.getenv("NF_PORTAL_URL", "http://127.0.0.1:8001").rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise HandoffRequired(
            "Portal must be a configured origin without credentials or paths"
        )
    local = (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and os.getenv("NF_MODE", "local") == "local"
    )
    if not local and parsed.scheme != "https":
        raise HandoffRequired("Cloud browser needs a reachable HTTPS portal origin")
    allowed = os.getenv("NF_PORTAL_ALLOWED_ORIGINS", base).split(",")
    if base not in [x.strip().rstrip("/") for x in allowed]:
        raise HandoffRequired("Portal origin is not allowlisted")
    if os.getenv("NF_MODE", "local") == "aws" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise HandoffRequired("Cloud browser cannot use localhost")
    return base


def service_headers() -> dict:
    secret = os.getenv("NF_PORTAL_SECRET", "")
    if not secret:
        raise FailedBeforeSubmission(
            "NF_PORTAL_SECRET is missing; restart with scripts/dev.py"
        )
    return {"x-portal-secret": secret}


async def portal_request(method: str, path: str, **kwargs):
    # Read retries are finite. Never retry POST submissions here.
    attempts = 3 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.request(
                    method, portal_url() + path, headers=service_headers(), **kwargs
                )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(
                0.25 * (2**attempt) + __import__("random").uniform(0, 0.1)
            )


def result(ticket: dict) -> dict:
    return {
        "receipt_id": ticket["receipt_id"],
        "url": f"{portal_url()}/receipt/{ticket['receipt_id']}?access={ticket['access']}",
        "raw_status": ticket["raw_status"],
        "normalized_status": ticket["normalized_status"],
        "closure_note": ticket.get("closure_note", ""),
        "history": ticket.get("history", []),
    }


async def lookup_receipt(attempt_id: str) -> dict | None:
    t = await portal_request("GET", f"/internal/receipts/{attempt_id}")
    return result(t) if t else None


async def get_ticket_status(receipt_id: str) -> dict:
    t = await portal_request("GET", f"/internal/tickets/{receipt_id}")
    if not t:
        raise RuntimeError("Receipt is no longer available; manual review required")
    return result(t)


async def set_ticket_status(receipt_id: str, status: str, note: str = "") -> dict:
    t = await portal_request(
        "POST",
        f"/internal/tickets/{receipt_id}/status",
        json={"status": status, "closure_note": note},
    )
    if not t:
        raise RuntimeError("Ticket not found")
    return result(t)


@asynccontextmanager
async def browser_session(playwright):
    if os.getenv("NF_MODE", "local") == "aws":
        from services.agents.browser_provider import open_agentcore_browser

        async with open_agentcore_browser(playwright) as browser:
            yield browser
    else:
        browser = await playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            await browser.close()


async def submit_report(
    payload: dict,
    attempt_id: str,
    evidence_paths: list[str],
    screenshot_path: str | None = None,
    before_write=None,
) -> dict:
    base = portal_url()
    if payload.get("recipient") != "Demo Borough Public Works":
        raise HandoffRequired("Recipient does not match the trusted registry")
    if not payload.get("payload_hash"):
        raise HandoffRequired("No frozen payload authorization")
    files = []
    for file in evidence_paths:
        path = Path(file)
        raw = path.read_bytes()
        files.append(
            {
                "name": path.name,
                "mimeType": mimetypes.guess_type(path.name)[0] or "image/png",
                "buffer": raw,
            }
        )
    if (
        os.getenv("NF_MODE", "local") == "aws"
        and sum(len(f["buffer"]) for f in files) > 4 * 1024 * 1024
    ):
        raise HandoffRequired(
            "AWS demo reports support at most 4 MB of photos in total. Remove photos and approve a fresh revision."
        )
    if sorted(hashlib.sha256(f["buffer"]).hexdigest() for f in files) != sorted(
        payload.get("attachment_hashes", [])
    ):
        raise HandoffRequired("Attachment content changed after approval")
    write_started = False
    try:
        grant = await portal_request(
            "POST",
            "/internal/grants",
            json={
                "attempt_id": attempt_id,
                "payload": payload,
                "workspace_id": payload.get("workspace_id", "local"),
            },
        )
        if not grant or not isinstance(grant.get("grant"), str):
            raise HandoffRequired(
                "The portal did not return a valid authorized transfer"
            )
        async with asyncio.timeout(int(os.getenv("NF_BROWSER_TIMEOUT_SECONDS", "90"))):
            async with async_playwright() as p:
                async with browser_session(p) as browser:
                    context = await browser.new_context(
                        viewport={"width": 1100, "height": 850}
                    )

                    async def allow_origin(route):
                        target = urlsplit(route.request.url)
                        if f"{target.scheme}://{target.netloc}" != base:
                            await route.abort("blockedbyclient")
                        else:
                            await route.continue_()

                    await context.route("**/*", allow_origin)
                    page = await context.new_page()
                    page.set_default_timeout(15000)
                    try:
                        response = await page.goto(
                            f"{base}/report/{grant['grant']}",
                            wait_until="domcontentloaded",
                        )
                        if not response or response.status != 200:
                            raise HandoffRequired(
                                "Portal transfer expired or unavailable"
                            )
                        if await page.locator(
                            'iframe, input[type="password"], [data-captcha]'
                        ).count():
                            raise HandoffRequired(
                                "Portal requires authentication or challenge; human handoff required"
                            )
                        form = page.locator('form[data-contract="nf-portal-v1"]')
                        if await form.count() != 1:
                            raise HandoffRequired(
                                "Portal form changed; review required"
                            )
                        names = await form.locator(
                            "input,select,textarea"
                        ).evaluate_all("nodes => nodes.map(n => n.name).sort()")
                        expected = sorted(
                            [
                                "recipient",
                                "category",
                                "description",
                                "location_label",
                                "latitude",
                                "longitude",
                                "contact",
                                "attachments",
                            ]
                        )
                        if names != expected:
                            raise HandoffRequired(
                                "Unexpected portal fields require fresh review"
                            )
                        if (
                            await form.get_attribute("action")
                            != f"/report/{grant['grant']}"
                        ):
                            raise HandoffRequired("Portal form destination changed")
                        if (
                            await page.locator('[name="recipient"]').input_value()
                            != payload["recipient"]
                        ):
                            raise HandoffRequired("Portal recipient changed")
                        await page.locator('[name="category"]').select_option(
                            payload["category"]
                        )
                        for name in ["description", "location_label"]:
                            await page.locator(f'[name="{name}"]').fill(
                                str(payload[name])
                            )
                        for name in ["latitude", "longitude"]:
                            await page.locator(f'[name="{name}"]').evaluate(
                                "(el,value)=>el.value=value", str(payload[name])
                            )
                        import json

                        if await page.locator(
                            '[name="contact"]'
                        ).input_value() != json.dumps(
                            payload.get("contact", {}), sort_keys=True
                        ):
                            raise HandoffRequired("Portal contact consent changed")
                        if files:
                            await page.locator('[name="attachments"]').set_input_files(
                                files
                            )
                        if before_write is not None:
                            import inspect

                            check = before_write()
                            if inspect.isawaitable(check):
                                await check
                        # Portal form content is untrusted; no model reads it as instructions.
                        write_started = True
                        await page.get_by_role(
                            "button", name="Submit approved report", exact=True
                        ).click()
                        await page.wait_for_url(f"{base}/receipt/**")
                        receipt = await page.locator("#receipt-id").inner_text()
                        status = await page.locator("#ticket-status").inner_text()
                        if payload.get("simulate_lost_receipt"):
                            raise OutcomeUnknown(
                                "Connection lost after external acceptance. Reconcile the existing attempt; do not resubmit."
                            )
                        output = {
                            "receipt_id": receipt,
                            "url": page.url,
                            "raw_status": status,
                            "normalized_status": "RECEIVED",
                            "closure_note": "",
                            "steps": [
                                "Approved transfer opened",
                                "Exact fields and attachments validated",
                                "Submit clicked",
                                "Receipt captured",
                            ],
                        }
                        if screenshot_path:
                            path = Path(screenshot_path)
                            # Receipt renders no contact; obscure resident free text/location before retaining browser evidence.
                            try:
                                path.parent.mkdir(parents=True, exist_ok=True)
                                await page.screenshot(
                                    path=str(path),
                                    full_page=True,
                                    mask=[
                                        page.locator("dd").nth(3),
                                        page.locator("dd").nth(4),
                                    ],
                                )
                                output["screenshot_path"] = str(path)
                            except Exception:
                                # Receipt is already confirmed. Optional screenshot
                                # failure must not turn a known outcome into unknown.
                                output["steps"].append(
                                    "Receipt confirmed; optional screenshot unavailable"
                                )
                        return output
                    finally:
                        await context.close()
    except (OutcomeUnknown, HandoffRequired):
        raise
    except Exception as exc:
        if write_started:
            raise OutcomeUnknown(
                "Browser disconnected after a possible submission. Receipt reconciliation is required."
            ) from exc
        raise FailedBeforeSubmission(
            f"Browser could not prepare the authorized report ({type(exc).__name__})."
        ) from exc


class DemoPortalAdapter:
    capabilities = {
        "categories": ["damaged_sidewalk", "pothole", "walkway_obstruction"],
        "submission": True,
        "status": True,
        "attachments": True,
        "idempotency": True,
        "human_handoff": False,
    }

    async def get_service_catalog(self):
        return {
            **self.capabilities,
            "recipient": "Demo Borough Public Works",
            "allowed_destinations": [portal_url()],
        }

    async def get_required_fields(self, category):
        return ["description", "location_label", "latitude", "longitude"]

    async def validate_report(self, report):
        if (
            report.get("recipient") != "Demo Borough Public Works"
            or report.get("category") not in self.capabilities["categories"]
        ):
            raise HandoffRequired("Unsupported route")
        for field in await self.get_required_fields(report.get("category")):
            if report.get(field) is None or report.get(field) == "":
                raise HandoffRequired(f"Missing {field}")

    async def prepare_submission(self, report):
        await self.validate_report(report)
        return dict(report)

    async def submit_approved_report(self, report, attempt_id, evidence_paths):
        return await submit_report(report, attempt_id, evidence_paths)

    async def get_receipt(self, attempt_id):
        return await lookup_receipt(attempt_id)

    async def get_status(self, receipt_id):
        return await get_ticket_status(receipt_id)

    async def prepare_followup(self, report, message):
        return {
            "recipient": report["recipient"],
            "message": message,
            "requires_fresh_approval": True,
        }


class AssistedHandoffAdapter:
    capabilities = {
        "submission": False,
        "status": False,
        "attachments": True,
        "idempotency": False,
        "human_handoff": True,
    }

    async def get_service_catalog(self):
        return self.capabilities

    async def get_required_fields(self, category):
        return ["description", "location_label"]

    async def validate_report(self, report):
        if not report.get("description") or not report.get("location_label"):
            raise HandoffRequired("Complete report details first")

    async def prepare_submission(self, report):
        await self.validate_report(report)
        return {
            "report": dict(report),
            "status": "HANDOFF_REQUIRED",
            "destination_verified": bool(report.get("verified_destination")),
            "instructions": "Review the report packet and use a verified agency contact. No report has been sent.",
        }

    async def submit_approved_report(self, *args, **kwargs):
        raise HandoffRequired("This route requires assisted handoff")

    async def get_receipt(self, attempt_id):
        return None

    async def get_status(self, receipt_id):
        return {"normalized_status": "UNKNOWN"}

    async def prepare_followup(self, report, message):
        return {"message": message, "requires_fresh_approval": True}
