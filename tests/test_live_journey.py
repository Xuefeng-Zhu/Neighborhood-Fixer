"""Full local vertical slice: actual API, persisted worker, Chromium and portal.

Never reaches a real agency or AWS. Creates a disposable isolated directory.
"""

import concurrent.futures
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_stack(tmp_path):
    api_port, portal_port = free_port(), free_port()
    env = os.environ.copy()
    env.update(
        NF_MODE="local",
        NF_ENVIRONMENT="development",
        NF_DATA_DIR=str(tmp_path),
        NF_PORTAL_SECRET="isolated-integration-secret",
        NF_PORTAL_URL=f"http://127.0.0.1:{portal_port}",
        NF_PORTAL_ALLOWED_ORIGINS=f"http://127.0.0.1:{portal_port}",
    )
    children = []
    logs = []

    def start(args):
        handle = (tmp_path / f"process-{len(children)}.log").open("wb")
        logs.append(handle)
        p = subprocess.Popen(
            [sys.executable, *args],
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
        )
        children.append(p)
        return p

    start(
        [
            "-m",
            "uvicorn",
            "services.portal.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(portal_port),
            "--no-access-log",
        ]
    )
    start(
        [
            "-m",
            "uvicorn",
            "services.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--no-access-log",
        ]
    )
    worker = start(["-m", "services.worker.main"])
    client = httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=15)
    for _ in range(150):
        try:
            if client.get("/api/health").status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    else:
        raise AssertionError("Local stack unavailable")
    yield client, worker, start
    client.close()
    for p in children:
        if p.poll() is None:
            p.terminate()
    for p in children:
        p.wait(timeout=10)
    for handle in logs:
        handle.close()


def req(c, method, path, **kwargs):
    r = c.request(method, path, **kwargs)
    assert r.status_code < 300, (r.status_code, r.text)
    return r.json()


def wait_operation(c, oid, seconds=40):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        op = req(c, "GET", f"/api/operations/{oid}")
        if op["status"] == "completed":
            return op["result"]
        assert op["status"] != "failed", op
        time.sleep(0.15)
    raise AssertionError("Persisted operation did not complete")


def photo(c, name="curb-before.png"):
    with (ROOT / "fixtures/images" / name).open("rb") as f:
        return req(c, "POST", "/api/uploads", files={"file": (name, f, "image/png")})[
            "id"
        ]


def report(
    c, eid, lat=47.615, desc="Broken curb ramp edge beside the yellow tactile surface."
):
    return req(
        c,
        "POST",
        "/api/observations",
        json={
            "description": desc,
            "category": "damaged_sidewalk",
            "latitude": lat,
            "longitude": -122.335,
            "location_label": "Maple & Alder · fictional Demo Borough",
            "location_confirmed": True,
            "asset_public": "yes",
            "evidence_ids": [eid],
            "share_public": True,
        },
    )


def analyze(c, o):
    return wait_operation(
        c, req(c, "POST", f"/api/observations/{o['id']}/analyze")["operation_id"]
    )


def decide(c, o, **decision):
    return wait_operation(
        c,
        req(c, "POST", f"/api/observations/{o['id']}/decision", json=decision)[
            "operation_id"
        ],
    )["incident_id"]


def approve(c, i):
    d = req(c, "GET", f"/api/incidents/{i}")["draft"]
    return req(
        c,
        "POST",
        f"/api/incidents/{i}/approve",
        json={
            "draft_id": d["id"],
            "payload_hash": d["payload_hash"],
            "publish_consent": True,
        },
    )["operation_id"]


