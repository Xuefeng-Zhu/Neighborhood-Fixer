"""AgentCore Runtime HTTP entrypoint; deploy via IAM-authenticated Runtime API."""

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from . import engine

from .observability import configure

tracer = configure()
app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload, context):
    action = payload.get("action")
    if action not in ("analyze", "route", "prepare", "review_case"):
        raise ValueError("Unsupported reasoning phase")
    principal = payload.get("principal", {})
    # Only trusted backend IAM can invoke this runtime; it supplies the principal
    # after Cognito authorization. Runtime permissions are never frontend grants.
    args = payload.get("arguments", {})
    with tracer.start_as_current_span("case_reasoning") as span:
        span.set_attribute("nf.operation", action)
        try:
            result = await _execute(action, args, principal)
            span.set_attribute("nf.outcome", "completed")
            span.set_attribute("nf.tool_count", len(result.get("agent_activity", [])))
            return result
        except Exception:
            span.set_attribute("nf.outcome", "failed")
            raise


async def _execute(action, args, principal):
    import asyncio

    if action in ("analyze", "review_case"):
        return await asyncio.to_thread(
            getattr(engine, action),
            args["observation"],
            args.get("evidence", []),
            args.get("candidates", []),
            principal,
        )
    if action == "route":
        return await asyncio.to_thread(engine.route, args["observation"], principal)
    return await asyncio.to_thread(
        engine.prepare, args["incident"], args["routing"], principal
    )


if __name__ == "__main__":
    app.run()
