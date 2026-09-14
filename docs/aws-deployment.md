# AWS demo deployment and teardown

**Status: infrastructure synthesized locally; nothing deployed to AWS.** SDK calls and IAM/resource definitions exist in code. Cloud smoke tests require an explicit opt-in and were skipped. Local success does not establish AWS account access, model availability, container execution, hosted login, remote browser attachment transfer, or deployed end-to-end behavior. Docker packaging could not be built in the implementation environment because the Docker daemon was unavailable.

The receiving agency remains fictional in every mode. This project contains no authorized real municipal adapter.

## Reproducible preparation (no cloud writes)

From the repository root:

```sh
uv sync --all-extras
npm --prefix infra/cdk ci
npm --prefix infra/cdk run build
npm --prefix infra/cdk test
npm --prefix infra/cdk run synth
uv run --all-extras pytest tests/test_agents.py tests/test_aws_smoke.py -q
```

`infra/cdk/cdk.out/NeighborhoodFixer.template.json` is the synthesized template. CDK synth registers Docker assets without building or deploying them. Python dependencies are pinned in `uv.lock`; CDK has an independent `infra/cdk/package-lock.json`. Dockerfiles use uv 0.11.2, matching the locally verified frozen export, and Linux ARM64 Python 3.13.

Once Docker is running, validate both deployment images before requesting deployment approval:

```sh
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.lambda -t neighborhood-fixer-lambda .
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.runtime -t neighborhood-fixer-runtime .
```

The default AWS path runs Issue Analyst first, then Routing Specialist, and prepares wording with an actual Routing Specialist → Case Coordinator Strands graph. The graph rechecks the configured recipient and cannot authorize or perform an external write.

The Runtime image launches `python -m services.agents.runtime`; the SDK serves the documented HTTP contract on port 8080. Lambda's bootstrap retrieves the generated portal service secret into process memory for the worker and portal before importing their handlers. The API does not request that secret; AWS resident authentication uses Cognito. The bootstrap does not print secret values. The portal and API are separate Lambda entrypoints in the same modular application image.

## Account and endpoint prerequisites

Use an explicitly authorized AWS account and `us-west-2` (the verified default intersection for Runtime, Browser, and Nova 2 Lite). Configure CLI authentication yourself. Confirm service quotas and access to `us.amazon.nova-2-lite-v1:0`; this is the US geographic inference profile, so requests may be processed in supported US destination regions. The parameter is explicit, but choosing another model also requires reviewing IAM model resource ARNs and capabilities.

Choose the actual HTTPS frontend origin, without a trailing slash. The origin is used for API CORS and the map-key referrer restriction. Cognito callback and logout URLs both append `/` to that origin, so `VITE_COGNITO_REDIRECT_URI` must match the resulting root URL exactly. The CDK stack creates an empty Amplify Hosting app and branch; it does not connect GitHub or publish a site. If using the generated Amplify domain, first create the stack with a reserved origin under your control, then read the Amplify app domain and redeploy with its actual branch origin before using login or publishing. A custom frontend domain requires the owner's DNS and Amplify domain configuration.

Cognito self-registration is disabled. Provision invited test residents through Cognito admin tooling; password and MFA setup remain resident-controlled. Cloud cases default to one workspace per Cognito `sub`. For the two-neighbor cloud demo, an administrator can provision exact existing Cognito subjects into one reviewed workspace using the command below. The API reads the server-side membership record on every request; callers cannot choose a workspace through headers. No fixture identity selector exists in AWS mode.

```sh
# Read-only validation against the authorized table:
uv run --all-extras python infra/cdk/provision_workspace.py \
  --workspace DEMO_WORKSPACE_ID --user-sub RESIDENT_A_SUB --user-sub RESIDENT_B_SUB
# After reviewing this exact membership change, add --apply to write it.
```

The command refuses reassignment of an existing resident to another workspace. Membership records live in a reserved auth partition. The application exposes no HTTP membership-write endpoint; the provision script uses administrative AWS credentials. Trusted API/worker IAM roles have table write access, so these are application-enforced membership boundaries, not per-partition IAM isolation. A resident can contribute to a shared case but still cannot approve another resident's draft or read their private evidence.

## Deploy only after explicit account/environment approval

These commands create billable AWS resources. They are instructions, not commands that were run during implementation:

