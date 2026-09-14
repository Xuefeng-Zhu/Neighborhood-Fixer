# Test report

Verification date: **2026-09-14 UTC**. All local reports, residents, photos, and agency tickets used here are fictional. No AWS resources or municipal requests were created.

## Results

| Check | Actual result | What it establishes |
|---|---|---|
| Python pytest suite | 75 passed; 4 AWS smoke tests skipped (17.08s) | Domain/API/portal/browser behavior, actual local Chromium, worker restart, concurrency, authorization and deterministic Strands tests |
| Frontend component tests | 11 passed | Exact approval payload/consent, changed-revision consent reset, error handling, OAuth expiry/state rejection, AWS map attribution and local-mode isolation |
| Frontend Playwright | 2 passed (20.4s), desktop Chromium plus 390px mobile | Real UI→API→worker→browser→portal→receipt→verification and resident switching; refresh persistence, filters and no horizontal overflow |
| Web TypeScript and production build | Passed | Type consistency with generated OpenAPI types and deployable static bundle |
| CDK TypeScript compile | Passed | CDK source compiles against pinned constructs |
| CDK assertions | 6 passed | 46-resource stack's key services, private data boundaries, callback graph, Cognito URL matching and bootstrap-secret wiring and default-disabled portal-only status controls |
| CDK synthesis | Passed | CloudFormation/resource and asset definitions can be generated locally |
| Actual Bedrock/AgentCore/DynamoDB/S3/Cognito/Location calls | Not run; 4 smoke tests explicitly skipped | No cloud behavior is claimed as verified |
| Linux ARM64 Lambda and AgentCore container builds | Not completed: Docker daemon unavailable | Container build/startup remains unverified |
| AWS deployment, hosted UI and remote portal journey | Not run | No account/environment deployment authorized |

Passing deterministic Strands SDK graph tests is distinct from a Bedrock model evaluation.

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

## Visual evidence

The built-in Codex browser was used for the report sequence, duplicate selection, resident switching, exact approval, actual receipt display, and 390px responsive inspection. Automated Playwright then exercised the full UI story in isolated contexts. The inspected mobile DOM reported `innerWidth=390`, document width 390 and scroll width 390. Desktop and mobile render evidence is in `docs/verification`.

Early visual tests found missing MapLibre worker geometry, stale resident switching, and an upload-preview overflow that intercepted a consent checkbox. These were fixed and the browser journeys rerun. Development hot-reload context errors were addressed by separating the session context/hook module. Final screenshots use generated synthetic assets, never stock images presented as resident evidence.

- [Neighborhood desktop](verification/neighborhood-desktop.png)
- [Case desktop](verification/case-desktop.png)
- [Case mobile](verification/case-mobile.png)
- [Design comparison](design/FIDELITY.md)

## Reproduce

See the exact command block in README. Python browser tests need local loopback sockets and installed Playwright Chromium. On restrictive sandboxes those actions require execution permission; an initial sandbox-denied socket bind was a tool-environment failure, then the same test ran successfully with local-process permission. No failing check was counted as passing.

The two Python warnings are upstream deprecations from Starlette's AnyIO BlockingPortal alias and Mangum's event-loop lookup during import. No test failure resulted. Live-cloud evaluation requires its own authorized credentials, reachable HTTPS deployment and explicit `NF_RUN_AWS_SMOKE=1` gate. Do not count those skipped cases as successful AWS integration.

Source quality checks passed: Ruff Python formatting and F-series checks, Prettier, and the checked-in OpenAPI export matching all 30 paths in the running API. Vite reports a non-failing large-chunk advisory for MapLibre; bundle splitting and real-network performance remain potential optimizations.
