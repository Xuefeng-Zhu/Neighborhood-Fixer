# Test report

Verification date: **2026-09-14 UTC**. All reports, residents, photos and tickets used here are fictional. The historical AWS deployment passed through photo analysis, canonical linking and one immutable draft, stopping before approval. That live evidence predates the current Clerk migration. Clerk deployment, signup, token renewal, quotas, shared admission and post-reset state require fresh hosted acceptance.

## Results

| Check | Actual result | What it establishes |
|---|---|---|
| Python pytest suite | 167 passed; 4 opt-in AWS smoke tests skipped; 3 dependency warnings (14.95s) | Domain/API/portal/browser behavior, actual local Chromium, worker restart, concurrency, authorization, Clerk claim policy, resident/workspace quotas, deterministic Strands tests, artifact and private-journey safeguards |
| Frontend component tests | 29 passed | Clerk token/session lifecycle, private cache isolation, exact approval payload/consent, changed-revision consent reset, error handling, idempotency, AWS map attribution and local-mode isolation |
| Frontend Playwright | 2 passed (23.1s), desktop Chromium plus 390px mobile | Real UI→API→worker→browser→portal→receipt→verification and resident switching; refresh persistence, filters and no horizontal overflow |
| Web TypeScript and production build | Passed | Type consistency with generated OpenAPI types and deployable static bundle |
| AWS browser support tests | 11 passed for Clerk migration | Explicit gate, exact deployed origins, binding metadata, sign-in link, fixed diagnostics, private atomic exports and no application token-storage dependency |
| CDK TypeScript compile | Passed | CDK source compiles against pinned constructs |
| CDK assertions | 15 passed for Clerk migration | Exact Clerk JWT issuer/audience/scope, server quotas/generation, public health/OPTIONS with Idempotency-Key, scoped permissions and temporary legacy retention |
| CDK synthesis | Both variants passed | 45 Clerk-only resources or 48 migration resources excluding metadata; the three-resource difference is the temporarily retained Cognito pool/client/domain, and the asset stack remains four resources with no persistent deployment role |
| Live AWS API and storage smoke tests | 2 passed in a separate authorized run | AWS health, rejected unauthenticated development controls, private S3 settings and active DynamoDB table |
| Historical hosted AWS browser smoke | Both residents passed under the previous identity integration | Authenticated API, maps, disabled controls, refresh/logout and private session exports; this does not establish Clerk live acceptance |
| AgentCore Runtime routing | Passed after the 07:49 UTC deployment, with exact registry constraints | Real Bedrock/Strands routing returned the configured registry values. The initial AWS account-verification gate cleared at 07:31 UTC |
| Real API photo-analysis path | Two residents' synthetic photo analyses passed through S3, Step Functions and Runtime | Real Bedrock/Strands provenance and successful `read_authorized_evidence` and `query_nearby_incidents` activity establish the deployed image-read/orchestration path. The accepted analyses recorded 3 and 4 tool activities and retained 3 unknowns each |
| Separate inline-photo Runtime check | Needs review; `CHECK_FAILED` | This standalone attempt returned no verified success evidence. The real API/S3/Step Functions photo-analysis path was verified separately afterward |
| AgentCore Browser | Custom and built-in sessions remain READY/ENABLED; automation streams return HTTP 404 after account verification cleared | Remote page navigation remains blocked. Start/stream-signing credentials matched when compared in memory; no IAM finding is established, and the cause remains unresolved |
| Deployed fictional portal | HTTP 200 | Portal reachability only; this does not establish remote browser submission or receipt handling |
| Linux ARM64 Lambda and AgentCore container builds | Both passed | Lambda API/portal/worker/bootstrap imports; emulated API health 200 and fixture login 403; non-root AgentCore ping 200 and unauthorized case rejection |
| AWS case journey through draft | Passed; report status `paused_before_approval` | Exact registry routing and coordinator preparation produced one canonical case, two observations and one immutable draft hash. Resident B could not read A's private photo or draft. A's existing photo and completed analysis were reused after review; no duplicate A observation was created. Zero approval requests, browser submissions or agency closures occurred |
| Historical backend deployment | UPDATE_COMPLETE at 08:05:46 UTC; health 200 at 08:07 UTC | The prior application passed the bounded shared-draft journey. Current Clerk source has not inherited that acceptance. |

