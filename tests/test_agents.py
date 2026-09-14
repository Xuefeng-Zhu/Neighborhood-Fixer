"""Deterministic real-Strands harness tests. These do not call Bedrock."""

import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from services.agents import engine, runtime_client
from services.agents.schemas import Analysis, Routing

OBS = {
    "id": "obs",
    "workspace_id": "workspace",
    "owner_id": "alex",
    "description": "Resident describes damaged curb paving.",
    "category": "damaged_sidewalk",
    "latitude": 47.61,
    "longitude": -122.335,
    "location_label": "Demo crossing",
    "location_confirmed": True,
    "asset_public": "yes",
}
PRINCIPAL = {"id": "alex", "workspace_id": "workspace"}


def test_missing_configuration_has_no_fixture_fallback(monkeypatch):
    monkeypatch.delenv("NF_BEDROCK_MODEL_ID", raising=False)
    with pytest.raises(engine.AgentConfigurationError):
        engine.analyze(OBS, [], [], PRINCIPAL)


def test_cross_workspace_rejected_before_model(monkeypatch):
    monkeypatch.setattr(
        engine, "_model", lambda: pytest.fail("Unauthorized model call")
    )
    with pytest.raises(PermissionError):
        engine.analyze(OBS, [], [], {"id": "sam", "workspace_id": "another"})


def test_evidence_owner_is_checked_before_tool_registration():
    with pytest.raises(PermissionError):
        engine._analyst(
            OBS,
            [{"id": "secret", "workspace_id": "workspace", "owner_id": "sam"}],
            [],
            PRINCIPAL,
            engine.Budget(),
        )


def test_analyst_contract_separates_visual_facts_unknowns_and_required_questions(
    monkeypatch,
):
    captured = {}

    def capture(name, schema, tools, prompt, budget):
        captured.update(schema=schema, prompt=prompt)
        return object()

    monkeypatch.setattr(engine, "_agent", capture)
    engine._analyst(OBS, [], [], PRINCIPAL, engine.Budget())
    properties = captured["schema"].model_json_schema()["properties"]
    assert "properly installed" in properties["observed_facts"]["description"]
    assert "do not by themselves block" in properties["unknowns"]["description"]
    assert (
        "asset_public still unknown" in properties["missing_information"]["description"]
    )
    assert "location_confirmed is true" in captured["prompt"]
    assert "asset_public is yes" in captured["prompt"]
    assert "force an empty list" in captured["prompt"]


def test_analyst_retains_unknowns_and_real_missing_resident_facts(monkeypatch):
    monkeypatch.setattr(engine, "_agent", lambda *args: object())
    expected = {
        "observed_facts": [],
        "resident_claims": [OBS["description"]],
        "unknowns": ["Exact dimensions and safety remain unverified."],
        "candidate_category": OBS["category"],
        "missing_information": [
            "Please confirm the location.",
            "Is this on a public walkway?",
        ],
        "duplicate_candidates": [],
        "decision_summary": "Required resident confirmations are still missing.",
    }
    monkeypatch.setattr(engine, "_run", lambda *args: dict(expected))
    result = engine.analyze(
        {**OBS, "location_confirmed": False, "asset_public": "unknown"},
        [],
        [],
        PRINCIPAL,
    )
    assert result["unknowns"] == expected["unknowns"]
    assert result["missing_information"] == expected["missing_information"]


def test_schema_rejects_invented_category_and_extra_action():
    with pytest.raises(ValidationError):
        Analysis(
            observed_facts=[],
            resident_claims=[],
            unknowns=[],
            candidate_category="crime",
            missing_information=[],
            decision_summary="send police",
        )
    with pytest.raises(ValidationError):
        Routing(
            supported=True,
            recipient="x",
            supported_category="pothole",
            required_fields=[],
            sources=[],
            registry_review_date="2026-09-13",
            unresolved_questions=[],
            decision_summary="x",
            submit_now=True,
        )


def test_budget_stops_turns_and_cancels_tools():
    budget = engine.Budget()
    budget.max_turns = 1
    budget.max_tools = 0
    budget.before_model(None)
    with pytest.raises(engine.AgentBudgetExceeded):
        budget.before_model(None)
    event = SimpleNamespace(cancel_tool=False)
    budget.before_tool(event)
    assert event.cancel_tool


