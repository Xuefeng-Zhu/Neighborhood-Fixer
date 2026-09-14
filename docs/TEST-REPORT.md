# Test report

Verification date: **2026-09-14 UTC**. All reports, test residents, photos, and agency tickets used here are fictional. The AWS demo infrastructure and hosted frontend are deployed; no real municipal requests were created. The live two-resident case journey passed through photo analysis, canonical linking and an immutable draft, then intentionally stopped before approval. The final backend deployment reached `UPDATE_COMPLETE` at 08:05:46 UTC; post-deployment health checks returned HTTP 200 at 08:07 UTC.

## Results

| Check | Actual result | What it establishes |
|---|---|---|
| Python pytest suite | 106 passed; 4 opt-in AWS smoke tests skipped; 3 warnings (15.91s) | Domain/API/portal/browser behavior, actual local Chromium, worker restart, concurrency, authorization, deterministic Strands tests, artifact and private-journey safeguards |
| Frontend component tests | 11 passed | Exact approval payload/consent, changed-revision consent reset, error handling, OAuth expiry/state rejection, AWS map attribution and local-mode isolation |
| Frontend Playwright | 2 passed (20.4s), desktop Chromium plus 390px mobile | Real UI→API→worker→browser→portal→receipt→verification and resident switching; refresh persistence, filters and no horizontal overflow |
| Web TypeScript and production build | Passed | Type consistency with generated OpenAPI types and deployable static bundle |
| AWS browser support tests | 9 passed | Explicit cloud gate, validated deployment origins, safe diagnostics, independent request failures and private atomic session exports |
| CDK TypeScript compile | Passed | CDK source compiles against pinned constructs |
| CDK assertions | 12 passed | Key services, private data boundaries, callback graph, Cognito URL matching, ECR policy ordering, explicit unauthenticated CORS preflight and default-disabled portal-only status controls |
| CDK synthesis | Passed; 48 main-stack resources excluding CDK metadata | CloudFormation/resource and asset definitions can be generated locally |
| Live AWS API and storage smoke tests | 2 passed in a separate authorized run | AWS health, rejected unauthenticated development controls, private S3 settings and active DynamoDB table |
| Hosted AWS browser smoke | Both synthetic residents passed every phase | Actual Cognito S256 PKCE login, authenticated API, disabled local controls, Amazon Location styles and tiles, attribution, page refresh, logout and signed-out refresh; requested session exports were private and mode 0600 |
| AgentCore Runtime routing | Passed after the 07:49 UTC deployment, with exact registry constraints | Real Bedrock/Strands routing returned the configured registry values. The initial AWS account-verification gate cleared at 07:31 UTC |
| Real API photo-analysis path | Two residents' synthetic photo analyses passed through S3, Step Functions and Runtime | Real Bedrock/Strands provenance and successful `read_authorized_evidence` and `query_nearby_incidents` activity establish the deployed image-read/orchestration path. The accepted analyses recorded 3 and 4 tool activities and retained 3 unknowns each |
| Separate inline-photo Runtime check | Needs review; `CHECK_FAILED` | This standalone attempt returned no verified success evidence. The real API/S3/Step Functions photo-analysis path was verified separately afterward |
| AgentCore Browser | Custom and built-in sessions remain READY/ENABLED; automation streams return HTTP 404 after account verification cleared | Remote page navigation remains blocked. Start/stream-signing credentials matched when compared in memory; no IAM finding is established, and the cause remains unresolved |
| Deployed fictional portal | HTTP 200 | Portal reachability only; this does not establish remote browser submission or receipt handling |
| Linux ARM64 Lambda and AgentCore container builds | Both passed | Lambda API/portal/worker/bootstrap imports; emulated API health 200 and fixture login 403; non-root AgentCore ping 200 and unauthorized case rejection |
| AWS case journey through draft | Passed; report status `paused_before_approval` | Exact registry routing and coordinator preparation produced one canonical case, two observations and one immutable draft hash. Resident B could not read A's private photo or draft. A's existing photo and completed analysis were reused after review; no duplicate A observation was created. Zero approval requests, browser submissions or agency closures occurred |
| Latest backend deployment | `UPDATE_COMPLETE` at 08:05:46 UTC; health checks HTTP 200 at 08:07 UTC | The final deployed application passed the live draft journey. The owner account remains `FORCE_CHANGE_PASSWORD`; synthetic test-account success does not establish owner sign-in |

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

See the exact command block in README. Python browser tests need local loopback sockets, installed Playwright Chromium and `PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/browsers"`. An earlier full-suite invocation omitted this environment variable and produced five missing-browser-binary failures; its corrected rerun passed 92 tests. The latest suite passed 106 tests with four cloud tests skipped. On restrictive sandboxes local browser/socket actions require execution permission; an earlier sandbox-denied socket bind was also a tool-environment failure, followed by a successful authorized rerun. No failing invocation was counted as passing.

The latest Python run reported three warnings and no test failures. Live-cloud evaluation requires its own authorized credentials, reachable HTTPS deployment and explicit `NF_RUN_AWS_SMOKE=1` gate. Hosted browser verification separately requires `NF_RUN_AWS_BROWSER_SMOKE=1` and privately supplied synthetic-account credentials. Its standalone browser runner intentionally saves no screenshots, traces or authentication DOM. Do not count skipped cases as successful AWS integration.

Source quality checks passed: Ruff Python formatting and F-series checks, Prettier, and the checked-in OpenAPI export matching all 30 paths in the running API. Vite reports a non-failing large-chunk advisory for MapLibre; bundle splitting and real-network performance remain potential optimizations.

AWS deployment history: the asset stack contains four resources and zero IAM roles; the current main synthesis contains 48 resources excluding CDK metadata. Build contexts contain only allowlisted source and synthetic fixtures (under 10 MB), excluding local evidence, credentials, caches and generated assets. AWS CLI 2.36.44 was installed from the signed official package. Both stacks and Amplify hosting are deployed. Initial application creation rolled back because AgentCore started before its ECR policy; a regression now verifies that dependency. Four retained empty resources from the failed attempt were checked and removed before retrying. An explicit unauthenticated OPTIONS route corrected the deployed CORS preflight rejection while authenticated API routes remain JWT protected.

During actual Hosted UI verification, the smoke runner was corrected to target Cognito Classic's named submit control and to use native fetch's boolean `Response.ok`. Both synthetic residents then passed every hosted browser phase. AWS account verification cleared at 07:31 UTC. Earlier model results incorrectly treated unknown dimensions, safety and ownership as blocking clarification; a later draft attempt failed its tool guard. Those application defects were corrected and the inspected existing observation was continued without duplicating A's evidence. The final run passed through canonical linking and draft validation, then intentionally stopped before approval. The separate inline-photo attempt still needs review, and AgentCore Browser automation-stream HTTP 404 remains unresolved. Remote submission, receipt, agency closure, status polling and resident verification have not been established. See [AWS deployment status](AWS-STATUS.md) for the deployment handoff.
