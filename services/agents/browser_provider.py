"""Bounded AgentCore Browser session. Shares the local Playwright form executor."""

import asyncio
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse


@asynccontextmanager
async def open_agentcore_browser(playwright):
    from bedrock_agentcore.tools.browser_client import BrowserClient

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    identifier = os.environ.get("NF_AGENTCORE_BROWSER_ID")
    destination = urlparse(os.environ.get("NF_PORTAL_URL", ""))
    if (
        not region
        or not identifier
        or destination.scheme != "https"
        or destination.hostname in (None, "localhost", "127.0.0.1", "::1")
    ):
        raise RuntimeError(
            "AWS browser requires region, browser ID, and a reachable allowlisted HTTPS portal"
        )
    seconds = min(
        max(int(os.environ.get("NF_BROWSER_TIMEOUT_SECONDS", "180")), 30), 300
    )
    client = BrowserClient(region=region)
    browser = None
    started = False
    try:
        await asyncio.to_thread(
            client.start, identifier=identifier, session_timeout_seconds=seconds
        )
        started = True
        # Signed connection material remains server-side and must never be logged.
        url, headers = await asyncio.to_thread(client.generate_ws_headers)
        browser = await playwright.chromium.connect_over_cdp(
            url, headers=headers, timeout=30000
        )
        async with asyncio.timeout(seconds):
            yield browser
    finally:
        try:
            if browser is not None:
                await browser.close()
        finally:
            if started:
                await asyncio.to_thread(client.stop)