```sh
export AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2
# Confirm this is the explicitly authorized account before any cloud write:
aws sts get-caller-identity --query Account --output text
cd infra/cdk
npx cdk bootstrap aws://AUTHORIZED_ACCOUNT_ID/us-west-2
npx cdk diff
npx cdk deploy NeighborhoodFixer \
  --parameters FrontendOrigin=https://YOUR-APP-ORIGIN \
  --parameters BedrockModelId=us.amazon.nova-2-lite-v1:0 \
  --parameters MapKeyExpiry=2027-09-13T00:00:00Z \
  --outputs-file ../../.local/aws-outputs.json
```

The output file contains identifiers and URLs, never server secret values. The actual HTTPS `PortalUrl` Function URL is injected into the worker. No cloud browser points at localhost. AgentCore Runtime uses IAM authorization; the frontend receives no permission to invoke it directly. API Gateway verifies Cognito JWTs before the application trusts `requestContext.authorizer.jwt.claims`.

Resources: API Lambda, worker Lambda, fictional portal Lambda, Cognito user pool/client, HTTP API, records and portal DynamoDB tables, private evidence S3 bucket, Standard workflow, custom AgentCore Browser, AgentCore Runtime container, scoped IAM roles, CloudWatch logs, Amplify app/branch, and an Amazon Location map with an expiring map-only referrer-restricted API key. No VPC or NAT gateway is created.

## Frontend configuration and publishing

Run the following application commands from the repository root. Set build-time Vite settings using `apps/web/.env.example`; the file's example region is `us-east-1`, so replace it with the actual deployed region (`us-west-2` for this guide). AWS settings are never a substitute for deployed verification:

- `VITE_API_BASE_URL` = `ApiUrl` output, without an added `/api` suffix; the client appends API paths.
- `VITE_AWS_REGION` = deployed region; `VITE_COGNITO_CLIENT_ID` = `UserPoolClientId`; `VITE_COGNITO_DOMAIN` = `CognitoDomain`; `VITE_COGNITO_REDIRECT_URI` = exact frontend origin plus `/`.
- `VITE_LOCATION_MAP_NAME` = `LocationMapName`; `VITE_LOCATION_API_KEY` = the restricted public map API key's value. This key is designed for browser use and is restricted to map rendering, one map ARN, the configured referrer, and an explicit expiry. It is not an AWS access key. Retrieve its value using the authenticated Location console or `DescribeKey` into a local ignored environment file; do not paste it into source, chat, or logs.
- The frontend determines local/AWS mode from `/api/health`, not a build-time mode flag. Keep that health route reachable without authentication so the Cognito sign-in screen can load; all private API routes remain JWT protected.

Build with `npm ci && npm run build`. Upload only `apps/web/dist` to the created Amplify app's `main` branch using Amplify manual deployment after publication approval. The repository includes the Amplify build specification in CDK for a future owner-authorized repository connection. No repository token is stored in CDK. Verify the registered root callback URL, deep-link refreshes, API CORS, map tiles and login/logout on the actual deployed origin.

## Fictional agency status changes in AWS

Cloud ticket status management is **disabled by default**. To enable the operator path for a reviewed AWS demo, update the stack with `--parameters DemoStatusManagementEnabled=true` along with the existing frontend-origin parameters. This maps only the portal Lambda to `NF_ENABLE_DEMO_STATUS_MANAGEMENT=true`. The endpoint also requires `NF_MODE=aws`, `NF_ENVIRONMENT=demo`, and the portal service secret; production still rejects it. The browser-facing app's identity switching, reset, virtual-clock and local scenario endpoints remain disabled in AWS.

After the deployed report has a receipt, run the operator command from the repository root using the authorized AWS account:

```sh
uv run --all-extras python scripts/set_demo_ticket_status.py \
  --stack-name NeighborhoodFixer --region us-west-2 \
  --receipt-id ACTUAL_DEMO_RECEIPT_ID --status CLOSED \
  --closure-note 'Fictional agency reports completion; resident verification is still pending.' \
  --confirm
```

Omit `--confirm` to preview the exact target and proposed status without fetching the secret or writing. If a write's response is lost, rerun the command with `--inspect` instead of `--status`, `--closure-note`, and `--confirm` to read the persisted outcome before considering another update.

The operator needs CloudFormation `DescribeStacks` and Secrets Manager `GetSecretValue` for the stack's portal secret. The command derives `PortalUrl` and `PortalSecretArn` from the trusted stack outputs, checks that the portal is an enabled AWS demo, fetches the secret into memory and sends a single status update without following redirects. It does not accept an arbitrary recipient URL or print the secret. Stack outputs include the secret ARN, never its value.

