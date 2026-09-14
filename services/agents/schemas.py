from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["damaged_sidewalk", "pothole", "walkway_obstruction"]


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Analysis(StrictOutput):
    observed_facts: list[str] = Field(
        max_length=20,
        description="Only directly visible appearance from photos actually read. Do not certify proper installation, code compliance, safety, exact dimensions, or ownership. A visible tactile surface is not proof it is properly installed.",
    )
    resident_claims: list[str] = Field(
        max_length=20,
        description="Attribute the resident's description and public-location statement to the resident; these are not independently verified measurements or authoritative ownership records.",
    )
    unknowns: list[str] = Field(
        max_length=20,
        description="Retain evidence limits such as exact dimensions, safety, authoritative ownership, and maintenance responsibility. These expected uncertainties do not by themselves block preparing a report and must not be copied into missing_information.",
    )
    candidate_category: Category
    missing_information: list[str] = Field(
        max_length=10,
        description="Only resident-answerable questions about facts required to proceed: absent or unclear issue description/category, unconfirmed location, or asset_public still unknown. Do not demand exact measurements, safety or installation certification, or authoritative ownership/maintenance proof. When the resident has confirmed a public location and supplied the issue, category, and confirmed location, those expected uncertainties belong in unknowns, not blocking questions. Keep genuinely missing required facts here.",
    )
    duplicate_candidates: list[str] = Field(default_factory=list, max_length=20)
    decision_summary: str = Field(max_length=1200)


class Routing(StrictOutput):
    supported: bool
    recipient: str | None
    supported_category: Category
    required_fields: list[str]
    sources: list[str]
    registry_review_date: str
    unresolved_questions: list[str]
    decision_summary: str = Field(max_length=1200)


class PreparedReport(StrictOutput):
    description: str = Field(min_length=10, max_length=4000)
    recipient: str | None
    category: Category
    next_action: Literal[
        "request_approval",
        "clarify",
        "assisted_handoff",
        "request_verification",
        "wait",
    ]
    missing_information: list[str]
    decision_summary: str = Field(max_length=1200)
