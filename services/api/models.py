from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ContactResearchInput(StrictModel):
    candidate_id: str = Field(min_length=16, max_length=80)
    context_hash: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]
    refresh: bool = False


class ContactSelectionInput(StrictModel):
    research_id: str = Field(min_length=16, max_length=80)
    contact_id: str = Field(min_length=16, max_length=80)


class OutreachEmailDraftInput(StrictModel):
    pass


class OutreachApprovalInput(StrictModel):
    draft_id: str = Field(min_length=16, max_length=80)
    payload_hash: str = Field(min_length=64, max_length=64)


class VoiceApprovalInput(StrictModel):
    envelope_id: str = Field(min_length=16, max_length=80)
    payload_hash: str = Field(min_length=64, max_length=64)


class VoiceEndInput(StrictModel):
    reason: Literal["completed", "resident"] = "resident"


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


class JurisdictionCandidate(BaseModel):
    id: str
    incident_id: str
    display_name: str
    locality: str
    municipality: str
    region: str
    country_code: str
    label: str
    supported: bool
    provider: str
    context_hash: str
    status: Literal["AWAITING_CONFIRMATION", "CONFIRMED", "STALE", "EXPIRED"]
    created_at: str
    expires_at: str


class OfficialContact(BaseModel):
    id: str
    agency: str = Field(max_length=120)
    role: str = Field(max_length=120)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, pattern=r"^\d{3}-\d{3}-\d{4}$")
    source_title: str = Field(max_length=160)
    source_url: str = Field(max_length=2048, pattern=r"^https://")
    source_hostname: str = Field(max_length=253)
    match_reason: str = Field(max_length=300)
    retrieved_at: str


class ContactResearchScope(BaseModel):
    jurisdiction: Literal["Seattle, WA"]
    category: Category


class ContactResearch(BaseModel):
    id: str
    incident_id: str
    candidate_id: str
    context_hash: str
    status: Literal["PENDING", "READY", "FAILED", "STALE", "EXPIRED"]
    query_scope: ContactResearchScope
    contacts: list[OfficialContact] = Field(max_length=3)
    provider: str
    selected_contact_id: str | None = None
    created_at: str
    expires_at: str


class ContactSelection(BaseModel):
    id: str
    incident_id: str
    research_id: str
    contact_id: str
    contact: OfficialContact
    context_hash: str
    status: Literal["SELECTED", "STALE"]
    selected_at: str


class OutreachDraft(BaseModel):
    id: str
    incident_id: str
    channel: Literal["email"]
    revision: int
    subject: str
    body: str
    research_reference: OfficialContact
    selected_contact: OfficialContact | None = None
    research_snapshot_id: str
    execution_target: Literal["internal-email-simulator-v1"]
    context_hash: str
    payload_hash: str
    status: Literal["AWAITING_APPROVAL", "APPROVED", "STALE", "SIMULATED_NOT_SENT"]
    created_at: str
    expires_at: str


class VoiceFact(BaseModel):
    id: Literal["category", "description", "location", "jurisdiction"]
    label: str
    value: str = Field(max_length=160)


class VoiceEnvelope(BaseModel):
    id: str
    incident_id: str
    revision: int
    facts: list[VoiceFact] = Field(min_length=4, max_length=4)
    allowed_intents: dict[str, list[str]]
    refusal_rules: list[str] = Field(max_length=6)
    max_turns: Literal[6]
    max_duration_seconds: Literal[90]
    research_reference: OfficialContact
    selected_contact: OfficialContact | None = None
    research_snapshot_id: str
    execution_target: Literal["internal-voice-simulator-v1"]
    context_hash: str
    payload_hash: str
    status: Literal[
        "AWAITING_APPROVAL",
        "APPROVED",
        "GENERATING",
        "RUNNING",
        "FAILED",
        "STALE",
    ]
    variability_notice: str
    created_at: str
    expires_at: str


class OutreachApproval(BaseModel):
    id: str
    incident_id: str
    record_id: str
    payload_hash: str
    action: Literal["simulate_email", "simulate_voice"]
    status: Literal["APPROVED", "STALE"]
    created_at: str
    expires_at: str


class VoiceTurn(BaseModel):
    id: str
    speaker: Literal["reporting_agent", "fictional_intake_agent"]
    intent: str
    fact_ids: list[str]
    variant_id: str
    caption: str = Field(max_length=300)
    audio_url: str


class VoiceRun(BaseModel):
    id: str
    incident_id: str
    envelope_id: str
    payload_hash: str
    status: Literal[
        "GENERATING",
        "RUNNING",
        "FAILED",
        "COMPLETED",
        "ENDED",
        "INTERRUPTED",
        "STALE",
        "EXPIRED",
    ]
    execution_target: Literal["internal-voice-simulator-v1"]
    created_at: str
    ready_at: str | None = None
    started_at: str | None = None
    transcript_expires_at: str | None = None
    turn_count: int | None = None
    research_snapshot_id: str
    playback_token: str | None = Field(default=None, min_length=32, max_length=128)
    turns: list[VoiceTurn] | None = Field(default=None, max_length=6)


class SimulationReceipt(BaseModel):
    id: str
    incident_id: str
    channel: Literal["email", "voice"]
    status: Literal["SIMULATED_NOT_SENT", "SIMULATED_NOT_DIALED"]
    execution_target: Literal[
        "internal-email-simulator-v1", "internal-voice-simulator-v1"
    ]
    payload_hash: str
    summary: str
    subject: str | None = None
    duration_seconds: int | None = None
    turn_count: int | None = None
    run_status: Literal["COMPLETED", "ENDED", "INTERRUPTED"] | None = None
    research_snapshot_id: str
    created_at: str


class OutreachSnapshot(BaseModel):
    available: bool
    disabled_reason: str | None = None
    voice_available: bool
    jurisdiction: JurisdictionCandidate | None = None
    research: ContactResearch | None = None
    selection: ContactSelection | None = None
    email_draft: OutreachDraft | None = None
    email_receipt: SimulationReceipt | None = None
    voice_envelope: VoiceEnvelope | None = None
    voice_run: VoiceRun | None = None
    voice_receipt: SimulationReceipt | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool
    correlation_id: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
