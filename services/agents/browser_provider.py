"""Bounded AgentCore Browser session. Shares the local Playwright form executor."""

import asyncio
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse


async def _connect_ready_session(playwright, client):
    """Retry only initial stream discovery, before any browser action can occur.

    AWS documents checking active session state before connecting Playwright:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool-troubleshooting.html
    A newly created, valid session returned a transient-looking automation 404 in
    live smoke testing. Bound discovery to the same session; never start another.
    """
    from botocore.exceptions import ClientError
    from playwright.async_api import Error as PlaywrightError

    browser_id, session_id = client.identifier, client.session_id
    state_reads = connection_attempts = stream_not_found = state_not_found = 0
    last_state = "not_read"
    try:
        async with asyncio.timeout(30):
            for attempt in range(5):
                try:
                    state_reads += 1
                    state = await asyncio.to_thread(client.get_session)
                except ClientError as exc:
                    if (
                        exc.response.get("Error", {}).get("Code")
                        != "ResourceNotFoundException"
                    ):
                        raise RuntimeError(
                            "Unable to read the AgentCore Browser session state."
                        ) from None
                    state = None
                    state_not_found += 1
                if state is not None:
                    stream = state.get("streams", {}).get("automationStream", {})
                    last_state = (
                        "ready_enabled"
                        if state.get("status") == "READY"
                        and stream.get("streamStatus") == "ENABLED"
                        else "not_ready"
                    )
                    if (
                        state.get("browserIdentifier") != browser_id
                        or state.get("sessionId") != session_id
                        or state.get("status") == "TERMINATED"
                        or stream.get("streamStatus") == "DISABLED"
                    ):
                        raise RuntimeError(
                            "AgentCore Browser session is unavailable for automation."
                        )
                    if (
                        state.get("status") == "READY"
                        and stream.get("streamStatus") == "ENABLED"
                    ):
                        url, headers = await asyncio.to_thread(
                            client.generate_ws_headers
                        )
                        if url != stream.get("streamEndpoint"):
                            raise RuntimeError(
                                "AgentCore Browser automation endpoint did not match its session."
                            )
                        try:
                            connection_attempts += 1
                            return await playwright.chromium.connect_over_cdp(
                                url, headers=headers, timeout=10000
                            )
                        except PlaywrightError as exc:
                            # This exact failure happened before a WebSocket was
                            # established. Never retry navigation or page writes.
                            if not (
                                "404" in str(exc)
                                and "Required resources not found for session"
                                in str(exc)
                            ):
                                raise RuntimeError(
                                    "Unable to connect to AgentCore Browser automation."
                                ) from None
                            stream_not_found += 1
                if attempt < 4:
                    await asyncio.sleep(2**attempt)
    except TimeoutError:
        pass
    raise RuntimeError(
        "AgentCore Browser automation was not ready within the bounded connection window. "
        f"State reads: {state_reads}; state not found: {state_not_found}; "
        f"connection attempts: {connection_attempts}; stream 404 responses: {stream_not_found}; "
        f"last state: {last_state}."
    )


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
        browser = await _connect_ready_session(playwright, client)
        async with asyncio.timeout(seconds):
            yield browser
    finally:
        try:
            if browser is not None:
                await browser.close()
        finally:
            if started:
                await asyncio.to_thread(client.stop)