Passing deterministic Strands SDK graph tests is distinct from a Bedrock model evaluation. The four cloud tests skipped by the full Python suite are not counted as cloud successes; the API/storage results above came from a separate explicitly enabled run.

## Meaningful behaviors exercised

- Two residents preserve independent evidence while linking one canonical incident; the nearby distinct issue remains separate.
- Real local API, portal and worker run as separate processes. An analysis operation survives stopping/restarting its worker.
- Concurrent approvals reserve one operation and one actual browser submission. Material wording/hash/revision changes and expiration invalidate authorization.
- Late, duplicate and spoofed callback attempts cannot bypass stored approval or correlation. No HTTP endpoint exposes callback tokens.
- Lost receipt after actual portal acceptance enters `OUTCOME_UNKNOWN`; receipt lookup recovers the same attempt without another submission.
- Agency closure as “duplicate” remains physically unverified until optional resident verification. “Still present” sends no follow-up.
- Portal server validation rejects changed recipients, wording, contacts, coordinates, attachments and extra fields. Portal text does not select the execution destination.
- Attachment storage fails before ticket acceptance; accepted receipt lookup survives a missing attempt-index update. Replayed attempts must retain the same workspace and payload.
- Before-write cancellation and content-hash mismatches prevent the external click. Optional browser screenshot capture failure preserves the confirmed receipt.
- Private evidence, original contacts and receipt links are excluded from other residents' projections. Photo sharing requires separate explicit consent from sharing a summary.
- Image-derived analysis and phase activity, exact canonical coordinates, and the owner’s draft remain owner-only even after another resident links an observation to the public case. Linked residents keep access to their own private observation.
- Decision operations deduplicate by resident, observation revision and selected outcome. Identical pending or completed replays reuse one operation and one quota charge; failed work can retry, while unlink increments the revision so a later intentional relink is distinct.
- Atomic UTC-day caps apply both per resident and across the shared workspace for reports, upload reservations and reasoning/decision jobs. Concurrent cross-account exhaustion cannot partially consume a losing resident’s quota.
- Expiring sessions, cross-workspace rejection, revoked cloud membership, forged principal headers, CORS preflight boundaries, image decoding/metadata removal and database file permissions are tested.
- Spatial indexes find cases beyond the first 200 unrelated records; membership, subscription, event and pending-job access use bounded scoped indexes.
- AWS unknown/missing configuration fails closed. Cloud browser origins must be HTTPS and allowlisted; aggregate transport limits reject oversized approved transfers before any external call.
- AWS fictional-ticket status changes require demo environment, explicit opt-in and the service secret. Operator preview, exact status/note confirmation, allowlisted stack destination, disabled/production rejection and uncertain-write inspection are tested with deterministic clients.
- Actual Strands SDK execution is tested with deterministic model implementations: tool invocation, validated structured output, and active routing→coordinator graph ordering. Evidence SHA and per-photo read guards are tested. These are not real-model accuracy tests.
- Two synthetic residents completed the deployed Hosted UI flow independently. Browser checks confirmed actual authenticated API responses, rejected local-only endpoints, no local resident/scenario controls, loaded Amazon Location styles and tiles with attribution, and correct session behavior through reload and logout. No AWS case was submitted by these checks.
- Cloud browser verification records only fixed phase labels, numeric status codes, booleans and control counts. Requested bearer-session exports are atomic, owner-readable files; tokens, account identifiers, authorization URLs, screenshots and raw browser diagnostics are excluded from test output.
- Two residents' synthetic-photo analyses completed through private S3, Step Functions and AgentCore Runtime, with real Bedrock/Strands provenance and successful authorized-evidence and nearby-incident tools. The accepted analyses retained three unknowns each. The final continuation reused A's existing photo and completed analysis after checking the prior operations and observation inputs; it created no duplicate A observation or new A photo analysis.
- Real registry-constrained routing and coordinator preparation produced one canonical case and an immutable draft. B's first observation linked to that case without changing its payload hash, and B could not read A's private draft or photo. The saved continuation report is `paused_before_approval`, with zero approval requests, no Browser submission and no agency closure. Receipt, status polling and resident verification remain unverified in AWS.
- Runtime routing passed against exact configured registry constraints after the latest verified deployment. The separate remote Browser stream failure persists despite READY/ENABLED sessions and matching credentials; no IAM cause has been established.

