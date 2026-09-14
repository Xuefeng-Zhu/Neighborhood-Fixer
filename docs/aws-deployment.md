# AWS demo deployment and teardown

**Status as of 2026-09-14 08:07 UTC:** both stacks and the Amplify frontend are deployed in `us-west-2`. The latest backend update reached `UPDATE_COMPLETE` at 08:05:46 UTC; subsequent health checks returned HTTP 200. AWS setup and temporary CLI access are authorized. Live storage privacy and API checks passed. Hosted Cognito PKCE, authenticated API, map, refresh and logout checks passed for both test residents. The owner Cognito account remains in `FORCE_CHANGE_PASSWORD` for an owner-controlled first login.

The bounded cloud path from uploaded photos to a shared draft passed: two actual photo analyses through S3, Step Functions and AgentCore Runtime, exact registry routing and Coordinator preparation, one canonical case containing two observations, an immutable draft, and privacy checks. The journey intentionally stopped before approval; it performed no browser submission or agency closure. The initial Bedrock account-verification gate cleared at 07:31 UTC and is historical.

Custom and built-in Browser sessions report `READY` with automation `ENABLED`, but their automation streams persistently return HTTP 404 before page navigation. Neither IAM nor the historical Bedrock verification gate has been established as the cause. Remote attachment transfer and the complete approval → submission → receipt → closure workflow remain unverified. See [current AWS status](AWS-STATUS.md).

**Evidence boundary:** the live result above establishes the bounded photo-to-shared-draft path. Local tests and synthesis provide separate contract evidence: the final Python suite passed 106 tests with four opt-in AWS smoke tests skipped. Those skips do not negate the separately executed live journey, and the journey does not establish the untested submission and closure stages.

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

Validate both deployment images with Docker before deployment:

```sh
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.lambda -t neighborhood-fixer-lambda .
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.runtime -t neighborhood-fixer-runtime .
```

Local container checks passed: Lambda API/portal/worker/bootstrap imports, emulated API health 200 and fixture login 403, plus non-root AgentCore ping 200 and rejection of an unauthorized case. These checks did not contact AWS.

The default AWS path runs Issue Analyst first, then Routing Specialist, and prepares wording with an actual Routing Specialist → Case Coordinator Strands graph. The graph rechecks the configured recipient and cannot authorize or perform an external write.

The Runtime image launches `python -m services.agents.runtime`; the SDK serves the documented HTTP contract on port 8080. Lambda's bootstrap retrieves the generated portal service secret into process memory for the worker and portal before importing their handlers. The API does not request that secret; AWS resident authentication uses Cognito. The bootstrap does not print secret values. The portal and API are separate Lambda entrypoints in the same modular application image.

## Account and endpoint prerequisites

Use an explicitly authorized AWS account and `us-west-2` (the verified default intersection for Runtime, Browser, and Nova 2 Lite). Configure CLI authentication yourself. Confirm service quotas and access to `us.amazon.nova-2-lite-v1:0`; this is the US geographic inference profile, so requests may be processed in supported US destination regions. The parameter is explicit, but choosing another model also requires reviewing IAM model resource ARNs and capabilities.

Leave `FrontendOrigin` empty for the generated Amplify URL. The stack derives `https://main.<Amplify DefaultDomain>` and outputs it as `WebUrl`; API CORS, Cognito and map-key referrer restrictions use that URL during the first deployment. The Amplify app has no backend-dependent settings; branch environment settings reference the completed backend, avoiding a dependency cycle. For an owner-configured custom domain, set `FrontendOrigin` to its exact HTTPS origin without a trailing slash. Cognito callback and logout URLs both append `/`; `VITE_COGNITO_REDIRECT_URI` must match exactly. CDK creates an empty app and branch; it does not connect GitHub, configure custom-domain DNS, or publish files. [Amplify App DefaultDomain](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-amplify-app.html), [branch environment settings](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-amplify-branch.html).

Cognito self-registration is disabled. Provision invited test residents through Cognito admin tooling; password and MFA setup remain resident-controlled. Cloud cases default to one workspace per Cognito `sub`. For the two-neighbor cloud demo, an administrator can provision exact existing Cognito subjects into one reviewed workspace using the command below. The API reads the server-side membership record on every request; callers cannot choose a workspace through headers. No fixture identity selector exists in AWS mode.

```sh
# Read-only validation against the authorized table:
uv run --all-extras python infra/cdk/provision_workspace.py \
  --workspace DEMO_WORKSPACE_ID --user-sub RESIDENT_A_SUB --user-sub RESIDENT_B_SUB
# After reviewing this exact membership change, add --apply to write it.
```

