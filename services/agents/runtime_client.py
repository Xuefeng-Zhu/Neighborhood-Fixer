import json
import os
import uuid


def invoke(action, arguments, principal):
    import boto3
    from botocore.config import Config

    principal = {
        **principal,
        "id": principal.get("id") or principal.get("user", {}).get("id"),
    }
    arn = os.environ.get("NF_AGENTCORE_RUNTIME_ARN")
    if not arn:
        raise RuntimeError(
            "NF_AGENTCORE_RUNTIME_ARN is missing; no fixture fallback is permitted"
        )
    # A fresh session per request prevents cross-case model history retention.
    client = boto3.client(
        "bedrock-agentcore",
        region_name=os.environ.get("AWS_REGION"),
        config=Config(
            connect_timeout=5, read_timeout=90, retries={"total_max_attempts": 1}
        ),
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=str(uuid.uuid4()),
        payload=json.dumps(
            {"action": action, "arguments": arguments, "principal": principal}
        ).encode(),
        contentType="application/json",
        accept="application/json",
    )
    raw = response["response"].read()
    result = json.loads(raw)
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError("AgentCore phase failed; preserve case for retry or review")
    if action == "route":
        result["category"] = result.pop("supported_category")
        result["status"] = "SUPPORTED" if result["supported"] else "HANDOFF_REQUIRED"
        result["registry_version"] = 1
        result["sources"] = [
            {
                "title": "Fictional registry configuration v1",
                "url": source,
                "kind": "fictional_configuration",
            }
            for source in result["sources"]
        ]
    return result


def analyze(observation, evidence, candidates, principal):
    return invoke(
        "analyze",
        {"observation": observation, "evidence": evidence, "candidates": candidates},
        principal,
    )


def route(observation, principal):
    return invoke("route", {"observation": observation}, principal)


def prepare(incident, routing, principal):
    return invoke("prepare", {"incident": incident, "routing": routing}, principal)


def simulate_voice(envelope, principal):
    return invoke("simulate_voice", {"envelope": envelope}, principal)