def test_runtime_sessions_are_unique_and_errors_do_not_fallback(monkeypatch):
    pytest.importorskip("boto3")
    import boto3

    monkeypatch.setenv(
        "NF_AGENTCORE_RUNTIME_ARN",
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
    )
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs)
        return {"response": io.BytesIO(b'{"description":"prepared"}')}

    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kw: SimpleNamespace(invoke_agent_runtime=invoke),
    )
    runtime_client.prepare(
        OBS, {}, {"user": {"id": "alex"}, "workspace_id": "workspace"}
    )
    runtime_client.prepare(
        OBS, {}, {"user": {"id": "alex"}, "workspace_id": "workspace"}
    )
    assert calls[0]["runtimeSessionId"] != calls[1]["runtimeSessionId"]
    assert len(calls[0]["runtimeSessionId"]) >= 33
    assert json.loads(calls[0]["payload"])["principal"]["id"] == "alex"


def test_real_strands_graph_tools_and_structured_output(monkeypatch):
    pytest.importorskip("strands")
    from strands.models.model import Model

    class ScriptedModel(Model):
        def __init__(self):
            self.calls = 0

        def update_config(self, **kwargs):
            pass

        def get_config(self):
            return {"model_id": "deterministic-harness"}

        async def structured_output(self, *args, **kwargs):
            raise AssertionError("Deprecated structured_output must not be used")
            yield

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name = "query_nearby_incidents"
                payload = {}
            else:
                names = [t["name"] for t in tool_specs]
                name = next(
                    n
                    for n in names
                    if n
                    not in [
                        "read_authorized_evidence",
                        "query_nearby_incidents",
                        "compare_candidate_observations",
                    ]
                )
                payload = {
                    "observed_facts": [],
                    "resident_claims": [OBS["description"]],
                    "unknowns": ["No visual evidence supplied."],
                    "candidate_category": "damaged_sidewalk",
                    "missing_information": [],
                    "duplicate_candidates": [],
                    "decision_summary": "Resident claim retained; no visual observations invented.",
                }
            yield {"messageStart": {"role": "assistant"}}
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {"toolUseId": f"tool-{self.calls}", "name": name}
                    }
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps(payload)}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            yield {
                "metadata": {
                    "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                    "metrics": {"latencyMs": 1},
                }
            }

    model = ScriptedModel()
    monkeypatch.setattr(engine, "_model", lambda: model)
    result = engine.analyze(OBS, [], [], PRINCIPAL)
    assert result["candidate_category"] == "damaged_sidewalk"
    assert any(
        a["tool"] == "query_nearby_incidents" and a["result"] == "success"
        for a in result["agent_activity"]
    )
    assert result["observed_facts"] == []
    assert model.calls == 2
    # These are deterministic harness usage values, not measured Bedrock usage.
    assert result["usage"]["inputTokens"] == 2


def test_dynamodb_transaction_uses_consistent_guard_and_injects_workspace():
    from services.agents.aws_storage import DynamoStore

    client = Mock()
    client.get_item.return_value = {}
    with DynamoStore("records", client=client).atomic("workspace") as tx:
        assert tx.get("incident", "case") is None
        tx.put("incident", "case", {"description": "one reservation"})
    assert all(c.kwargs["ConsistentRead"] for c in client.get_item.call_args_list)
    writes = client.transact_write_items.call_args.kwargs["TransactItems"]
    assert (
        writes[0]["Update"]["ConditionExpression"] == "attribute_not_exists(revision)"
    )
    record = json.loads(writes[1]["Put"]["Item"]["data"]["S"])
    assert record["workspace_id"] == "workspace" and record["id"] == "case"