def test_two_residents_one_browser_ticket_restart_and_verification(live_stack):
    c, worker, start = live_stack
    alex = req(c, "POST", "/api/demo/session", json={"resident": "alex"})
    eid = photo(c)
    o = report(c, eid)
    worker.terminate()
    worker.wait(timeout=5)
    oid = req(c, "POST", f"/api/observations/{o['id']}/analyze")["operation_id"]
    assert req(c, "GET", f"/api/operations/{oid}")["status"] == "pending"
    start(["-m", "services.worker.main"])
    analysis = wait_operation(c, oid)
    assert (
        analysis["analysis"]["unknowns"]
        and "fixture" in analysis["analysis"]["provenance"].lower()
    )
    iid = decide(c, o, different_issue=True)
    first = req(c, "GET", f"/api/incidents/{iid}")
    assert first["submission_status"] == "AWAITING_APPROVAL"
    first_draft = first["draft"]["payload_hash"]
    # Same signed local workspace, second resident. This switch is unavailable in AWS.
    req(
        c,
        "POST",
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": alex["workspace_id"]},
    )
    assert c.get(f"/api/evidence/{eid}").status_code == 403
    assert (
        c.post(
            f"/api/incidents/{iid}/approve",
            json={
                "draft_id": first["draft"]["id"],
                "payload_hash": first_draft,
                "publish_consent": True,
            },
        ).status_code
        == 403
    )
    second = report(
        c,
        photo(c),
        desc="The same broken curb ramp edge makes the crossing difficult with a stroller.",
    )
    candidates = analyze(c, second)["duplicate_candidates"]
    assert iid in [x["id"] for x in candidates]
    assert decide(c, second, incident_id=iid, different_issue=False) == iid
    req(
        c,
        "POST",
        "/api/demo/session",
        json={"resident": "alex", "workspace_id": alex["workspace_id"]},
    )
    current = req(c, "GET", f"/api/incidents/{iid}")
    assert (
        current["observation_count"] == 2
        and current["draft"]["payload_hash"] == first_draft
    )
    # Parallel clicks reserve exactly one operation. A singleton browser job follows.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        ops = list(pool.map(lambda _: approve(c, iid), range(4)))
    assert len(set(ops)) == 1
    wait_operation(c, ops[0])
    current = req(c, "GET", f"/api/incidents/{iid}")
    assert current["submission_status"] == "RECEIPT_CONFIRMED" and current["ticket"][
        "receipt_id"
    ].startswith("DB-")
    receipt_id = current["ticket"]["receipt_id"]
    req(
        c,
        "POST",
        f"/api/demo/tickets/{iid}/status",
        json={"status": "CLOSED", "closure_note": "duplicate"},
    )
    req(c, "POST", "/api/demo/clock", json={"advance_seconds": 120})
    for _ in range(100):
        current = req(c, "GET", f"/api/incidents/{iid}")
        if current["agency_status"] == "CLOSED":
            break
        time.sleep(0.1)
    assert current["agency_status"] == "CLOSED"
    assert current["resolution_status"] in ["UNVERIFIED", "VERIFICATION_REQUESTED"]
    req(
        c,
        "POST",
        f"/api/incidents/{iid}/verify",
        json={"choice": "looks_fixed", "evidence_id": photo(c, "curb-after.png")},
    )
    assert (
        req(c, "GET", f"/api/incidents/{iid}")["resolution_status"]
        == "RESIDENT_CONFIRMED_FIXED"
    )
    req(
        c,
        "POST",
        "/api/demo/session",
        json={"resident": "sam", "workspace_id": alex["workspace_id"]},
    )
    shared = req(c, "GET", f"/api/incidents/{iid}")
    assert shared["resolution_status"] == "RESIDENT_CONFIRMED_FIXED"
    assert (
        shared["ticket"]["receipt_id"] == receipt_id and "url" not in shared["ticket"]
    )
    assert req(c, "GET", "/api/notifications")["items"]


def test_real_lost_receipt_reconciles_without_resubmission(live_stack):
    c, _, _ = live_stack
    req(c, "POST", "/api/demo/session", json={"resident": "alex"})
    req(c, "POST", "/api/demo/scenario", json={"lost_receipt": True})
    o = report(c, photo(c))
    analyze(c, o)
    iid = decide(c, o, different_issue=True)
    wait_operation(c, approve(c, iid))
    inc = req(c, "GET", f"/api/incidents/{iid}")
    assert inc["submission_status"] == "OUTCOME_UNKNOWN"
    inc["events"]
    oid = req(c, "POST", f"/api/incidents/{iid}/reconcile")["operation_id"]
    wait_operation(c, oid)
    inc = req(c, "GET", f"/api/incidents/{iid}")
    assert inc["submission_status"] == "RECEIPT_CONFIRMED"
    assert sum(e["type"] == "APPROVED" for e in inc["events"]) == 1
    assert inc["ticket"]["receipt_id"]
