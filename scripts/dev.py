#!/usr/bin/env python3
"""One-command local supervisor. Persists state and secrets; no AWS credentials used."""

from pathlib import Path
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
if os.getenv("NF_MODE", "local") != "local":
    raise SystemExit(
        "This launcher runs local fixtures only. Use the documented AWS deployment workflow for AWS mode."
    )
for command in ["uv", "npm"]:
    if not shutil.which(command):
        raise SystemExit(f"Install {command} first; see README.md.")
for port in [8000, 8001]:
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(
                f"Port {port} is already in use. Stop that service before starting Neighborhood Fixer."
            )

web_port = int(os.getenv("NF_WEB_PORT", "5173"))
while web_port < 5184:
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", web_port)) != 0:
            break
    web_port += 1
if web_port >= 5184:
    raise SystemExit("No available local web port from 5173 to 5183.")
env = os.environ.copy()
env.update(
    UV_CACHE_DIR=str(ROOT / ".local/uv-cache"),
    PLAYWRIGHT_BROWSERS_PATH=str(ROOT / ".local/browsers"),
    NF_MODE="local",
    NF_ENVIRONMENT="development",
    NF_DATA_DIR=str(Path(os.getenv("NF_DATA_DIR", ROOT / ".local/data")).resolve()),
    NF_PORTAL_URL="http://127.0.0.1:8001",
    NF_PORTAL_ALLOWED_ORIGINS="http://127.0.0.1:8001",
)
env["NF_ALLOWED_ORIGINS"] = (
    f"http://localhost:{web_port},http://127.0.0.1:{web_port},http://localhost:8000,http://127.0.0.1:8000"
)
data = Path(env["NF_DATA_DIR"])
data.mkdir(parents=True, exist_ok=True)
for key, filename in [
    ("NF_SESSION_SECRET", ".session-secret"),
    ("NF_PORTAL_SECRET", ".portal-secret"),
]:
    path = data / filename
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(48))
    env[key] = path.read_text().strip()


def run(args):
    subprocess.run(args, env=env, check=True)


print("Preparing pinned dependencies…", flush=True)
run(["uv", "sync", "--locked", "--all-extras"])
if not (ROOT / "node_modules/vite").exists():
    run(["npm", "ci"])
python = str(ROOT / ".venv/bin/python")
run([python, "-m", "playwright", "install", "chromium"])
children = []


def stop(*_):
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    for args in [
        [
            python,
            "-m",
            "uvicorn",
            "services.portal.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
            "--no-access-log",
        ],
        [
            python,
            "-m",
            "uvicorn",
            "services.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--no-access-log",
        ],
        [python, "-m", "services.worker.main"],
        [
            "npm",
            "run",
            "dev",
            "--workspace",
            "apps/web",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(web_port),
        ],
    ]:
        children.append(subprocess.Popen(args, env=env, start_new_session=True))
    print(
        f"\nNeighborhood Fixer: http://localhost:{web_port}\nFictional portal: http://127.0.0.1:8001\nLocal demo — simulated AI and fictional agency. Ctrl+C stops all four processes.\n",
        flush=True,
    )
    while all(p.poll() is None for p in children):
        time.sleep(0.5)
    failed = [p.returncode for p in children if p.poll() is not None]
    if failed:
        raise SystemExit(
            f"A service exited ({failed}); all services are being stopped. State remains in .local/data."
        )
finally:
    stop()
