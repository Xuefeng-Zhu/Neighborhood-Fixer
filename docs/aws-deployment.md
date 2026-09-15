# AWS deployment with Clerk

The deployed application uses Clerk for resident authentication and one server-assigned Demo Borough workspace. The current public deployment is:

- App: [https://main.d12il66dljooo6.amplifyapp.com/](https://main.d12il66dljooo6.amplifyapp.com/)
- API: [https://09iviho9v3.execute-api.us-west-2.amazonaws.com](https://09iviho9v3.execute-api.us-west-2.amazonaws.com)
- Stack: `NeighborhoodFixer` in `us-west-2`
- Data generation: `clerk-public-v1`
- Clerk issuer: `https://working-turtle-1775.clerk.accounts.dev`
- JWT audience and scope: `neighborhood-fixer-api`, `nf:resident`

The receiving agency is fictional. The cloud validation stopped before approval and made no agency submission. AgentCore Browser remains blocked by an automation-stream HTTP 404, so remote attachment transfer, receipt, status polling, and closure are not verified.

## Local release checks

Run from the repository root:

```sh
uv sync --all-extras
.venv/bin/python -m pytest -q
npm ci
npm run test:web
npm run test:aws:web:config
npm run typecheck
npm run build
# Keep npm run dev running and use the Neighborhood Fixer URL it prints.
# The verified run used port 5174 and the installed private Node browser cache:
NF_WEB_URL=http://127.0.0.1:5174 \
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/ms-playwright-js" \
  npm run test:e2e
npm --prefix infra/cdk ci
npm --prefix infra/cdk run test
npm --prefix infra/cdk run build
npm --prefix infra/cdk run synth
```

The verified release run passed 182 Python tests with 4 explicit live-AWS skips and 3 dependency warnings, 29 frontend tests, 2 local Playwright journeys against Neighborhood Fixer on port 5174, 11 hosted-helper tests, and 14 CDK assertions. Web typecheck/build and CDK build/synthesis passed. Two earlier Playwright setup attempts encountered an empty browser cache and an unrelated app on port 5173; neither was a product failure. The production build emits a non-blocking Vite large-chunk advisory for the MapLibre bundle.

Synthesis registers Docker assets without deploying them. Rebuild both ARM64 images when backend source changes:

```sh
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.lambda -t neighborhood-fixer-lambda .
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.runtime -t neighborhood-fixer-runtime .
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.audio -t neighborhood-fixer-audio .
```

Docker contexts allowlist source, fixtures, and build inputs. They exclude private environments, credentials, resident evidence, generated outputs, and caches. A successful image build is not a vulnerability scan.

## Clerk configuration

The current development instance uses email/password signup, email-code verification, a 15-character minimum password, and Smart CAPTCHA. Configure the ordinary Clerk session token under Sessions → Customize session token:

```json
{"aud":"neighborhood-fixer-api","scope":"nf:resident"}
```

API Gateway validates the exact issuer and audience and requires the route scope. The backend independently checks the trusted `azp` frontend origin, user/session binding, version, expiry, server admission, and server-selected workspace. Client metadata and headers cannot select membership. [Clerk session customization](https://clerk.com/docs/guides/sessions/customize-session-tokens), [API Gateway JWT authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html).

Required CloudFormation parameters are:

- `ClerkIssuerUrl`: exact HTTPS issuer
- `ClerkPublishableKey`: public key matching that issuer
- `DataGeneration`: explicit deployment generation
- `MapKeyExpiry`: reviewed future timestamp for the restricted Location key
- `ContactResearchEnabled`: leave `false` until the Brave subscription, storage rights, secret value, and live checks have been reviewed
- `OfficialDomainExceptions`: reviewed comma-separated exact HTTPS host exceptions; leave empty for `.gov`-only research

`AuthAudience` defaults to `neighborhood-fixer-api`, and `SharedWorkspaceId` defaults to `demo-borough-v1`. Do not pass a Clerk secret key to Lambda, Runtime, Vite, or stack outputs. The frontend needs only the publishable key.

Leave `FrontendOrigin` empty to derive `https://main.<Amplify DefaultDomain>`. For a custom domain, supply its exact HTTPS origin without a trailing slash. API CORS, backend authorized-party validation, the audio service's in-function Clerk checks, and the Location key referrer must use the same origin. Health and explicit OPTIONS routes are public; private main API routes require `nf:resident`. The audio REST API exposes only GET/OPTIONS under `/api/{proxy+}` and accepts only Authorization and X-NF-Playback-Token. Its Lambda verifies the Clerk signature, issuer, audience, expiry, authorized party, scope, session, workspace, and owner before Polly runs.

The current development Clerk instance has public signup enabled, its allowlist restriction disabled, zero allowlist entries, and zero retained users after QA cleanup. Use disposable testing identities for future smoke runs and delete them afterward. Create a Clerk production instance and an owned custom domain before treating this as a production resident service.

## AWS deployment

Use only the authorized account and `us-west-2`. Nova 2 Lite uses the US inference profile `us.amazon.nova-2-lite-v1:0`. The app-specific asset stack uses current CLI credentials and does not create persistent administrator deployment roles.

```sh
export AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2
aws sts get-caller-identity --query Account --output text
export NF_DEPLOY_ACCOUNT=AUTHORIZED_ACCOUNT_ID
export NF_ASSET_BUCKET="nf-assets-${NF_DEPLOY_ACCOUNT}-${AWS_REGION}"
export NF_ASSET_REPOSITORY=neighborhood-fixer-assets
cd infra/cdk
npx cdk synth --quiet

# Required only when this app's asset stack is absent:
aws cloudformation deploy \
  --template-file cdk.out/NeighborhoodFixerAssets.template.json \
  --stack-name NeighborhoodFixerAssets

npx cdk diff NeighborhoodFixer
npx cdk deploy NeighborhoodFixer \
  --parameters ClerkIssuerUrl="$NF_CLERK_ISSUER" \
  --parameters ClerkPublishableKey="$NF_CLERK_PUBLISHABLE_KEY" \
  --parameters DataGeneration=clerk-public-v1 \
  --parameters MapKeyExpiry=REVIEWED_FUTURE_ISO_TIMESTAMP \
  --parameters ContactResearchEnabled=false \
  --parameters OfficialDomainExceptions= \
  --outputs-file ../../.local/aws-outputs.json
```

The first deployment creates `BraveSearchSecret` without exposing a key through CloudFormation parameters, outputs, shell history, or frontend configuration. Put the reviewed Brave Web Search API key into that secret through an approved secret-input workflow, then redeploy with `ContactResearchEnabled=true`. Only the isolated contact-research worker role can read the secret. If the subscribed plan does not permit retaining the selected derived contact/source metadata, leave the feature disabled.

Keep both asset environment variables set together for synth, diff, and deploy. The application stack contains the main HTTP API, a separate response-streaming REST API, five Lambda entrypoints, two retained DynamoDB tables, one encrypted TTL-backed no-PITR outreach table, versioned evidence S3, a Standard workflow, AgentCore Runtime and Browser, scoped IAM, logs, Amplify, Amazon Location, and Polly synthesis permission. The transient table holds the 24-hour unselected-candidate cache and short-lived captions; only the selected contact/source snapshot enters retained case storage. The workflow waits until each run's recorded transcript-expiry timestamp and then actively deletes abandoned caption sessions with bounded retries; table TTL remains a 15-minute fallback. The final template contains no Cognito, email-delivery, or telephony resources. Runtime invokes AWS services with IAM; only the isolated contact-research worker can fetch the Brave key, the fictional portal can fetch only its own portal secret, and only the audio Lambda can invoke Polly. The audio Lambda has retained/transient DynamoDB access but no Brave, portal, evidence, AgentCore, browser, state-machine, or worker-invocation access.

## Build and publish the frontend

Public frontend settings are the Clerk publishable key, main API origin, dedicated audio REST API stage URL, AWS region, and Amazon Location map settings. Retrieve the map-only key privately; never print or commit it. `AudioApiUrl` must be present in the stack outputs; the builder injects it as `VITE_AUDIO_API_BASE_URL`. Build the deployable ZIP from current stack outputs:

```sh
cd ../..
AWS_REGION=us-west-2 python3 scripts/build_amplify_artifact.py \
  --outputs .local/aws-outputs.json \
  --stack NeighborhoodFixer \
  --install
```

The builder verifies that the Clerk key and issuer match and writes `.local/amplify/frontend.zip` plus a non-secret configuration manifest. Publish that exact ZIP to the authorized Amplify branch. Manual Amplify deployment does not inject branch environment variables into an already built artifact.

## Hosted acceptance

Use disposable Clerk testing identities stored only in private mode-0600 files or environment variables. Never save tokens, passwords, traces, or authenticated DOM in source control.

```sh
NF_RUN_AWS_BROWSER_SMOKE=1 npm run test:aws:web -- \
  --config .local/amplify/frontend.manifest.json
```

The hosted smoke checks exact CORS including Idempotency-Key, public signup/sign-in, token claims, authenticated read-only API, Amazon Location style/tiles/canvas/attribution, token renewal after 70 seconds, refresh, logout, and absence of local-only controls. Run it for two independent identities when validating shared behavior. Delete all disposable users afterward and confirm that no test allowlist entry remains.

A release acceptance run must also prove:

- wrong issuer, audience, authorized party, scope, and tampered token rejection;
- server-controlled admission and workspace membership;
- owner-only photos, analysis details, coordinates, and draft content;
- one shared case without a cross-resident evidence leak;
- idempotent replay without a second quota charge;
- report quota exhaustion at 10/10;
- a clean post-reset sample and a fresh public signup.

When contact research is enabled, additionally prove one owner-confirmed Seattle reverse geocode, one Brave request containing only the normalized jurisdiction and category, manual selection with no default, an internal email receipt marked `SIMULATED_NOT_SENT`, and a captioned Polly call marked `SIMULATED_NOT_DIALED`. Inspect the retained table, temporary table, S3, logs, and network calls afterward: no raw Brave response, audio, expired transcript, real email delivery, or dial attempt may remain.

AgentCore Browser readiness does not prove navigation. Require a successful CDP connection, page action, receipt, and downstream state before reporting remote submission as verified. The current deployment has not met this gate.

## Admission and data lifecycle

The API never initializes or resets an AWS workspace. `scripts/manage_aws_demo.py` requires the exact account, region, stack, and data generation. Every command previews by default; mutation additionally requires `--apply` and, where applicable, `--validated-clerk`.

Inspect the current public state:

```sh
.venv/bin/python scripts/manage_aws_demo.py inspect \
  --expected-account "$NF_DEPLOY_ACCOUNT" \
  --region "$AWS_REGION" \
  --generation clerk-public-v1
```

The verified public state contains exactly one inert sample incident and observation, no admitted QA identities, no workflow operations, no ticket/approval/attempt/callback, no portal record, and no evidence objects or uploads. Public admission is active.

A future reset is destructive: it deletes reviewed records from this app's two tables and every evidence object version, delete marker, and multipart upload. It does not delete deployment assets, application infrastructure, CloudWatch/AWS audit history, point-in-time recovery, or backups. Before any reset:

1. Move current admission to maintenance using a previewed `admission` command.
2. Preview and apply `quiesce` for this stack, then confirm zero running workflows and custom Browser sessions.
3. Deploy a new `DataGeneration` so old work fails generation fencing.
4. Create a private `plan-reset` manifest that pins the stack ARN, owned resources, exact inventory, and intended next generation.
5. Review the printed counts and SHA256, wait the required 360-second drain, and apply only that manifest before its one-hour expiry.
6. Use `--resume` only for a remaining subset of the same manifest after inspecting a partial deletion.
7. While admission remains `validation`, run the reset verifier against the new generation:

   ```sh
   .venv/bin/python scripts/manage_aws_demo.py verify-reset \
     --expected-account "$NF_DEPLOY_ACCOUNT" \
     --region "$AWS_REGION" \
     --generation NEXT_GENERATION
   ```

8. Run hosted read-only smoke and a fresh disposable signup before previewing and applying public admission. `verify-reset` intentionally refuses to run after admission is public; use `inspect` for later read-only snapshots.

Do not treat an application reset as erasure of all historical cloud data. Inventory and retention of logs, execution history, recovery points, and backups require a separate authorized operation.

## Quotas and operational limits

Default resident UTC-day limits are 10 new observations, 25 accepted uploads, and 30 reasoning jobs. Shared-workspace limits are 100 observations, 250 uploads, and 300 reasoning jobs. Failed model starts count. Idempotent retries and worker redelivery do not add another quota charge. API Gateway throttles at 10 requests per second with burst 20. These controls bound a demo workload; they are not an AWS account spending cap.

AWS images are capped at 4 MiB, and combined submission attachments also cap at 4 MiB for the Lambda envelope. Worker timeout is five minutes. Model/tool calls and status checks are bounded. An ambiguous external write is never automatically retried.

`DemoStatusManagementEnabled` defaults false. When explicitly enabled for a reviewed fictional demo, `scripts/set_demo_ticket_status.py` derives the portal and secret ARN from stack outputs, verifies mode, previews without `--confirm`, and performs one confirmed update. It cannot enable identity switching, reset, or virtual time.

## Teardown

Before an explicitly authorized teardown, inspect the current stack diff and retained resources. Evidence S3, both DynamoDB tables, and asset S3/ECR use retention policies. Runtime-created log groups, workflow history, and recovery data need their own inventory. Infrastructure destruction does not imply authorization to delete retained data.
