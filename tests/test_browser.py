"""Actual local Chromium flow. Uses an isolated persisted portal, no AWS or real agency."""

import hashlib
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from PIL import Image
from services.worker.browser import (
    submit_report,
    lookup_receipt,
    OutcomeUnknown,
    HandoffRequired,
    portal_url,
)


@pytest.fixture
def live_portal(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    for k, v in {
        "NF_MODE": "local",
        "NF_ENVIRONMENT": "development",
        "NF_DATA_DIR": str(tmp_path),
        "NF_PORTAL_SECRET": "test-browser-private",
        "NF_PORTAL_URL": f"http://127.0.0.1:{port}",
        "NF_PORTAL_ALLOWED_ORIGINS": f"http://127.0.0.1:{port}",
    }.items():
        monkeypatch.setenv(k, v)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.portal.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(100):
            try:
                if (
                    httpx.get(
                        f"http://127.0.0.1:{port}/health", timeout=0.2
                    ).status_code
                    == 200
                ):
                    break
            except httpx.TransportError:
                time.sleep(0.05)
        else:
            raise AssertionError("Portal did not become healthy")
        yield tmp_path
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def payload(tmp):
    path = tmp / "photo.png"
    Image.new("RGB", (100, 100), "tan").save(path)
    p = {
        "recipient": "Demo Borough Public Works",
        "category": "damaged_sidewalk",
        "description": "Broken ramp edge observed by resident.",
        "location_label": "Fictional Maple and Alder",
        "latitude": 47.615,
        "longitude": -122.335,
        "contact": {},
        "attachment_hashes": [hashlib.sha256(path.read_bytes()).hexdigest()],
        "payload_hash": "b" * 64,
    }
    return p, [str(path)]


async def test_real_browser_submission_and_lost_receipt(live_portal):
    p, paths = payload(live_portal)
    first = await submit_report(p, "one", paths, str(live_portal / "receipt.png"))
    assert (
        first["receipt_id"].startswith("DB-")
        and Path(first["screenshot_path"]).stat().st_size > 1000
    )
    lookup = await lookup_receipt("one")
    assert lookup["receipt_id"] == first["receipt_id"]
    p["simulate_lost_receipt"] = True
    with pytest.raises(OutcomeUnknown):
        await submit_report(p, "two", paths)
    reconciled = await lookup_receipt("two")
    assert reconciled["normalized_status"] == "RECEIVED"
    # No second submit call is made to recover a lost receipt.
    assert (await lookup_receipt("two"))["receipt_id"] == reconciled["receipt_id"]


async def test_before_write_cancellation_and_attachment_hash(live_portal):
    p, paths = payload(live_portal)

    def cancelled():
        raise HandoffRequired("Resident cancelled before write")

    with pytest.raises(HandoffRequired):
        await submit_report(p, "cancelled", paths, before_write=cancelled)
    assert await lookup_receipt("cancelled") is None
    p["attachment_hashes"] = ["c" * 64]
    with pytest.raises(HandoffRequired):
        await submit_report(p, "changed", paths)


def test_cloud_browser_origin_restrictions(monkeypatch):
    monkeypatch.setenv("NF_MODE", "aws")
    monkeypatch.setenv("NF_PORTAL_URL", "http://localhost:8001")
    with pytest.raises(HandoffRequired):
        portal_url()
    monkeypatch.setenv("NF_PORTAL_URL", "https://untrusted.example")
    monkeypatch.setenv("NF_PORTAL_ALLOWED_ORIGINS", "https://approved.example")
    with pytest.raises(HandoffRequired):
        portal_url()


async def test_grant_network_failure_is_before_submission(tmp_path, monkeypatch):
    import services.worker.browser as browser

    monkeypatch.setenv("NF_MODE", "local")
    monkeypatch.setenv("NF_PORTAL_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("NF_PORTAL_ALLOWED_ORIGINS", "http://127.0.0.1:8001")
    p, paths = payload(tmp_path)

    async def fail_grant(*args, **kwargs):
        raise httpx.ConnectError("grant unavailable")

    monkeypatch.setattr(browser, "portal_request", fail_grant)
    with pytest.raises(browser.FailedBeforeSubmission):
        await browser.submit_report(p, "grant-failed", paths)


async def test_optional_screenshot_failure_keeps_confirmed_receipt(live_portal):
    p, paths = payload(live_portal)
    blocked = live_portal / "not-a-directory"
    blocked.write_text("existing regular file")
    result = await submit_report(
        p, "screenshot-failed", paths, str(blocked / "receipt.png")
    )
    assert result["receipt_id"].startswith("DB-")
    assert "screenshot_path" not in result
    assert result["steps"][-1] == "Receipt confirmed; optional screenshot unavailable"
    assert (await lookup_receipt("screenshot-failed"))["receipt_id"] == result[
        "receipt_id"
    ]


async def test_aws_total_attachment_limit_stops_before_grant(tmp_path, monkeypatch):
    import services.worker.browser as browser

    monkeypatch.setenv("NF_MODE", "aws")
    monkeypatch.setenv("NF_PORTAL_URL", "https://portal.example.test")
    monkeypatch.setenv("NF_PORTAL_ALLOWED_ORIGINS", "https://portal.example.test")
    p, _ = payload(tmp_path)
    paths = []
    for index in range(2):
        path = tmp_path / f"large-{index}.png"
        path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        paths.append(str(path))
    p["attachment_hashes"] = [
        hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths
    ]

    async def no_grant(*args, **kwargs):
        pytest.fail("Oversized report must stop before any portal call")

    monkeypatch.setattr(browser, "portal_request", no_grant)
    with pytest.raises(HandoffRequired, match="4 MB"):
        await submit_report(p, "oversized", paths)