The command refuses reassignment of an existing resident to another workspace. Membership records live in a reserved auth partition. The application exposes no HTTP membership-write endpoint; the provision script uses administrative AWS credentials. Trusted API/worker IAM roles have table write access, so these are application-enforced membership boundaries, not per-partition IAM isolation. A resident can contribute to a shared case but still cannot approve another resident's draft or read their private evidence.

## Deploy only after explicit account/environment approval

These commands create billable AWS resources in the explicitly selected account. The application-specific asset path uses the current CLI credentials for asset publication and CloudFormation. It creates a private retained S3 bucket and retained ECR repository, without persistent deployment roles. Credentials must remain valid throughout deployment. [CDK current-credential synthesizer](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.CliCredentialsStackSynthesizer.html).

```sh
export AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2
# Check the returned account against the explicitly authorized account first:
aws sts get-caller-identity --query Account --output text
export NF_DEPLOY_ACCOUNT=AUTHORIZED_ACCOUNT_ID
export NF_ASSET_BUCKET="nf-assets-${NF_DEPLOY_ACCOUNT}-${AWS_REGION}"
export NF_ASSET_REPOSITORY=neighborhood-fixer-assets
cd infra/cdk
npx cdk synth --quiet
aws cloudformation deploy \
  --template-file cdk.out/NeighborhoodFixerAssets.template.json \
  --stack-name NeighborhoodFixerAssets
npx cdk diff NeighborhoodFixer
npx cdk deploy NeighborhoodFixer \
  --parameters BedrockModelId=us.amazon.nova-2-lite-v1:0 \
  --parameters MapKeyExpiry=2026-10-14T00:00:00Z \
  --outputs-file ../../.local/aws-outputs.json
```

Both `NF_ASSET_BUCKET` and `NF_ASSET_REPOSITORY` must remain set together for later synth, diff, deploy and destroy operations. S3 asset uploads require TLS; the bucket is encrypted and versioned. ECR uses immutable content-hash tags, scan-on-push and an untagged-image expiration rule; tagged images are retained so a running Lambda or Runtime does not lose its image. The deployment principal must have the required CloudFormation, asset publication and service creation permissions, including Lambda's documented ECR access. [Lambda image permissions](https://docs.aws.amazon.com/lambda/latest/dg/images-create.html).

An existing owner-approved standard CDK bootstrap environment is an optional alternative: omit both asset variables to use `DefaultStackSynthesizer` and its configured bootstrap roles. Review that environment's roles and trust policies before using it; creating a standard bootstrap stack is a separate infrastructure change. The application-specific path above does not require `cdk bootstrap`.

The output file contains identifiers and URLs, never server secret values. The actual HTTPS `PortalUrl` Function URL is injected into the worker. No cloud browser points at localhost. AgentCore Runtime uses IAM authorization; the frontend receives no permission to invoke it directly. API Gateway verifies Cognito JWTs before the application trusts `requestContext.authorizer.jwt.claims`.

Resources: API Lambda, worker Lambda, fictional portal Lambda, Cognito user pool/client, HTTP API, records and portal DynamoDB tables, private evidence S3 bucket, Standard workflow, custom AgentCore Browser, AgentCore Runtime container, scoped IAM roles, CloudWatch logs, Amplify app/branch, and an Amazon Location map with an expiring map-only referrer-restricted API key. No VPC or NAT gateway is created.

## Frontend configuration and publishing

Run the following application commands from the repository root. Set build-time Vite settings using `apps/web/.env.example`; the example region is `us-west-2`; use the actual deployed region. AWS settings are never a substitute for deployed verification:

- `VITE_API_BASE_URL` = `ApiUrl` output, without an added `/api` suffix; the client appends API paths.
- `VITE_AWS_REGION` = deployed region; `VITE_COGNITO_CLIENT_ID` = `UserPoolClientId`; `VITE_COGNITO_DOMAIN` = `CognitoDomain`; `VITE_COGNITO_REDIRECT_URI` = `WebUrl` plus `/`.
- `VITE_LOCATION_MAP_NAME` = `LocationMapName`; `VITE_LOCATION_API_KEY` = the restricted public map API key's value. This key is designed for browser use and is restricted to map rendering, one map ARN, the configured referrer, and an explicit expiry. It is not an AWS access key. Retrieve its value using the authenticated Location console or `DescribeKey` into a local ignored environment file; do not paste it into source, chat, or logs.
- The frontend determines local/AWS mode from `/api/health`, not a build-time mode flag. Keep that health route reachable without authentication so the Cognito sign-in screen can load; all private API routes remain JWT protected.

