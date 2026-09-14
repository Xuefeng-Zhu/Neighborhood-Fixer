"""Initial AgentCore connection recovery never repeats sessions or page actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import Error as PlaywrightError

from services.agents import browser_provider


def fake_client():
    client = Mock(identifier="browser", session_id="session")
    client.get_session.return_value = {
        "browserIdentifier": "browser",
        "sessionId": "session",
        "status": "READY",
        "streams": {
            "automationStream": {
                "streamStatus": "ENABLED",
                "streamEndpoint": "wss://private-endpoint",
            }
        },
    }
    client.generate_ws_headers.return_value = (
        "wss://private-endpoint",
        {"Authorization": "private-signature"},
    )
    return client


@pytest.mark.asyncio
async def test_initial_404_reconnects_same_ready_session_before_yield(monkeypatch):
    from bedrock_agentcore.tools.browser_client import BrowserClient

    client = fake_client()
    monkeypatch.setattr(
        "bedrock_agentcore.tools.browser_client.BrowserClient", lambda **kw: client
    )
    assert BrowserClient is not None
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("NF_AGENTCORE_BROWSER_ID", "browser")
    monkeypatch.setenv("NF_PORTAL_URL", "https://fictional.lambda-url.us-west-2.on.aws")
    monkeypatch.setattr(browser_provider.asyncio, "sleep", AsyncMock())
    browser = SimpleNamespace(close=AsyncMock())
    connect = AsyncMock(
        side_effect=[
            PlaywrightError(
                "WebSocket404 Required resources not found for session private"
            ),
            browser,
        ]
    )
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    async with browser_provider.open_agentcore_browser(playwright) as connected:
        assert connected is browser
    assert client.start.call_count == client.stop.call_count == 1
    assert (
        connect.await_count
        == client.get_session.call_count
        == client.generate_ws_headers.call_count
        == 2
    )
    assert browser.close.await_count == 1


@pytest.mark.asyncio
async def test_persistent_initial_404_is_bounded_and_redacted(monkeypatch):
    monkeypatch.setattr(browser_provider.asyncio, "sleep", AsyncMock())
    client = fake_client()
    connect = AsyncMock(
        side_effect=PlaywrightError(
            "404 Required resources not found for session wss://private?signature=secret"
        )
    )
    with pytest.raises(RuntimeError, match="bounded connection") as error:
        await browser_provider._connect_ready_session(
            SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect)), client
        )
    assert connect.await_count == 5 and client.start.call_count == 0
    assert "secret" not in str(error.value) and "wss:" not in str(error.value)
    assert "connection attempts: 5; stream 404 responses: 5" in str(error.value)
    assert "last state: ready_enabled" in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("state_change", ["TERMINATED", "DISABLED", "MISMATCH"])
async def test_nonautomatable_session_never_connects(state_change):
    client = fake_client()
    state = client.get_session.return_value
    if state_change == "TERMINATED":
        state["status"] = "TERMINATED"
    elif state_change == "DISABLED":
        state["streams"]["automationStream"]["streamStatus"] = "DISABLED"
    else:
        state["sessionId"] = "another-session"
    connect = AsyncMock()
    with pytest.raises(RuntimeError, match="unavailable"):
        await browser_provider._connect_ready_session(
            SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect)), client
        )
    assert connect.await_count == 0


@pytest.mark.asyncio
async def test_authentication_connection_error_is_not_retried():
    client = fake_client()
    connect = AsyncMock(side_effect=PlaywrightError("403 forbidden private-signature"))
    with pytest.raises(RuntimeError, match="Unable to connect") as error:
        await browser_provider._connect_ready_session(
            SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect)), client
        )
    assert connect.await_count == 1 and "private-signature" not in str(error.value)
