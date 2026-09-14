from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Category = Literal["damaged_sidewalk", "pothole", "walkway_obstruction"]


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Analysis(StrictOutput):
    observed_facts: list[str] = Field(max_length=20)
    resident_claims: list[str] = Field(max_length=20)
    unknowns: list[str] = Field(max_length=20)
    candidate_category: Category
    missing_information: list[str] = Field(max_length=10)
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
