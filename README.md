# Neighborhood Fixer

**Report once. Follow through together.**

A working neighborhood case manager for damaged sidewalks and curb ramps, potholes, and blocked walkways. Two residents can contribute evidence to one incident, approve one exact agency report, receive a browser-captured receipt, and independently verify whether a repair happened. Agency closure never automatically means the physical issue is fixed.

## Run locally

Install Node.js 24 LTS, Python 3.11–3.13, and [uv](https://docs.astral.sh/uv/getting-started/installation/). From this directory:

```sh
npm run dev
```

That command installs the pinned npm/Python dependencies and Chromium when needed, then starts the React application, FastAPI API, persisted workflow worker, and functional fictional agency portal. No AWS credentials are required. The first start needs package-registry download access.

Open the web address printed by the launcher: normally **http://localhost:5173**. It selects the next free port through 5183 if another app already uses that port. In the implementation environment the app runs on **http://localhost:5174**. API: `http://127.0.0.1:8000`; fictional portal: `http://127.0.0.1:8001`. Ctrl+C stops all four processes. The API and portal ports must be free; the launcher never terminates another application.

State, sanitized evidence, local service secrets, and browser receipts persist in ignored `.local/data`. Refreshing or restarting the processes preserves cases. Open Local scenario controls to switch residents, control a fictional ticket, advance the local virtual clock, simulate a lost receipt, or reset only the currently isolated demo workspace. Local identity switching is never enabled in AWS mode.

The local banner always says **“Local demo — simulated AI and fictional agency.”** Local analyst responses are deterministic fixtures, not Bedrock/Strands executions. Browser interactions still execute the actual portal form. Demo photos in `fixtures/images` are original synthetic illustrations, not real-world observations.

## Try the central story

1. As Alex, report a curb ramp using **Use illustrative demo photo**. Describe it, optionally share the reviewed summary so neighbors can find it, separately opt in to sharing its photo, and confirm the fictional Maple/Alder location and public-walkway statement.
2. Review evidence/unknowns, choose a distinct issue, and leave the exact report awaiting approval.
3. Switch to Sam in the same local workspace, report the same ramp, and choose **Add my observation**. There are now two observations and one incident.
4. Return to Alex, review the immutable report, contacts and attachment hashes, choose publication consent separately, and approve the exact submission. The actual browser submits one fictional ticket and records its receipt.
5. Set the ticket to Closed through local scenario controls and advance the virtual clock. Physical resolution stays unverified. Choose Looks fixed, Still present, or Unable to verify, optionally attaching an after-photo. Both followers see the case outcome.

Use the [five-minute demo script](docs/DEMO-SCRIPT.md) for the main story, ambiguous routing and lost-receipt scenarios. A lost receipt produces `OUTCOME_UNKNOWN`; reconciliation looks up the original attempt rather than blindly resubmitting it.

## Validation

```sh
UV_CACHE_DIR=.local/uv-cache uv sync --locked --all-extras
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m pytest -q
.venv/bin/ruff format --check services scripts tests infra/cdk
npm run typecheck
npm run test:web
npm run build
# Keep npm run dev running for browser UI tests. Set the printed web URL:
NF_WEB_URL=http://127.0.0.1:5174 npm run test:e2e
npm --prefix infra/cdk ci
npm --prefix infra/cdk run build
npm --prefix infra/cdk test
npm --prefix infra/cdk run synth
```

The Python integration tests start their own isolated API, portal and worker with disposable records. They exercise real Chromium, concurrent approvals and worker restart. AWS smoke tests require explicit opt-in; skipped cloud tests are not passing cloud evidence. See the [test report](docs/TEST-REPORT.md) for actual results and limits.

## AWS mode

The application is deployed in **us-west-2** at [Neighborhood Fixer](https://main.d12il66dljooo6.amplifyapp.com). Hosted Cognito login, maps, storage privacy, real Bedrock photo analysis, routing and report preparation passed. Two synthetic residents linked their observations to one unchanged draft, with private photos and drafts protected. The cloud check stopped before approval. **Full AWS behavior is not yet verified:** AgentCore Browser automation streams return HTTP404 before navigation, blocking submission and receipt verification. The initial AWS account-verification gate has cleared. See [current AWS status](docs/AWS-STATUS.md) for precise results and the owner sign-in step.

See [deployment/teardown](docs/aws-deployment.md), [SDK/reference verification](docs/aws-api-verification.md), [access patterns](docs/aws-access-patterns.md), and [configuration](docs/CONFIGURATION.md). Deploy only after the owner explicitly selects and authorizes an AWS account/environment. AWS errors remain visible; there is no silent fixture fallback. The receiving agency stays fictional. Real municipal submissions are disabled.

## Repository

| Path | Responsibility |
|---|---|
| `apps/web` | React, TypeScript, Router, Query, RHF/Zod, Tailwind, MapLibre |
| `apps/demo-portal` | Portal entry and development notes |
| `services/api` | FastAPI, typed domain commands, access control, SQLite adapter |
| `services/worker` | Persisted job runner and deterministic browser adapter |
| `services/portal` | Real fictional form, validation, attachments, ticket/receipt storage |
| `services/agents` | Real Strands, AgentCore, DynamoDB, durable AWS bridge, sanitized telemetry |
| `packages/contracts` | Generated OpenAPI source; generated TypeScript types consumed by web |
| `infra/cdk` | AWS CDK app, assertions and deployment Dockerfiles |
| `fixtures` | Versioned fictional registry and synthetic assets |
| `tests` | Domain, portal, real-browser, restart and gated cloud tests |
| `docs` | Architecture, security, demo/submission draft and truthful validation |

The domain is the authority for permissions, material revisions, submission reservations, and status. Model tools only read authorized data or propose bounded actions. Original observations are preserved. Linking an observation does not create an independent submission workflow.

[Architecture Mermaid source](docs/architecture.mmd) · [Security and limitations](docs/SECURITY.md) · [Submission draft](docs/DEVPOST-DRAFT.md) · [Disclosure](docs/DISCLOSURE.md) · [MIT license](LICENSE)

The implementation and dependency lockfiles are retained in Git. No pull request, hackathon registration, or hackathon submission was performed by this build.
