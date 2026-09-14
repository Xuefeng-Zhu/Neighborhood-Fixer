# Configuration and operating modes

The launcher runs **local** mode only and refuses `NF_MODE=aws`. It reads environment variables from the invoking shell; `.env.example` is a reference, not a secret file automatically loaded by Python. Vite loads its own `.env.local` from `apps/web` for build-time public configuration. Local server secrets are generated into ignored `.local/data` with restricted file permissions and passed only to child processes.

| Variable | Local default / purpose |
|---|---|
| `NF_MODE` | `local` or `aws`; unknown modes are rejected |
| `NF_ENVIRONMENT` | `development`; demo mutation controls require explicit local development |
| `NF_DATA_DIR` | `.local/data`; retained records and private evidence |
| `NF_WEB_PORT` | Starts looking at 5173; launcher chooses a free port through 5183 |
| `NF_SESSION_SECRET` | Server-only; generated/persisted by launcher |
| `NF_PORTAL_SECRET` | Server-only service-to-portal credential; generated/persisted by launcher |
| `NF_PORTAL_URL` | `http://127.0.0.1:8001`; AWS requires reachable allowlisted HTTPS origin |
| `NF_PORTAL_ALLOWED_ORIGINS` | Exact server-configured portal origins |
| `NF_ALLOWED_ORIGINS` | Exact frontend origins; launcher derives from selected port |
| `NF_APPROVAL_SECONDS` | 3600; valid bounds 60–86400 |
| `NF_WORKER_LEASE_SECONDS` | 360; valid bounds 1–900, longer than active browser work |
| `NF_STATUS_INTERVAL_SECONDS` | 60; valid bounds 1–86400 |
| `NF_MAX_STATUS_CHECKS` | 12; valid bounds 1–100 |
| `NF_BROWSER_TIMEOUT_SECONDS` | 90; active browser work only |
| `NF_AGENT_TIMEOUT_SECONDS` | 120; capped at 240 |
| `NF_AGENT_MAX_TURNS` | 8; capped at 12 |
| `NF_AGENT_MAX_TOOLS` | 16; capped at 24 |
| `PLAYWRIGHT_BROWSERS_PATH` | Launcher uses repository `.local/browsers` |
| `UV_CACHE_DIR` | Launcher uses repository `.local/uv-cache` |

AWS additionally needs explicit region/model, table, evidence bucket, runtime/browser identifiers, state machine ARN, Cognito-authorized API Gateway, and frontend configuration. CDK injects server settings and retrieves Secrets Manager values through the Lambda bootstrap. Refer to `docs/aws-deployment.md` and `apps/web/.env.example` for current exact outputs.

`/api/health` reports model provider, agent runtime, browser provider, storage and destination with explicit status. “Configured” means identifiers/settings exist; it does not establish a successful remote call. Local AI and destination are “simulated.” Actual browser/agent activity belongs to the persisted case. Missing AWS configuration returns an actionable error and keeps the operation record; it never changes to fixture mode.

## Fresh start versus process restart

A process restart reuses `.local/data`. A fresh browser session gets an isolated workspace; a local resident switch requires that workspace's existing signed cookie. The development reset endpoint affects only the signed-in workspace and rejects active worker leases. It is not an unrestricted database reset. Fixture assets themselves remain unchanged. Clearing a session cookie does not grant access to a stored workspace identifier.

The local virtual clock changes persisted workspace due-time calculations. It never adjusts the system clock or any AWS service clock. Cloud waits use the configured real durations in Step Functions.

## Generated contracts

The checked-in OpenAPI schema is `packages/contracts/openapi.json`. Export an updated schema from the running local API with `curl --fail http://127.0.0.1:8000/openapi.json -o packages/contracts/openapi.json`, then run `npm run generate:api` to regenerate web types from that checked-in schema. Schema updates must be followed by client regeneration and a TypeScript build. Model/schema validation errors never authorize a browser action.

## Upload limits

Local uploads allow up to 8 MiB per image. AWS uploads allow up to 4 MiB per image, and an approved browser submission may contain at most 4 MiB of total sanitized attachments. The smaller cloud bound accounts for base64 encoding within synchronous Lambda and browser-transfer envelopes. Oversized approved transfers are rejected before any portal request.