## Visual evidence

The built-in Codex browser was used for the report sequence, duplicate selection, resident switching, exact approval, actual receipt display, and 390px responsive inspection. Automated Playwright then exercised the full UI story in isolated contexts. The inspected mobile DOM reported `innerWidth=390`, document width 390 and scroll width 390. Desktop and mobile render evidence is in `docs/verification`.

Early visual tests found missing MapLibre worker geometry, stale resident switching, and an upload-preview overflow that intercepted a consent checkbox. These were fixed and the browser journeys rerun. Development hot-reload context errors were addressed by separating the session context/hook module. Final screenshots use generated synthetic assets, never stock images presented as resident evidence.

- [Neighborhood desktop](verification/neighborhood-desktop.png)
- [Case desktop](verification/case-desktop.png)
- [Case mobile](verification/case-mobile.png)
- [Design comparison](design/FIDELITY.md)

## Reproduce

See the exact command block in README. Python browser tests need local loopback sockets, installed Playwright Chromium and `PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/browsers"`. The latest suite passed 167 tests with four cloud tests skipped. On restrictive sandboxes local browser/socket actions require execution permission; a sandbox-denied socket bind was followed by the successful authorized rerun reported above. The frontend browser suite must target the Neighborhood Fixer URL when another Vite project occupies its default port; the verified run used `NF_WEB_URL=http://127.0.0.1:5174`. No failing invocation was counted as passing.

The latest Python run reported three warnings and no test failures. Live-cloud evaluation requires its own authorized credentials, reachable HTTPS deployment and explicit `NF_RUN_AWS_SMOKE=1` gate. Hosted browser verification separately requires `NF_RUN_AWS_BROWSER_SMOKE=1` and privately supplied synthetic-account credentials. Its standalone browser runner intentionally saves no screenshots, traces or authentication DOM. Do not count skipped cases as successful AWS integration.

Source quality checks passed: Ruff Python formatting and F-series checks, Prettier, and the checked-in OpenAPI export matching all 30 paths in the running API. Vite reports a non-failing large-chunk advisory for MapLibre; bundle splitting and real-network performance remain potential optimizations.

AWS deployment history: the asset stack contains four resources and zero IAM roles; the current main synthesis contains 48 resources excluding CDK metadata. Build contexts contain only allowlisted source and synthetic fixtures (under 10 MB), excluding local evidence, credentials, caches and generated assets. AWS CLI 2.36.44 was installed from the signed official package. Both stacks and Amplify hosting are deployed. Initial application creation rolled back because AgentCore started before its ECR policy; a regression now verifies that dependency. Four retained empty resources from the failed attempt were checked and removed before retrying. An explicit unauthenticated OPTIONS route corrected the deployed CORS preflight rejection while authenticated API routes remain JWT protected.

During historical hosted-login verification, the smoke runner was corrected for the provider's named submit control and native fetch Response.ok. Both synthetic residents then passed that earlier flow. The current Clerk smoke targets the application Sign in link and provider Continue controls, checks exact binding claims and waits through a real token-renewal interval; it still requires live execution. AWS account verification cleared at 07:31 UTC. Earlier model and draft tool-guard defects were corrected before the successful shared-draft run. AgentCore Browser automation-stream HTTP 404 remains unresolved, and remote submission, receipt, agency closure, status polling and resident verification remain unverified. See [AWS status](AWS-STATUS.md).
