"""Deterministic fixtures. Never represented as model execution."""

from .models import Analysis


def analyze(observation, evidence, candidates, principal):
    missing = []
    if not observation["location_confirmed"]:
        missing.append("Confirm the location before preparing a report.")
    if observation["asset_public"] == "unknown":
        missing.append("Is this on a public street or public walkway?")
    return Analysis(
        observed_facts=[
            "An image was supplied; the local fixture does not perform visual analysis."
        ]
        if evidence
        else ["No photograph supplied."],
        resident_claims=[observation["description"]],
        unknowns=[
            "Exact dimensions, safety, asset ownership, and repair needs are not established by this report."
        ],
        candidate_category=observation["category"],
        missing_information=missing,
        provenance="Local deterministic fixture — simulated AI; resident statements are not independently verified.",
    ).model_dump(mode="json")


def route(observation, principal):
    supported = (
        47.60 <= observation["latitude"] <= 47.63
        and -122.35 <= observation["longitude"] <= -122.32
        and observation["asset_public"] == "yes"
    )
    return {
        "recipient": "Demo Borough Public Works" if supported else None,
        "supported": supported,
        "status": "SUPPORTED" if supported else "HANDOFF_REQUIRED",
        "category": observation["category"],
        "required_fields": ["description", "confirmed_location", "category"],
        "sources": [
            {
                "title": "Fictional Demo Borough registry v1",
                "url": "/api/registry/demo-borough",
                "kind": "fictional_configuration",
            }
        ],
        "registry_review_date": "2026-09-13",
        "registry_version": 1,
        "unresolved_questions": []
        if supported
        else [
            "Jurisdiction or public asset responsibility is not established. Confirm the correct recipient before sending this packet."
        ],
        "provenance": "Local deterministic routing fixture using trusted fictional coverage configuration.",
    }


def prepare(incident, routing, principal):
    return {
        "description": incident["description"],
        "category": incident["category"],
        "recipient": routing["recipient"],
        "provenance": "Local deterministic report fixture.",
    }


def simulate_voice(envelope, principal):
    return {
        "turns": [
            {
                "speaker": "reporting_agent",
                "intent": "report_issue",
                "fact_ids": ["category", "description"],
                "variant_id": "report_standard",
            },
            {
                "speaker": "fictional_intake_agent",
                "intent": "request_location",
                "fact_ids": [],
                "variant_id": "ask_location_standard",
            },
            {
                "speaker": "reporting_agent",
                "intent": "answer_location",
                "fact_ids": ["location", "jurisdiction"],
                "variant_id": "location_standard",
            },
            {
                "speaker": "fictional_intake_agent",
                "intent": "acknowledge",
                "fact_ids": [],
                "variant_id": "acknowledge_standard",
            },
            {
                "speaker": "reporting_agent",
                "intent": "ask_next_step",
                "fact_ids": [],
                "variant_id": "next_step_standard",
            },
            {
                "speaker": "fictional_intake_agent",
                "intent": "close",
                "fact_ids": [],
                "variant_id": "close_standard",
            },
        ],
        "provenance": "Local deterministic voice fixture — no model or phone call.",
        "agent_activity": [],
    }