After loading the restricted public key privately as `NF_LOCATION_API_KEY`, build a deployment ZIP directly from the stack outputs:

```sh
python3 scripts/build_amplify_artifact.py \
  --outputs .local/aws-outputs.json --stack NeighborhoodFixer --install
```

The script validates deployed origins, sets Vite build values and produces `.local/amplify/frontend.zip` with a root `index.html` plus a configuration manifest that excludes the map-key value. It makes no cloud writes. Upload that ZIP to the created Amplify app's `main` branch using Amplify manual deployment as part of the authorized application deployment. The repository includes an Amplify build specification and branch environment settings in CDK for a future owner-authorized repository connection. Manual deployment uploads already-built files, so the local build must receive these values too; branch settings do not rewrite an uploaded bundle. No repository token is stored in CDK. Verify the registered root callback URL, deep-link refreshes, API CORS, map tiles and login/logout on the actual deployed origin.

## Fictional agency status changes in AWS

Cloud ticket status management is **disabled by default**. To enable the operator path for a reviewed AWS demo, update the stack with `--parameters DemoStatusManagementEnabled=true`, preserving any configured custom-origin override. This maps only the portal Lambda to `NF_ENABLE_DEMO_STATUS_MANAGEMENT=true`. The endpoint also requires `NF_MODE=aws`, `NF_ENVIRONMENT=demo`, and the portal service secret; production still rejects it. The browser-facing app's identity switching, reset, virtual-clock and local scenario endpoints remain disabled in AWS.

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

The Lambda worker has a five-minute invocation timeout. `WorkerReservedConcurrency` defaults to `0`, which this template translates to no reservation; it does not set Lambda's zero-concurrency throttle. Set the parameter to a reviewed positive value such as `3` only if the account quota permits it. API and portal concurrency are unreserved. AWS requires keeping 100 account concurrency units unreserved, so fixed reservations can prevent deployment in accounts with low quotas. [Lambda reserved concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html). Each model phase has at most 8 model calls, 16 tool calls, and 120 seconds; the tool budget cancels excess calls and the graph enforces a deadline. Bedrock transport retries are capped at two total attempts. The browser session has a maximum 300-second lifetime, and the form executor has its own shorter deadline. Safe status reads have bounded retries and 12 configured checks; ambiguous external writes are never automatically retried.

These are operational limits, not a hard spending cap. Cost budgets/alerts and an account owner-reviewed teardown date are recommended before running a public demo.

## Observability and recovery

`services/agents/observability.py` exports allowlisted OpenTelemetry operation spans as structured CloudWatch log records with trace ID, duration, operation type, outcome and tool count. It deliberately omits prompt text, tool arguments, images, private model reasoning and arbitrary exception content. The response contains actual provider usage only when the SDK returns it. No token costs are inferred. Strands verbose logs and AgentCore browser recording are disabled.

Standard workflow CloudWatch logs exclude execution data. Execution history still contains workflow inputs and callback task payloads and therefore requires restricted AWS operator access. Callback tokens stay in private application records and Lambda task payloads, never HTTP responses or browser state. The initial decision workflow waits for its exact revision, while approved submission runs as a separate persisted operation; the existing domain reservation remains the authority. Approval-before-registration is reconciled from the committed attempt/approval. Consumed tokens are removed. Expired or superseded callbacks cannot authorize a write.

If dispatch fails, the operation remains persisted with an actionable API error. After correcting configuration, a server administrator may call `start_operation(workspace_id, operation_id)` for that exact persisted operation. Duplicate StartExecution names do not create a second operation. Do not generate a new submission attempt to recover a lost receipt. Use the case's reconcile action, which performs receipt lookup only.

## Teardown and retained data

After explicit teardown approval, review `cdk diff` and run `npx cdk destroy NeighborhoodFixer`. Evidence, both DynamoDB tables, and the Cognito pool are intentionally **retained**. Runtime/Browser resources, workflow, hosting, and explicitly created application log groups are removed according to their resource policies. Runtime-created `/aws/bedrock-agentcore/runtimes/*` log groups are not explicit CDK resources here and may remain; inspect their retention and deletion separately. The separate `NeighborhoodFixerAssets` stack and its S3/ECR assets remain. Deleting that asset stack still retains the bucket and repository. If the optional standard bootstrap path was used, its assets and bootstrap resources may also remain. Inventory those resources in the authorized account and delete them only after the owner approves the exact data/retention consequences. Never infer that `cdk destroy` removed retained personal data or all continuing charges.