def test_callback_registration_reconciles_prior_approval(tmp_path, monkeypatch):
    from services.agents import aws_workflow
    from services.api import domain as domain_module
    from services.api.store import SQLiteStore

    store = SQLiteStore(tmp_path / "callback.sqlite")
    domain = SimpleNamespace(
        store=store,
        check_admission=lambda tx: None,
        settings=SimpleNamespace(data_generation="test-generation"),
    )
    with store.atomic("workspace") as tx:
        tx.put(
            "incident",
            "case",
            {
                "draft_id": "revision",
                "attempt_id": "attempt",
                "submission_status": "IN_FLIGHT",
            },
        )
        tx.put("draft", "revision", {"incident_id": "case", "payload_hash": "hash"})
        tx.put(
            "attempt", "attempt", {"draft_id": "revision", "approval_id": "approval"}
        )
        tx.put("approval", "approval", {"draft_id": "revision", "payload_hash": "hash"})
    client = Mock()
    monkeypatch.setattr(aws_workflow, "_client", lambda: client)
    monkeypatch.setattr(domain_module, "Domain", lambda: domain)
    aws_workflow.handler(
        {
            "phase": "register_approval",
            "workspace_id": "workspace",
            "incident_id": "case",
            "draft_id": "revision",
            "task_token": "private-token",
        },
        None,
    )
    assert client.send_task_success.call_count == 1
    with store.atomic("workspace") as tx:
        stored = tx.get("callback", "revision")
        assert stored["consumed"] and "task_token" not in stored
    assert aws_workflow.wake_approval("workspace", "case", "revision", domain) == {
        "woken": False
    }
    assert client.send_task_success.call_count == 1


def test_callback_cannot_wake_a_different_case(tmp_path, monkeypatch):
    from services.agents import aws_workflow
    from services.api.store import SQLiteStore

    store = SQLiteStore(tmp_path / "callback.sqlite")
    with store.atomic("workspace") as tx:
        tx.put(
            "callback",
            "revision",
            {
                "incident_id": "owner-case",
                "draft_id": "revision",
                "task_token": "private",
                "consumed": False,
            },
        )
    client = Mock()
    monkeypatch.setattr(aws_workflow, "_client", lambda: client)
    with pytest.raises(PermissionError):
        aws_workflow.wake_approval(
            "workspace",
            "another-case",
            "revision",
            SimpleNamespace(store=store, check_admission=lambda tx: None),
        )
    client.send_task_success.assert_not_called()


def test_photo_omission_cannot_be_presented_as_visual_analysis(monkeypatch):
    monkeypatch.setattr(engine, "_agent", lambda *args: object())
    monkeypatch.setattr(
        engine,
        "_run",
        lambda *args: {
            "observed_facts": ["Invented visual fact"],
            "duplicate_candidates": [],
        },
    )
    evidence = {
        "id": "photo",
        "workspace_id": "workspace",
        "owner_id": "alex",
        "sha256": "expected",
    }
    with pytest.raises(ValueError, match="required photo reads"):
        engine.analyze(OBS, [evidence], [], PRINCIPAL)


def test_changed_evidence_hash_is_rejected_by_real_registered_tool(monkeypatch):
    import base64

    captured = {}

    def capture(name, schema, tools, prompt, budget):
        captured["tools"] = tools
        return object()

    monkeypatch.setattr(engine, "_agent", capture)
    evidence = {
        "id": "photo",
        "workspace_id": "workspace",
        "owner_id": "alex",
        "sha256": "wrong",
        "bytes_base64": base64.b64encode(b"image bytes").decode(),
    }
    engine._analyst(OBS, [evidence], [], PRINCIPAL, engine.Budget())
    with pytest.raises(ValueError, match="hash mismatch"):
        captured["tools"][0]("photo")


def test_uncompared_duplicate_is_rejected(monkeypatch):
    monkeypatch.setattr(engine, "_agent", lambda *args: object())
    monkeypatch.setattr(
        engine, "_run", lambda *args: {"duplicate_candidates": ["nearby"]}
    )
    with pytest.raises(ValueError, match="without comparing"):
        engine.analyze(
            OBS, [], [{"id": "nearby", "category": "damaged_sidewalk"}], PRINCIPAL
        )


