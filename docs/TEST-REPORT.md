# Test report

Verification date: **2026-09-14 UTC**. All reports, residents, photos, and tickets used during validation were fictional. The deployed application is available at [main.d12il66dljooo6.amplifyapp.com](https://main.d12il66dljooo6.amplifyapp.com/), backed by [the us-west-2 API](https://09iviho9v3.execute-api.us-west-2.amazonaws.com).

## Official contact research and simulated outreach — current branch

The current branch adds the owner-initiated Seattle contact-research workflow and the internal email and voice simulators. Local development exercises the complete authorization and activity flow with labeled deterministic fixtures. Every execution target remains internal: the email simulator sends no message and the voice simulator dials no number. This branch has not been deployed; the public URL above remains on the preceding release.

| Check | Result | What it establishes |
|---|---|---|
| Python pytest suite | 245 passed, 4 explicitly skipped live-AWS tests, 3 dependency warnings | Research, selection, approval, privacy, idempotency, expiry, validation, transient cleanup, provider failure, and audio authorization behavior |
| Frontend Vitest | 54 passed | Typed API integration, truthful fixture/provider labels, manual contact selection, approval revisions, voice controls, captions, reload, and failure states |
| Local Playwright | 2 passed | Complete desktop workflow through manual contact selection, simulated email receipt, captioned voice simulation and cleanup, plus mobile map and draft persistence |
| Live bounded Seattle.gov extraction | Passed | The pinned-IP, suffix-range fetcher read exactly 256 KB from the current SDOT contact page and extracted its shared department email and phone without production hard-coding |
| CDK assertions | 18 passed | Disabled-by-default feature gating, encrypted TTL storage, scoped Brave secret access, Location and Polly permissions, streaming audio API, and absence of outbound email or telephony resources |
| Web typecheck and production build | Passed | Generated API types and the production frontend compile |
| CDK build and synthesis | Passed | Infrastructure code compiles and the feature resources synthesize with the intended IAM and response-streaming configuration |
| OpenAPI semantic parity | Passed | The checked-in OpenAPI document matches the FastAPI application schema |
| Generated client parity | Passed | A fresh TypeScript client generation matches the checked-in client byte for byte |
| Frozen audio dependency export | Passed | The dedicated audio Lambda dependency set resolves from the lockfile |

Live Brave search, Amazon Location reverse geocoding, Bedrock role execution, Polly synthesis, and hosted progressive audio streaming have not been run for this branch. The audio container image was not built locally because no Docker daemon was available; frozen dependency export and CDK asset synthesis passed. `ContactResearchEnabled` remains false until a Brave key is configured, the subscription's selected-contact storage terms are approved, IAM is reviewed in the target account, and the live acceptance checks pass. No live email or call test is expected because real delivery and dialing are intentionally absent from this feature.

## Release results

| Check | Result | What it establishes |
|---|---|---|
| Python pytest suite | 182 passed, 4 explicitly skipped live-AWS tests, 3 dependency warnings | Domain, API, portal, worker restart, concurrency, authorization, Clerk claim policy, quotas, deterministic Strands behavior, reset safeguards, and privacy boundaries |
| Frontend Vitest | 29 passed | Clerk session lifecycle, cache isolation, approval consent, idempotency, AWS map attribution, and local-mode isolation |
| Local Playwright | 2 passed against Neighborhood Fixer on port 5174 | Desktop and mobile browser journeys through the local API, worker, fictional portal, receipt, and resident switching |
| Hosted helper Node tests | 11 passed | Hosted-origin gating, Clerk binding metadata, private session export safeguards, and browser-smoke support |
| CDK assertions | 14 passed | Exact Clerk JWT issuer/audience/scope, quotas, generation fencing, public health and OPTIONS, scoped permissions, and zero Cognito resources |
| Web typecheck and production build | Passed | Generated API types and the production frontend compile; Vite's large-chunk advisory is non-blocking |
| CDK build and synthesis | Passed | The final template compiles and contains no Cognito resource |
| Hosted Clerk acceptance | Passed | Public signup, exact JWT binding, authenticated API, token renewal, refresh, logout, and disposable-account cleanup |
| Amazon Location map | Passed | Style, tiles, rendered canvas, and attribution loaded from the hosted origin using the restricted key |
| Authorization negative tests | Passed | An unadmitted valid user received 403 during closed validation; a tampered token received 401 |
| Live report quota | Passed | Ten new reports exhausted the per-resident 10/10 daily allowance; the next new request received 429, while an idempotent replay returned its original 201 without another charge |
| Real two-resident cloud journey | Passed through immutable draft | Private S3, Step Functions, AgentCore Runtime, Bedrock/Strands analysis, canonical linking, owner-only evidence, and neighbor-redacted draft behavior |
| Final reset and public opening | Passed | One inert sample incident and observation remain, with zero QA identities, operations, tickets, approvals, uploads, or evidence objects; admission is public |
| Identity-provider retirement | Passed | CloudFormation contains zero Cognito resources and the exact retained user pool was deleted |
| AgentCore Browser | Blocked by automation-stream HTTP 404 | Custom and built-in sessions report READY/ENABLED, but remote navigation never began; submission, receipt, status polling, and closure remain unverified |

The four skipped pytest cases require an explicit live-AWS gate and are not counted as cloud successes. Live results in the table came from separately authorized hosted and AWS runs. No agency submission was made.

## Live Clerk and frontend acceptance

The hosted app uses Clerk issuer `https://working-turtle-1775.clerk.accounts.dev`, audience `neighborhood-fixer-api`, and scope `nf:resident`. Browser validation confirmed the token issuer, audience, authorized-party origin, scope, subject, and session binding before exercising authenticated API reads. It also waited through a real renewal interval, refreshed the page, signed out, and confirmed that local resident/scenario controls are unavailable in AWS mode.

Two restricted synthetic residents completed the validation journey. A separate disposable account proved that a valid but unadmitted identity was rejected while admission was closed, and a tampered bearer token was rejected as unauthenticated. After the reset, public hosted signup was verified against the one-sample workspace. Every synthetic Clerk user was then deleted, both temporary allowlist entries were removed, and Clerk's allowlist restriction was disabled. The Clerk instance ended with zero users and zero allowlist entries.

The hosted map fetched an Amazon Location style and tiles, rendered a non-empty canvas, and displayed attribution. The map credential is map-only, scoped to the deployed map, restricted to the exact Amplify referrer, and time-limited. These checks establish that the map works on the deployed site; they do not measure every device, network, or browser combination.

## Live backend acceptance

Two synthetic residents uploaded distinct photos through the real API. The service sanitized and stored them privately in S3, ran durable Step Functions jobs, invoked AgentCore Runtime, and received Bedrock/Strands analyses with authorized evidence reads and nearby-incident queries. Both observations linked to one canonical case. The owner's immutable draft hash stayed fixed; the second resident could not read the owner's photo or private draft and saw the redacted neighbor projection.

Server-owned required facts determined whether analysis could proceed. Model-suggested questions that were not required by the server remained non-blocking unknowns. The run stopped at `paused_before_approval`, with zero approval requests, external browser writes, receipts, or agency closures.

Quota validation consumed exactly the ten allowed new reports for one resident. Request eleven returned 429. Replaying a previously accepted idempotency key returned the original 201 response and left usage at 10/10, establishing that retries do not consume a second unit.

The reviewed reset removed the validation inventory and seeded generation `clerk-public-v1`. Final read-only verification found exactly one inert sample incident and its observation, no admitted QA identities, no workflow operations, no tickets or approvals, no portal records, and no S3 versions, delete markers, or multipart uploads. The sample has no evidence or submission path. Public admission was then enabled and a fresh hosted signup confirmed access to that state.

## Security and failure behavior exercised

- Private evidence, original contacts, receipt links, analysis details, coordinates, and drafts remain owner-scoped. Community publication is a separate explicit consent.
- Concurrent approvals reserve one operation. Material wording, hash, attachment, or revision changes invalidate approval.
- Late, duplicate, spoofed, and mismatched callbacks cannot bypass stored authorization or correlation. No public endpoint accepts a task token.
- Ambiguous external writes enter `OUTCOME_UNKNOWN` and are never automatically replayed. Receipt lookup can reconcile the same attempt.
- Portal validation rejects changed recipients, wording, contacts, coordinates, attachments, and extra fields. Portal content cannot choose an execution destination.
- Atomic daily limits cover resident and shared-workspace reports, uploads, and reasoning jobs. Idempotent retries and worker redelivery do not double-charge.
- Cloud configuration fails closed for wrong issuer, audience, authorized party, scope, workspace generation, or admission state.
- The final CloudFormation template and deployed stack contain no Cognito resources; the former retained pool no longer exists.

## Known limitation

AgentCore Browser remains independently blocked. Both custom and AWS built-in sessions reached READY with automation enabled, but signed CDP connections returned HTTP 404 before navigation. The fictional portal itself returned HTTP 200, which establishes reachability only. No attachment transfer, agency submission, receipt capture, status poll, closure, or resident verification was performed in AWS. The complete fictional portal flow remains covered by local Chromium tests.

## Reproduce

See the command blocks in the README and [AWS deployment guide](aws-deployment.md). Browser tests need loopback sockets, Playwright Chromium, and an explicit Node browser cache such as `PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/ms-playwright-js"`. Live tests require explicit gates and authorized credentials; skipped tests must never be reported as live successes.

Source quality checks also passed Ruff F/E9 checks and formatting on the changed Python files, Prettier on the changed JavaScript/TypeScript files, TypeScript, the production frontend build, CDK build/synthesis, and `git diff --check`. Local Playwright passed two journeys against Neighborhood Fixer on port 5174. Earlier setup attempts encountered an empty browser cache and an unrelated app on port 5173; neither was a Neighborhood Fixer product failure. Vite reports a non-failing MapLibre bundle-size advisory.

Visual regression evidence remains in [docs/verification](verification), including the [desktop neighborhood](verification/neighborhood-desktop.png), [desktop case](verification/case-desktop.png), and [mobile case](verification/case-mobile.png). The inspected mobile layout used a 390px viewport with no horizontal overflow.
