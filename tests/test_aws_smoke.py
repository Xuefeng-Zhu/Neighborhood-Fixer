"""Explicit opt-in, live AWS smoke tests. Skipped is not passed.

Run only after authorizing this account/environment and deployed portal:
NF_RUN_AWS_SMOKE=1 uv run --all-extras pytest tests/test_aws_smoke.py -q
These tests make billable Bedrock/AgentCore calls but never submit a report.
"""

import asyncio
import os
import uuid
from urllib.parse import urlparse
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NF_RUN_AWS_SMOKE") != "1",
    reason="Live AWS smoke tests require explicit NF_RUN_AWS_SMOKE=1; no cloud verification claimed",
)


def required(name):
    value = os.environ.get(name)
    assert value, f"Set {name} for the authorized AWS demo deployment"
    return value


def test_deployed_storage_is_private_and_table_exists():
    import boto3

    bucket = required("NF_EVIDENCE_BUCKET")
    table = required("NF_TABLE_NAME")
    block = boto3.client("s3").get_public_access_block(Bucket=bucket)[
        "PublicAccessBlockConfiguration"
    ]
    assert all(block.values())
    assert (
        boto3.client("dynamodb").describe_table(TableName=table)["Table"]["TableStatus"]
        == "ACTIVE"
    )


def test_real_agentcore_runtime_routing_has_no_fixture_provenance():
    from services.agents.runtime_client import route

    required("NF_AGENTCORE_RUNTIME_ARN")
    workspace = "smoke-" + str(uuid.uuid4())
    result = route(
        {
            "id": "observation",
            "workspace_id": workspace,
            "owner_id": "smoke",
            "description": "Resident reports an obstruction on the public demo walkway.",
            "category": "walkway_obstruction",
            "latitude": 47.615,
            "longitude": -122.335,
            "location_label": "Fictional Demo Borough",
            "location_confirmed": True,
            "asset_public": "yes",
        },
        {"id": "smoke", "workspace_id": workspace},
    )
    assert result["recipient"] == "Demo Borough Public Works"
    assert result["supported"] is True
    assert result["agent_activity"]
    assert "not a fixture" in result["provenance"]


def test_agentcore_browser_reaches_configured_https_fictional_portal():
    from services.agents.browser_provider import open_agentcore_browser
    from playwright.async_api import async_playwright

    url = required("NF_PORTAL_URL")
    required("NF_AGENTCORE_BROWSER_ID")
    assert urlparse(url).scheme == "https"
    assert urlparse(url).hostname not in ("localhost", "127.0.0.1", "::1")

    async def check():
        async with async_playwright() as p:
            async with open_agentcore_browser(p) as browser:
                context = (
                    browser.contexts[0]
                    if browser.contexts
                    else await browser.new_context()
                )
                page = await context.new_page()
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=30000
                )
                assert response and response.status < 500
                assert "fictional" in (await page.inner_text("body")).lower()

    asyncio.run(check())


def test_deployed_api_reports_aws_and_rejects_development_controls():
    import httpx

    base = required("NF_AWS_API_URL").rstrip("/")
    health = httpx.get(base + "/api/health", timeout=20)
    health.raise_for_status()
    assert health.json()["mode"] == "aws"
    response = httpx.post(
        base + "/api/demo/session", json={"resident": "alex"}, timeout=20
    )
    assert response.status_code in (401, 403, 404)
