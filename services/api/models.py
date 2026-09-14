from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Category(StrEnum):
    damaged_sidewalk = "damaged_sidewalk"
    pothole = "pothole"
    walkway_obstruction = "walkway_obstruction"


class User(StrictModel):
    id: str
    name: str
    resident: str


class SessionRequest(StrictModel):
    resident: Literal["alex", "sam"] = "alex"
    workspace_id: str | None = None


class QuotaUsage(StrictModel):
    limit: int = Field(ge=1)
    used: int = Field(ge=0)
    reserved: int = Field(ge=0)
    remaining: int = Field(ge=0)


class QuotaSnapshot(StrictModel):
    date_utc: str
    reset_at: str
    reports: QuotaUsage
    uploads: QuotaUsage
    reasoning: QuotaUsage


class SessionResponse(StrictModel):
    user: User
    workspace_id: str
    mode: Literal["local", "aws"]
    generation: str | None = None
    quotas: QuotaSnapshot | None = None


class ObservationInput(StrictModel):
    description: str = Field(min_length=8, max_length=3000)
    category: Category
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    location_label: str = Field(min_length=3, max_length=240)
    location_confirmed: bool
    asset_public: Literal["yes", "no", "unknown"] = "unknown"
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)
    share_public: bool = False
    share_evidence: bool = False
    asset_id: str | None = Field(default=None, max_length=120)


class ObservationPatch(StrictModel):
    description: str | None = Field(default=None, min_length=8, max_length=3000)
    category: Category | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_label: str | None = Field(default=None, min_length=3, max_length=240)
    location_confirmed: bool | None = None
    asset_public: Literal["yes", "no", "unknown"] | None = None
    share_public: bool | None = None
    share_evidence: bool | None = None
    evidence_ids: list[str] | None = Field(default=None, max_length=4)


class DuplicateDecision(StrictModel):
    incident_id: str | None = None
    different_issue: bool = False


class Contact(StrictModel):
    name: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=50)


class DraftInput(StrictModel):
    description: str | None = Field(default=None, min_length=8, max_length=3000)
    contact: Contact = Field(default_factory=Contact)
    attachment_ids: list[str] | None = Field(default=None, max_length=4)


class ApprovalInput(StrictModel):
    draft_id: str
    payload_hash: str = Field(min_length=64, max_length=64)
    publish_consent: bool = False
    share_evidence: bool = False


class SubscriptionInput(StrictModel):
    following: bool


class VerificationInput(StrictModel):
    share_public: bool = False
    choice: Literal["looks_fixed", "still_present", "unable_to_verify"]
    evidence_id: str | None = None
    note: str = Field(default="", max_length=1000)


class ClockInput(StrictModel):
    advance_seconds: int = Field(ge=1, le=86400 * 30)


class TicketStatusInput(StrictModel):
    status: Literal["OPEN", "IN_PROGRESS", "CLOSED"]
    closure_note: str = Field(default="", max_length=1000)


class ScenarioInput(StrictModel):
    lost_receipt: bool


class Analysis(StrictModel):
    observed_facts: list[str]
    resident_claims: list[str]
    unknowns: list[str]
    candidate_category: Category
    missing_information: list[str]
    provenance: str
    agent_activity: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    decision_summary: str = ""
    duplicate_candidates: list[str] = Field(default_factory=list)


# Persisted entities retain explicit semantic types even though the store serializes JSON.
class Observation(ObservationInput):
    id: str
    workspace_id: str
    owner_id: str
    created_at: str
    updated_at: str
    incident_id: str | None = None
    analysis: dict[str, Any] | None = None
    version: int = 1


class Incident(BaseModel):
    is_sample: bool = False
    id: str
    title: str
    description: str
    category: Category
    latitude: float
    longitude: float
    location_label: str
    agency_status: str
    resolution_status: str
    submission_status: str
    observation_count: int
    owner_id: str
    following: bool = False
    shared_public: bool
    version: int
    created_at: str
    updated_at: str
    thumbnail_url: str | None = None
    next_action: str | None = None
    model_config = ConfigDict(extra="allow")


class AgencyRegistryEntry(BaseModel):
    id: str
    name: str
    version: int
    coverage: dict
    supported_categories: list[Category]
    required_fields: list[str]
    automation_authorized: bool
    destination: str
    sources: list[dict]
    reviewed_at: str


class Evidence(BaseModel):
    id: str
    workspace_id: str
    owner_id: str
    sha256: str
    content_type: str
    size: int
    created_at: str
    sanitized: bool = True
    public_approved: bool = False


class SubmissionDraft(BaseModel):
    id: str
    revision: int
    recipient: str
    category: Category
    description: str
    location_label: str
    latitude: float
    longitude: float
    contact: dict
    attachment_ids: list[str]
    attachment_hashes: list[str]
    payload_hash: str
    expires_at: str
    status: str


class Approval(BaseModel):
    id: str
    approver_id: str
    incident_id: str
    draft_id: str
    payload_hash: str
    recipient: str
    action: str = "submit_report"
    attachment_hashes: list[str]
    expires_at: str
    status: str


class SubmissionAttempt(BaseModel):
    id: str
    incident_id: str
    draft_id: str
    approval_id: str
    status: str
    payload: dict
    write_started: bool = False


class AgencyTicket(BaseModel):
    id: str
    incident_id: str
    receipt_id: str
    url: str
    normalized_status: str
    raw_status: str
    closure_note: str = ""


class CaseEvent(BaseModel):
    id: str
    incident_id: str
    operation_id: str
    type: str
    message: str
    created_at: str


class Subscription(BaseModel):
    id: str
    incident_id: str
    user_id: str
    following: bool


class WorkflowJob(BaseModel):
    id: str
    workspace_id: str
    kind: str
    status: str
    due_at: float
    attempts: int = 0
    lease_until: float = 0
    payload: dict


class Notification(BaseModel):
    id: str
    user_id: str
    incident_id: str
    message: str
    created_at: str
    read: bool = False


class OperationResponse(BaseModel):
    operation_id: str


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool
    correlation_id: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
