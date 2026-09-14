# Security boundaries and limitations

## Authority and consent

Residents and deterministic domain commands authorize actions. Agents may read authorized evidence, compare cases, consult the pinned registry, and propose wording or next actions. They cannot approve themselves, change recipients, bypass ownership checks, or create an agency write. Each Strands invocation receives a fresh case and principal context. Text in images, descriptions, tools, and portal pages is untrusted data rather than executable instruction.

An immutable report revision binds recipient, category, wording, location, contact object, attachment IDs, content hashes, actor, incident, action, and expiration. Material edits require a new approval. Domain transactions reserve one attempt before execution. Cancellation is checked and write-start is recorded immediately before an external click; after a write begins, cancellation cannot claim to undo it.

Community publication is an independent opt-in. Agency-send approval does not grant publication consent. Public summaries use dedicated projections, rounded coordinates, and only explicitly shared sanitized photos. Contacts, original files, private analysis, exact coordinates, approval objects, receipts, and callback tokens are excluded.

## Identity and private resources

Local authentication uses signed HttpOnly SameSite=Strict cookies. Resident switching and scenario controls require local development mode. Cloud identity comes only from API Gateway-validated Clerk JWT claims in Mangum's trusted event. API Gateway checks issuer, audience, and `nf:resident`; the backend also checks the exact `azp` frontend origin, version-2 user/session binding, expiry, admission, and disabled-resident state. The server assigns the Demo Borough and active data generation. A known workspace ID, client claim, or header cannot grant membership.

The live Clerk issuer is `https://working-turtle-1775.clerk.accounts.dev`, with audience `neighborhood-fixer-api`. Public hosted signup, renewal, logout, a 401 tampered-token rejection, and a 403 unadmitted-user rejection during closed validation were verified. After public acceptance, all disposable users and allowlist entries were deleted and the allowlist restriction was disabled. The Clerk instance ended with zero users. No Clerk secret is deployed to the frontend, Lambda, or Runtime.

The final CloudFormation stack contains zero Cognito resources, and the exact former user pool was deleted. There is no alternate cloud login path in the application stack.

Credentialed CORS uses an exact origin allowlist. Local services bind loopback. AWS evidence storage and DynamoDB are private and encrypted, with scoped IAM and no browser AWS access keys. The Amazon Location credential is restricted to map rendering, the deployed map ARN, the exact Amplify referrer, and a finite expiry. The hosted app uses `strict-origin-when-cross-origin`; private API and portal responses use `no-referrer`.

The fictional portal uses short-lived opaque transfer grants, exact form validation, conditional ticket creation, and high-entropy private receipt links. Receipt links are bearer capabilities: anyone with a deliberately shared link can view that fictional receipt.

## Evidence

The API accepts only bounded safe image types, verifies decodability and pixel count, strips metadata, and persists a newly encoded JPEG. Original upload bytes are discarded after sanitization. Content hashes are checked before browser transfer. Evidence remains private until the owner separately shares a sanitized projection.

Metadata removal does **not** detect faces, license plates, visible addresses, or other sensitive content inside pixels. Automatic visual redaction and text moderation are not implemented. Residents must review or redact an image before publication. No person identification is performed.

## Durable and external-system limits

Local jobs persist due times, attempts, and leases across worker restarts. In AWS, Step Functions Standard owns durable waits and server-only callbacks. Expiration and callback correlation are deterministic, including approval-before-registration reconciliation. No public endpoint accepts a task token. SDK and controller logs omit prompts, tool arguments, images, and arbitrary exception text.

An external website write cannot be atomic with an application transaction. A failure after possible acceptance becomes `OUTCOME_UNKNOWN`. Recovery looks up the same attempt or requires human review; it never automatically repeats an ambiguous write. An agency closure, including a duplicate disposition, does not prove physical repair. A resident's “still present” response creates no automatic outbound follow-up.

The deployed AgentCore Browser path remains unverified beyond session start. Custom and built-in sessions report READY with automation enabled, but signed CDP connections return HTTP 404 before navigation. Portal HTTP 200 does not establish submission. No cloud approval, attachment transfer, browser submission, receipt, status poll, closure, or resident verification occurred during validation.

## Public demo limits

The public workspace contains one inert sample incident and observation, zero retained QA identities or operations, and no evidence objects. A fresh disposable signup verified public admission and was deleted afterward. The sample has no evidence, ticket, approval, job, or submission path.

Atomic resident and workspace UTC-day limits bound new reports, accepted uploads, and reasoning jobs. The deployed report limit was verified at 10/10: a new request returned 429, while an idempotent replay returned the original 201 without another charge. API rate limits, Clerk Smart CAPTCHA, and shared quotas constrain abuse, but they are not strong identity proof or an AWS budget. A production service needs a production Clerk instance, owned custom domain, stronger identity/cost controls, monitoring, and policy review.

The fictional Demo Borough portal is the only receiving adapter exercised end to end locally. Real municipal auto-submission is disabled. Ambiguous or unsupported routes produce a handoff packet. Reverse geocoding is not evidence of agency ownership.

Exact duplicate detection cannot be guaranteed across independent descriptions. Category-specific distance, time, asset identity, evidence comparison, and resident confirmation narrow candidates. Canonical locking prevents duplicate delivery for one reserved incident action; it cannot prove that every semantically similar report is the same issue.

Small-demo caps remain deliberate: 50-record list pages, 100 records per queried geographic cell, a 100-event current timeline plus paginated history, 20 followers per incident, and bounded active jobs. Dense deployments require load testing, broader discovery, operational monitoring, and abuse review.

Receipt links are not a municipal authentication system. The portal retains only fictional payloads. The app makes no real government-policy, service-level, repair-certification, or public-safety claim. Accessibility checks cover layout, labels, keyboard-capable native controls, and responsive automation; no independent assistive-technology audit or participant field study has been completed.

Follow-up packet preparation exists. Automatic outbound follow-up, live Open311, email, AgentCore Memory/Gateway, vector search, voice, payments, rewards, crime reporting, and social feeds remain outside this implementation.

Report security issues privately to the repository owner until a public security contact or private vulnerability-reporting channel is published.