def test_unsupported_model_routing_cannot_override_verified_coverage(monkeypatch):
    monkeypatch.setattr(engine, "_agent", lambda *args: object())
    monkeypatch.setattr(
        engine,
        "_run",
        lambda *args: {
            "supported": True,
            "recipient": "Demo Borough Public Works",
            "sources": ["/api/registry/demo-borough"],
        },
    )
    with pytest.raises(ValueError, match="unsupported or unconfirmed"):
        engine.route({**OBS, "longitude": 0}, PRINCIPAL)


@pytest.mark.parametrize(
    "invalid_first",
    [
        None,
        "source",
        "recipient",
        "premature_router",
        "premature_coordinator",
        "premature_both",
    ],
)
def test_real_two_specialist_graph_rechecks_route_then_prepares(
    monkeypatch, invalid_first
):
    from strands.models.model import Model

    models = []

    class SpecialistModel(Model):
        def __init__(self):
            self.calls = 0
            models.append(self)

        def get_config(self):
            return {"model_id": "deterministic-graph-harness"}

        def update_config(self, **kw):
            pass

        async def structured_output(self, *args, **kwargs):
            raise AssertionError("No deprecated extraction")
            yield

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            names = [t["name"] for t in tool_specs]
            is_router = "lookup_jurisdiction" in names
            required = (
                [
                    "lookup_jurisdiction",
                    "lookup_asset_ownership",
                    "read_agency_registry",
                ]
                if is_router
                else ["prepare_submission"]
            )
            premature = invalid_first == "premature_both" or invalid_first == (
                "premature_router" if is_router else "premature_coordinator"
            )
            call_index = self.calls - int(premature)
            if premature and self.calls == 1:
                feedback = [
                    content["toolResult"]
                    for message in messages
                    for content in message["content"]
                    if "toolResult" in content
                ]
                assert feedback[-1]["status"] == "error"
                explanation = feedback[-1]["content"][0]["text"]
                assert "Final output is not accepted yet" in explanation
                assert all(tool in explanation for tool in required)
            if 0 <= call_index < len(required):
                name = required[call_index]
                payload = {}
            else:
                ordinary = required + (
                    ["retrieve_official_guidance"]
                    if is_router
                    else [
                        "request_approval",
                        "retrieve_ticket_status",
                        "prepare_followup",
                        "request_verification",
                    ]
                )
                name = next(n for n in names if n not in ordinary)
                payload = (
                    {
                        "supported": True,
                        "recipient": "Demo Borough Public Works",
                        "supported_category": "damaged_sidewalk",
                        "required_fields": ["description"],
                        "sources": ["/api/registry/demo-borough"],
                        "registry_review_date": "2026-09-13",
                        "unresolved_questions": [],
                        "decision_summary": "Fictional registry checked.",
                    }
                    if is_router
                    else {
                        "description": OBS["description"],
                        "recipient": "Demo Borough Public Works",
                        "category": "damaged_sidewalk",
                        "next_action": "request_approval",
                        "missing_information": [],
                        "decision_summary": "Draft wording proposed; nothing submitted.",
                    }
                )
                if (
                    is_router
                    and invalid_first in ("source", "recipient")
                    and self.calls == len(required)
                ):
                    if invalid_first == "source":
                        payload["sources"] = [
                            "https://invented.example/municipal-policy"
                        ]
                    else:
                        payload["recipient"] = "demo-borough"
            self.calls += 1
            yield {"messageStart": {"role": "assistant"}}
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": f"t-{self.calls}", "name": name}}
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps(payload)}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}

    monkeypatch.setattr(engine, "_model", SpecialistModel)
    result = engine.prepare(
        OBS, {"supported": True, "recipient": "Demo Borough Public Works"}, PRINCIPAL
    )
    names = [a["tool"] for a in result["agent_activity"]]
    assert names.index("read_agency_registry") < names.index("prepare_submission")
    assert result["next_action"] == "request_approval"
    assert result["recipient"] == "Demo Borough Public Works"
    assert models[0].calls == 4 + int(
        invalid_first in ("source", "recipient", "premature_router", "premature_both")
    )
    assert models[1].calls == 2 + int(
        invalid_first in ("premature_coordinator", "premature_both")
    )
    assert sum(model.calls for model in models) <= 8


