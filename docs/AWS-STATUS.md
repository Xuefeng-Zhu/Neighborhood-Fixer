# AWS deployment status

Historical live snapshot: **2026-09-14 08:07 UTC**, account ending **4085**, **us-west-2**. Current source is migrating to Clerk; the deployment results below predate that migration and must not be treated as its acceptance.

- App: https://main.d12il66dljooo6.amplifyapp.com
- API: https://09iviho9v3.execute-api.us-west-2.amazonaws.com
- Stacks: `NeighborhoodFixerAssets` and `NeighborhoodFixer`.
- Hosting: Amplify manual deployment job `1` succeeded.
- Latest backend update: `UPDATE_COMPLETE` at **08:05:46 UTC**; subsequent app, API, authenticated workspace, CORS preflight and fictional portal checks all returned HTTP200.
- Target authentication: Clerk development instance, ordinary session JWT with exact issuer/audience/azp and nf:resident scope; public signup and shared Demo Borough admission open only after validation and reset.
- Restricted Amazon Location rendering key expires **2026-10-14 00:00 UTC**.

The destination is a fictional agency. No real municipal request, hackathon submission, or Git publication occurred.

| Acceptance layer | Current evidence |
|---|---|
| Cloud infrastructure | Both stacks deployed successfully. An initial ECR policy ordering failure was corrected; its four retained resources were verified empty and removed. |
| API and storage | Live health, rejected unauthenticated development controls, private S3 settings and active DynamoDB table passed. Explicit unauthenticated OPTIONS preflight works; private requests remain JWT protected. |
| Historical hosted authentication | Both synthetic residents passed the previous hosted login, authenticated API, rejected local controls, Location style/tiles/attribution, refresh and logout. Clerk live signup/renewal/logout remain pending. |
| Owner account | The previous identity-provider account is not a Clerk sign-in. The owner must claim/configure the reviewed Clerk instance and establish their own account; no old password is migrated. |
| AI Runtime | Real Bedrock/Strands photo analysis and exact registry routing/preparation passed. Both synthetic observations retained uncertainty and actual tool activity. The initial account-verification gate cleared; source/recipient constraints, unnecessary clarification and premature structured-output acceptance were corrected and verified. No fixture fallback was used. |
| Cloud case through draft | Two real photo analyses used private S3, Step Functions and Runtime. Two observations linked to one canonical case; the frozen draft hash remained unchanged. The neighboring resident could read neither the private photo nor the owner's draft. The case remains `AWAITING_APPROVAL`; zero approval requests, browser submissions or agency closure actions were sent. |
| Remote Browser | Custom and AWS built-in Browser sessions report READY with ENABLED automation streams, but CDP connections return HTTP404 before navigation. A built-in comparison produced five such responses in one bounded session, which was stopped. The fictional portal returns HTTP200. No documented custom-role permission gap or credential mismatch was found; the cause remains unresolved. |
| Full case workflow | Cloud approval-to-browser submission, approved attachment transfer, receipt, agency status polling and resident verification remain unverified. The complete flow passed locally with real local Chromium and the fictional portal. |

The cloud driver stopped with `paused_before_approval`. Earlier failed operations were inspected; the existing first observation, sanitized photo and successful analysis were reused when continuing. No failed write was blindly replayed, and no duplicate first observation was created. Private evidence reports remain in ignored `.local`; their credentials and identifiers are not included here. The separate earlier direct inline-photo check failed to produce verification evidence and is not counted as a success.

The Browser failure persists independently of the cleared Bedrock verification gate. A private AWS support draft is prepared; no support message has been sent. Resolve the Browser connection first, then review the saved existing case before authorizing any next action. Do not rerun the full journey against a fresh report path to bypass existing-case checks.

Current migration checks passed the 167-test Python suite (four explicit live-AWS skips), 29 frontend tests, 11 hosted-helper tests, two local browser journeys and 15 CDK assertions. Clerk-only synthesis contains 45 application resources; the temporary validation variant contains 48, preserving three legacy login resources until cutover. The separate pre-migration live results are listed above. See [test report](TEST-REPORT.md) and [deployment/reset/teardown guide](aws-deployment.md). No AWS mutations were performed by these local migration checks.
