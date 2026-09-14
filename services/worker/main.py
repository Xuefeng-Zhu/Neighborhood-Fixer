"""Restartable worker. All due times, leases, and outcomes live in SQLite.

One-shot: uv run python -m services.worker.main --once
Service: uv run python -m services.worker.main
"""

import argparse
import asyncio
import signal
from services.api.config import Settings
from services.api.domain import Domain


async def run(once=False, interval=0.5):
    settings = Settings()
    if settings.mode != "local":
        raise RuntimeError(
            "The local worker is disabled in AWS mode; Step Functions dispatches shared domain commands."
        )
    domain = Domain(settings)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    while not stopping.is_set():
        worked = False
        for workspace_id in domain.store.workspaces():
            if await domain.run_job(workspace_id):
                worked = True
        if once:
            break
        if not worked:
            try:
                await asyncio.wait_for(stopping.wait(), timeout=interval)
            except TimeoutError:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))