@pytest.mark.parametrize(
    "other_role,tool_status", [(True, "success"), (False, "error")]
)
def test_required_tool_feedback_uses_only_this_specialists_successes(
    other_role, tool_status
):
    budget = engine.Budget()
    budget.after_tool(
        SimpleNamespace(
            agent=SimpleNamespace(
                name="Routing Specialist" if other_role else "Case Coordinator"
            ),
            tool_use={"name": "prepare_submission"},
            result={"status": tool_status},
        )
    )
    event = SimpleNamespace(
        selected_tool=SimpleNamespace(tool_type="structured_output"),
        agent=SimpleNamespace(name="Case Coordinator"),
        cancel_tool=False,
    )
    budget.before_tool(event)
    assert "prepare_submission" in event.cancel_tool
    assert (
        budget.missing_tool_detail("Case Coordinator")
        == "; missing required tools: prepare_submission"
    )


def test_router_schema_only_accepts_exact_registry_source_references(monkeypatch):
    captured = {}

    def capture(name, schema, tools, prompt, budget):
        captured.update(schema=schema, prompt=prompt)
        return object()

    monkeypatch.setattr(engine, "_agent", capture)
    engine._router(OBS, PRINCIPAL, engine.Budget())
    output = {
        "supported": True,
        "recipient": "Demo Borough Public Works",
        "supported_category": OBS["category"],
        "required_fields": ["description"],
        "sources": ["/api/registry/demo-borough"],
        "registry_review_date": "2026-09-13",
        "unresolved_questions": [],
        "decision_summary": "Registry tools reviewed.",
    }
    assert captured["schema"].model_validate(output).sources == output["sources"]
    for invented in (
        "agency-registry.json",
        "Fictional registry configuration v1",
        "https://invented.example/api/registry/demo-borough",
    ):
        with pytest.raises(ValidationError):
            captured["schema"].model_validate({**output, "sources": [invented]})
    assert "including relative paths" in captured["prompt"]
    for change in (
        {"recipient": "demo-borough"},
        {"recipient": "Demo Borough"},
        {"supported_category": "pothole"},
        {"required_fields": ["social_security_number"]},
        {"registry_review_date": "1900-01-01"},
    ):
        with pytest.raises(ValidationError):
            captured["schema"].model_validate({**output, **change})


def test_post_model_routing_guards_reject_untrusted_sources_and_category_changes():
    output = {
        "supported": True,
        "recipient": "Demo Borough Public Works",
        "supported_category": OBS["category"],
        "sources": ["/api/registry/demo-borough"],
    }
    with pytest.raises(ValueError, match="outside the verified registry"):
        engine._validate_routing_output(
            OBS, {**output, "sources": ["https://invented.example"]}, engine._registry()
        )
    with pytest.raises(ValueError, match="confirmed routing category"):
        engine._validate_routing_output(
            OBS, {**output, "supported_category": "pothole"}, engine._registry()
        )
    with pytest.raises(ValueError, match="sources do not support"):
        engine._validate_routing_output(
            OBS, {**output, "sources": []}, engine._registry()
        )


def test_coordinator_schema_preserves_exact_route_recipient_and_category(monkeypatch):
    captured = {}

    def capture(name, schema, tools, prompt, budget):
        captured["schema"] = schema
        return object()

    monkeypatch.setattr(engine, "_agent", capture)
    engine._coordinator(
        OBS, {"recipient": "Demo Borough Public Works"}, PRINCIPAL, engine.Budget()
    )
    output = {
        "description": OBS["description"],
        "recipient": "Demo Borough Public Works",
        "category": OBS["category"],
        "next_action": "request_approval",
        "missing_information": [],
        "decision_summary": "Proposed only; requires exact resident approval.",
    }
    assert captured["schema"].model_validate(output).recipient == output["recipient"]
    for change in ({"recipient": "demo-borough"}, {"category": "pothole"}):
        with pytest.raises(ValidationError):
            captured["schema"].model_validate({**output, **change})
    with pytest.raises(ValueError, match="outside the verified registry"):
        engine._coordinator(
            OBS, {"recipient": "Unreviewed agency"}, PRINCIPAL, engine.Budget()
        )
