# Security boundaries and limitations

## Authority and consent

The resident and deterministic domain commands authorize actions. Agents can read authorized evidence, compare cases, consult a pinned registry, or propose report wording and next actions; they cannot approve themselves, change recipients, bypass permission checks, or create an agency write. Every real Strands invocation uses a fresh case/principal context. Untrusted image text, portal text, and descriptions are data rather than instructions.

An immutable report revision contains the exact recipient, category, wording, location, contact object, attachment IDs and content hashes. Approval binds the actor, incident, revision, recipient, action, payload hash, attachment hashes and expiration. Material edits require a new approval. Adding observations, following, and timeline changes do not modify a frozen report. Domain transactions reserve one attempt before execution. Cancellation is checked and write-start recorded immediately before the browser click. After a write begins, cancellation cannot claim to undo it.

Community publication is an independent opt-in. An early reviewed observation can be shared to let another neighbor find it before the agency report is approved. Agency-send approval does not implicitly authorize publication. Shared summaries use dedicated projections, rounded coordinates and explicitly approved sanitized photos. Contacts, original file bytes, server paths, approval objects, private receipts and callback tokens are not public projections.

## Identity and private resources

Local authentication uses signed HttpOnly SameSite=Strict cookies. An unguessable workspace ID is not sufficient to join it. Resident switching and scenario controls require both local mode and development environment. Cloud API identity comes only from Cognito claims validated by API Gateway and supplied through Mangum's trusted Lambda event. Spoofable request headers are ignored. Cloud sharing requires an administrator-provisioned membership record; revocation is consulted on every authenticated request. Private evidence and approvals remain owned by the contributing resident even within a shared workspace.

Origin allowlists bound credentialed CORS. Local services bind loopback. AWS has encrypted private S3 and DynamoDB, retained-data policies, scoped IAM and no browser AWS access-key credentials. Map credentials are restricted, expiring rendering keys, never server secret keys. The fictional portal uses short-lived opaque transfer grants, exact form validation, conditional ticket creation, and private opaque receipt links. Those links are bearer capabilities: anyone the owner intentionally shares one with can view the fictional receipt. Access logs are disabled locally and referrer policy is `no-referrer` to reduce link leakage.

## Evidence

The API checks safe image types, input size, decodability and pixel count, strips EXIF and other metadata, and persists a newly encoded JPEG. Original upload bytes are discarded after sanitization. Hashes are checked before browser transfer. Files remain private until explicit community sharing. Uploaded photos may be excluded before approval, and after-photos remain private unless separately shared.

Metadata removal does **not** detect faces, plates, addresses visible inside an image, or other sensitive visible content. Automatic image redaction is not implemented. Residents must review and exclude sensitive photos or redact them before uploading. No person identification is performed. Public free text is resident-reviewed; automated sensitive-text detection/moderation is not implemented. Deployment should remain an invited fictional demo until those broader public-release controls are reviewed.

## Durable and external-system limits

The local worker persists jobs, due times, attempts and leases; restarting does not rely on surviving an in-memory timer. AWS Step Functions Standard owns durable waits and server-only callbacks. Expiration and callback correlation are deterministic, with approval-before-registration reconciliation. No public endpoint accepts a task token. SDK/controller logs omit private reasoning and arbitrary tool data.

An external website write is not atomic with an application transaction. A failure after possible acceptance becomes `OUTCOME_UNKNOWN`. Recovery uses a receipt lookup for that attempt or requires human review. There is no automatic replay of an ambiguous write. A receipt can be captured even while physical resolution is still unverified; agency closure, including “duplicate,” never certifies a repair. “Still present” creates no automatic escalation or outbound follow-up.

## Deployment and scope limits

- AWS resources are deployed in the authorized Oregon account. The latest backend update reached `UPDATE_COMPLETE` at 08:05:46 UTC on 2026-09-14; subsequent health checks returned HTTP 200 at 08:07 UTC. Both ARM64 containers, storage privacy, and hosted Cognito PKCE/API/map/refresh/logout checks passed. The owner Cognito account remains in `FORCE_CHANGE_PASSWORD` for an owner-controlled first login.
- The bounded cloud path from uploaded photos to a shared draft passed: two actual photo analyses through S3, Step Functions and AgentCore Runtime, exact registry routing and Coordinator preparation, one canonical case with two observations, an immutable draft, and privacy checks. The journey intentionally stopped before approval; it performed no browser submission or agency closure. The initial Bedrock account-verification gate cleared at 07:31 UTC and is historical.
- Custom and built-in Browser sessions report `READY` with automation `ENABLED`, but their automation streams still return HTTP 404 before navigation. The cause remains unresolved; neither IAM nor the historical Bedrock verification gate has been established as the cause. Remote attachment transfer and the complete approval → submission → receipt → closure workflow remain unverified. See [current AWS status](AWS-STATUS.md).
- The only receiving adapter exercised end to end is the fictional Demo Borough portal. Real municipal auto-submission is disabled. Ambiguous or unsupported routes provide a handoff packet; reverse geocoding is never proof of ownership.
- Exact duplicate detection cannot be guaranteed across independent reports. Category-specific distance, time, asset identity, evidence comparison and resident confirmation narrow candidates. Canonical locking prevents duplicate delivery for one reserved incident action, not every semantically similar issue ever created.
- Small-demo caps are deliberate: 50-record list pages, 100 records per queried geographic cell, 100-event current timeline plus paginated history, 20 followers per incident, and bounded active jobs. Workspace-wide conditional write serialization limits throughput. Dense neighborhoods need pagination/load testing and a broader discovery model.
- Receipt links have high-entropy capabilities but are not a general-purpose municipal authentication system. The portal retains fictional report payloads. No real government policy, service-level deadline, repair certification or public-safety claim is represented.
- Accessibility has been checked through browser layout, labels and keyboard-capable native controls; no independent assistive-technology audit or participant field study has been conducted. No real-world “safe” or “fun/useful for everyone” claim follows from automated tests.
- Follow-up packet preparation exists. Automatic outbound follow-up, live municipal Open311, email, AgentCore Memory/Gateway, vector search, voice, payments, rewards, crime reporting and social feeds are outside this implementation.

For a vulnerability report, create a private report through the repository owner's chosen channel when one is published. This build has not published a repository or established a public security contact.
