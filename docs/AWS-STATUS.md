# AWS deployment status

Current verified snapshot: **2026-09-14 UTC**, account ending **4085**, region **us-west-2**.

- Application: [https://main.d12il66dljooo6.amplifyapp.com/](https://main.d12il66dljooo6.amplifyapp.com/)
- API: [https://09iviho9v3.execute-api.us-west-2.amazonaws.com](https://09iviho9v3.execute-api.us-west-2.amazonaws.com)
- Application stack: `NeighborhoodFixer`
- Asset stack: `NeighborhoodFixerAssets`
- Active data generation: `clerk-public-v1`
- Admission: `public`
- Authentication: Clerk development issuer `https://working-turtle-1775.clerk.accounts.dev`, audience `neighborhood-fixer-api`, scope `nf:resident`

The destination is a fictional agency. No municipal request, approval, browser submission, receipt, status update, or closure was sent during cloud validation.

| Acceptance layer | Current evidence |
|---|---|
| Cloud infrastructure | Both stacks are deployed. Health, CORS preflight, private DynamoDB/S3 settings, and the public Amplify origin passed. The final CloudFormation stack contains zero Cognito resources, and the exact former user pool was deleted. |
| Clerk authentication | Public hosted signup passed with exact issuer, audience, authorized party, scope, subject, and session binding. Authenticated API reads, token renewal, refresh, and logout passed. A tampered token returned 401. |
| Clerk cleanup | The Clerk instance contains zero users and zero allowlist entries. The temporary allowlist restriction is disabled, so new users follow the public hosted signup flow. No test credential remains in the service. |
| Workspace admission | Server admission is public for `clerk-public-v1`. A fresh disposable public signup joined the server-selected Demo Borough and saw the inert sample before the account was deleted. Client claims and headers cannot choose a workspace. |
| Clean dataset | Exactly one inert sample incident and one sample observation remain. There are zero admitted QA identities, workflow operations, tickets, approvals, attempts, callbacks, portal records, evidence objects, delete markers, or multipart uploads. |
| Amazon Location | The hosted map loaded its style and tiles, rendered a canvas, and displayed attribution using a map-only key restricted to the exact Amplify referrer. |
| AI Runtime | Two residents completed real private-photo analysis through S3, Step Functions, AgentCore Runtime, and Bedrock/Strands. Both linked to one canonical case and one unchanged draft, with owner-only photo/draft enforcement and redacted neighbor views. |
| Quotas and retries | One resident reached the 10/10 report limit; the next new request returned 429. An idempotent replay returned the original 201 without increasing usage. |
| Remote AgentCore Browser | Custom and built-in sessions reach READY with automation enabled, but signed CDP connections return HTTP 404 before navigation. The fictional portal itself returns HTTP 200. |
| Full AWS case workflow | Approval-to-submission, attachment transfer, receipt, status polling, closure, and resident verification remain unverified because remote Browser navigation is blocked. The fictional end-to-end flow passes locally with Chromium. |

The cloud journey intentionally stopped at `paused_before_approval`. Its validation inventory was subsequently removed by the reviewed reset. Final verification was read-only after the one-sample seed, and public signup was tested with a disposable user that was deleted afterward.

Current automated results are 182 Python tests passed with 4 explicit live-AWS skips and 3 dependency warnings, 29 frontend tests passed, 2 local Playwright journeys passed against Neighborhood Fixer on port 5174, 11 hosted-helper tests passed, and 14 CDK assertions passed. Web typecheck/build and CDK build/synthesis passed. Vite's bundle-size warning is non-blocking. See the [test report](TEST-REPORT.md) and [deployment guide](aws-deployment.md).

The remaining cloud blocker is the AgentCore Browser automation stream. Do not infer submission or receipt success from the portal's HTTP 200 response or the Browser session's READY status.