Status changes are persisted by the portal's normal application transition. Run the demo update while the bounded status-read workflow is active (12 checks at 60-second intervals by default), then wait for its next real check; the operator command does not advance AWS time. Agency `CLOSED` still leaves physical resolution awaiting optional resident verification. Disable the operator path after the demo by redeploying with `DemoStatusManagementEnabled=false`. This path is implemented for deployment validation but has not been exercised against AWS.

## Deployment validation

Export stack outputs into a private shell environment as `NF_TABLE_NAME`, `NF_EVIDENCE_BUCKET`, `NF_AGENTCORE_RUNTIME_ARN`, `NF_AGENTCORE_BROWSER_ID`, `NF_PORTAL_URL`, `NF_AWS_API_URL`, and `AWS_REGION`. Then, only with approval for billable smoke calls:

```sh
NF_RUN_AWS_SMOKE=1 uv run --all-extras pytest tests/test_aws_smoke.py -q
```

The four smoke tests inspect private storage, run the Routing Specialist through actual AgentCore Runtime, open an AgentCore Browser session at the fictional portal, and check health plus unauthenticated rejection of the fixture-login endpoint. They do not test real multimodal analysis, the report-preparation graph, an authenticated development-control rejection, or a complete Step Functions execution. They never submit a report. The full authenticated report → approval → one browser write → receipt → agency closure → resident verification story needs separate testing against a dedicated demo workspace. Use the explicitly enabled operator status path above to drive the fictional agency response; no AWS clock is advanced. Missing environment, permission, model, routing, or browser configuration must fail visibly; there is no fixture fallback.

AWS uploads are capped at 4 MiB per image, and the browser executor and portal cap combined submission attachments at 4 MiB to leave room for Lambda's encoded request envelope. Local uploads allow 8 MiB per image. Remote attachment transfer is still untested.

The Lambda worker is limited to three concurrent invocations and a five-minute invocation timeout. Each model phase has at most 8 model calls, 16 tool calls, and 120 seconds; the tool budget cancels excess calls and the graph enforces a deadline. Bedrock transport retries are capped at two total attempts. The browser session has a maximum 300-second lifetime, and the form executor has its own shorter deadline. Safe status reads have bounded retries and 12 configured checks; ambiguous external writes are never automatically retried.

These are operational limits, not a hard spending cap. Cost budgets/alerts and an account owner-reviewed teardown date are recommended before running a public demo.

## Observability and recovery

`services/agents/observability.py` exports allowlisted OpenTelemetry operation spans as structured CloudWatch log records with trace ID, duration, operation type, outcome and tool count. It deliberately omits prompt text, tool arguments, images, private model reasoning and arbitrary exception content. The response contains actual provider usage only when the SDK returns it. No token costs are inferred. Strands verbose logs and AgentCore browser recording are disabled.

Standard workflow CloudWatch logs exclude execution data. Execution history still contains workflow inputs and callback task payloads and therefore requires restricted AWS operator access. Callback tokens stay in private application records and Lambda task payloads, never HTTP responses or browser state. The initial decision workflow waits for its exact revision, while approved submission runs as a separate persisted operation; the existing domain reservation remains the authority. Approval-before-registration is reconciled from the committed attempt/approval. Consumed tokens are removed. Expired or superseded callbacks cannot authorize a write.

If dispatch fails, the operation remains persisted with an actionable API error. After correcting configuration, a server administrator may call `start_operation(workspace_id, operation_id)` for that exact persisted operation. Duplicate StartExecution names do not create a second operation. Do not generate a new submission attempt to recover a lost receipt. Use the case's reconcile action, which performs receipt lookup only.

## Teardown and retained data

After explicit teardown approval, review `cdk diff` and run `npx cdk destroy NeighborhoodFixer`. Evidence, both DynamoDB tables, and the Cognito pool are intentionally **retained**. Runtime/Browser resources, workflow, hosting, and explicitly created application log groups are removed according to their resource policies. Runtime-created `/aws/bedrock-agentcore/runtimes/*` log groups are not explicit CDK resources here and may remain; inspect their retention and deletion separately. CDK bootstrap ECR assets and bootstrap resources may also remain. Inventory those resources in the authorized account and delete them only after the owner approves the exact data/retention consequences. Never infer that `cdk destroy` removed retained personal data or all continuing charges.
