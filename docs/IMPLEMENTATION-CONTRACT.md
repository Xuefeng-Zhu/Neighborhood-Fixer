# Shared implementation contract

The authoritative HTTP contract is the generated [OpenAPI schema](../packages/contracts/openapi.json). [Pydantic models](../services/api/models.py) define inputs, outputs, statuses and error envelopes. The web client consumes generated TypeScript schema types. See [configuration](CONFIGURATION.md) for regeneration commands.

## Domain ownership

`services/api/domain.py` owns authorization, immutable drafts, material hashes, approval expiration, canonical attempt reservation, worker leases, observation links, independent agency/resident status, events and notifications. The frontend and model tools call these commands; they do not reproduce or override the rules.

SQLite and DynamoDB implement the same scoped storage interface. The local worker persists jobs and due times. Step Functions Standard owns cloud waits and callbacks. A fresh Strands context performs bounded active reasoning for the authorized case; it is not a second durable workflow engine.

## Identity and visibility

A local session initially creates an isolated workspace. Joining by a known workspace ID is insufficient: switching seeded residents requires the existing signed cookie. AWS identity comes from API Gateway's trusted Clerk JWT event with exact issuer/audience/scope and backend azp/session/expiry checks. The server assigns the shared workspace and generation, checks admission and disabled residents on every request, and enforces atomic per-resident and workspace daily observation/upload/reasoning quotas. Client-selected workspace or identity metadata is never authoritative.

Agency-send consent, public-summary consent and public-photo consent are distinct. Public incident projections exclude private contacts, receipt capabilities, internal storage paths, approvals, callback tokens, exact canonical coordinates and evidence-derived analysis. Evidence access is checked on every request. Shared images are sanitized derivatives; original upload bytes are discarded after decoding and sanitization.

## Browser execution

`services/worker/browser.py` exposes the typed agency adapter and shared deterministic Playwright flow. Both local Chromium and AgentCore Browser use that flow. Only the configured allowlisted portal origin may receive the frozen payload and approved attachments.

The domain reserves an attempt before execution and checks authorization again immediately before the external click. A failure before the click is distinct from an ambiguous result after possible acceptance. `OUTCOME_UNKNOWN` is reconciled through the original attempt's receipt lookup, never a blind second submission. Screenshot capture and optional screenshot storage cannot discard a confirmed receipt.

## Demo boundaries

The fictional portal persists actual tickets and validates the exact prepared form server-side. Local scenario endpoints require local development mode and the signed workspace session. Identity switching, reset and virtual-clock controls remain disabled in AWS. Cloud fictional-ticket management, when explicitly enabled, uses the separate server-authenticated operator path documented in the [AWS deployment guide](aws-deployment.md).

Unsupported routing yields clarification or an assisted handoff packet. Real municipal submissions remain disabled. Integration health reports distinguish configured, tested, unavailable and simulated states; errors never switch AWS operations into fixtures.
