# AWS deployment and Clerk cutover

The source now targets a Clerk development instance and one server-assigned Demo Borough workspace. This migration has local test evidence; Clerk deployment, signup, token renewal and post-reset acceptance must be verified on the live origin before public admission. Prior AWS results used the previous identity provider: two residents' real photo analyses through S3, Step Functions and AgentCore Runtime produced one shared case and an immutable draft. That historical result does not establish Clerk acceptance.

AgentCore Browser remains unresolved: custom and built-in sessions reported READY with automation ENABLED but automation-stream HTTP 404 before navigation. The initial Bedrock account-verification gate cleared at 07:31 UTC on 2026-09-14. It is not an established cause of the Browser failure. Approval, remote attachment transfer, receipt and closure remain unverified. See [current AWS status](AWS-STATUS.md). The receiving agency is fictional.

## Local preparation

Run from the repository root:

```sh
uv sync --all-extras
npm --prefix infra/cdk ci
npm --prefix infra/cdk run build
npm --prefix infra/cdk test
npm --prefix infra/cdk run synth
.venv/bin/python -m pytest tests/test_aws_admin.py tests/test_frontend_artifact.py tests/test_aws_journey_script.py -q
node --test tests/aws_frontend_support.test.mjs
```

Synthesis registers Docker assets without deploying them. Dependencies are pinned in the Python and independent infrastructure lockfiles. The ARM64 Lambda and Runtime images have previously built and passed local startup checks; rebuild them for changed backend source:

```sh
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.lambda -t neighborhood-fixer-lambda .
docker build --platform linux/arm64 -f infra/cdk/Dockerfile.runtime -t neighborhood-fixer-runtime .
```

Docker contexts allowlist application source, fixtures and build inputs; private environments, credentials, evidence, generated outputs and caches are excluded. Review current image findings separately; a successful build is not a vulnerability scan.

## Clerk and AWS configuration

Provision and claim the reviewed Clerk development instance before deployment. Keep public signup closed during validation. In Clerk Sessions → Customize session token, configure the ordinary session token with:

```json
{"aud":"neighborhood-fixer-api","scope":"nf:resident"}
```

The frontend uses the default session token and retains its session binding. API Gateway validates the exact issuer, audience and required scope. The backend independently checks the trusted claims, exact authorized frontend origin in `azp`, active version-2 session identity and expiry. A valid token still needs the configured workspace generation and admission policy. Client metadata cannot choose membership. [Clerk session customization](https://clerk.com/docs/guides/sessions/customize-session-tokens), [HTTP API JWT authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html).

Required CloudFormation parameters are `ClerkIssuerUrl` (exact HTTPS development issuer), `ClerkPublishableKey` (matching `pk_test_` public key), and `DataGeneration` (explicit deployment generation). `AuthAudience` is fixed to `neighborhood-fixer-api`; `SharedWorkspaceId` defaults to `demo-borough-v1`. No Clerk secret key is passed to Lambda, Runtime, Vite, or stack outputs.

Leave `FrontendOrigin` empty to derive `https://main.<Amplify DefaultDomain>`. For an already configured custom domain, set its exact HTTPS origin without a trailing slash. API CORS, backend authorized parties and the map key referrer use this origin. Amplify branch settings supply the public Clerk key. Public health and explicit OPTIONS routes allow login bootstrapping; all private routes require JWT scope `nf:resident`. CORS permits Authorization, Content-Type and Idempotency-Key. [Amplify App](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-amplify-app.html), [HTTP API CORS](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html).

Default per-resident UTC-day limits are 10 new observations, 25 accepted uploads and 30 reasoning jobs. Atomic workspace UTC-day limits cap the shared demo at 100 new observations, 250 accepted uploads and 300 reasoning jobs across all identities. Failed model starts count, while idempotent retries and worker redelivery do not create another quota charge. Upload and observation requests require an Idempotency-Key, and decision requests are deduplicated by resident, observation revision and selected outcome. API Gateway throttles at 10 requests/second with burst 20. All six daily limits and both request-rate limits are bounded CloudFormation parameters. The workspace controls constrain disposable-account abuse, but they are application limits rather than an AWS account spending cap.

## Deploy the validation generation

Use only the explicitly authorized account and region; the verified service intersection is `us-west-2`. Nova 2 Lite uses the US inference profile `us.amazon.nova-2-lite-v1:0`; another model requires an IAM/capability review. The app-specific asset stack uses current CLI credentials without persistent administrator deployment roles. [CDK credential synthesizer](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.CliCredentialsStackSynthesizer.html).

```sh
export AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2
aws sts get-caller-identity --query Account --output text
export NF_DEPLOY_ACCOUNT=AUTHORIZED_ACCOUNT_ID
export NF_ASSET_BUCKET="nf-assets-${NF_DEPLOY_ACCOUNT}-${AWS_REGION}"
export NF_ASSET_REPOSITORY=neighborhood-fixer-assets
cd infra/cdk
npx cdk synth --quiet
# Required only when this app's asset stack is not already deployed:
aws cloudformation deploy --template-file cdk.out/NeighborhoodFixerAssets.template.json --stack-name NeighborhoodFixerAssets
npx cdk diff NeighborhoodFixer -c retainLegacyCognito=true
npx cdk deploy NeighborhoodFixer -c retainLegacyCognito=true \
  --parameters ClerkIssuerUrl="$NF_CLERK_ISSUER" \
  --parameters ClerkPublishableKey="$NF_CLERK_PUBLISHABLE_KEY" \
  --parameters DataGeneration=clerk-validation-v1 \
  --parameters MapKeyExpiry=REVIEWED_FUTURE_ISO_TIMESTAMP \
  --outputs-file ../../.local/aws-outputs.json
```

For this existing-stack migration only, `retainLegacyCognito=true` preserves the original pool/client/domain while the API switches to Clerk. It does not preserve a second API login path. The final source and deployment must remove this temporary helper and context option after successful Clerk validation. A fresh installation needs no legacy option. Keep both asset environment variables set together for subsequent synth/diff/deploy. An existing owner-approved standard CDK bootstrap is an optional alternative when both variables are omitted.

The stack contains three Lambda entrypoints, HTTP API, two private DynamoDB tables, versioned evidence S3, Standard workflow, AgentCore Runtime/Browser, scoped IAM, logs, Amplify and Amazon Location. Runtime uses IAM, not browser credentials. Worker and portal bootstrap fetch only their portal service secret into memory. No VPC/NAT is created. S3/ECR asset resources are retained, encrypted and private.

Initialize only the reviewed validation subjects using the administrator CLI. Omit `--apply` first to inspect the target; the API never initializes an AWS workspace itself:

```sh
.venv/bin/python scripts/manage_aws_demo.py initialize \
  --expected-account "$NF_DEPLOY_ACCOUNT" --region "$AWS_REGION" \
  --generation clerk-validation-v1 --subject CLERK_USER_A --subject CLERK_USER_B
# Add --apply after reviewing the target and subject list.
```

This creates one inert, clearly marked sample case and validation admission. The sample has no uploaded evidence, ticket, approval, job or subscription; it cannot be submitted or matched as a real neighbor report.

## Build, publish and validate

Public frontend settings are `VITE_CLERK_PUBLISHABLE_KEY`, `VITE_API_BASE_URL`, region and Amazon Location map configuration. The API base is the output origin without an extra /api suffix. Retrieve the restricted map-only key privately into `NF_LOCATION_API_KEY`; never print its value. Build from current stack outputs:

```sh
AWS_REGION=us-west-2 python3 scripts/build_amplify_artifact.py \
  --outputs .local/aws-outputs.json --stack NeighborhoodFixer --install
```

The builder checks the Clerk key/issuer match and creates `.local/amplify/frontend.zip` plus a configuration manifest excluding the map key. Publish the ZIP to the authorized Amplify branch. Manual deployment does not substitute branch environment settings into a prebuilt bundle. Keep secret keys and token files out of source control.

With a reviewed admitted Clerk account and credentials in private environment variables `NF_AWS_SMOKE_USERNAME` and `NF_AWS_SMOKE_PASSWORD`:

```sh
NF_RUN_AWS_BROWSER_SMOKE=1 npm run test:aws:web -- \
  --config .local/amplify/frontend.manifest.json
```

Run independently for both residents. The smoke checks real CORS including Idempotency-Key, sign-in, authenticated read-only API, map style/tiles/attribution, token renewal after 70 seconds, page refresh, logout and rejected local controls. It does not create reports. MFA, email verification and CAPTCHA remain owner-controlled. No failure DOM, screenshots, traces or raw exceptions are exported.

For a separately authorized backend journey, add `--session-output .local/aws-sessions/alex.json --keep-session-seconds 600` (use a different file/account for the second helper). The explicitly requested short-lived bearer token stays in an atomic mode-0600 file. The journey driver rereads it before each request and refuses expired tokens or changed user/workspace/origin. The keeper ends with sign-out; remove private exports after use. Ordinary frontend code does not copy tokens to browser storage.

Live cutover acceptance must include both accounts, wrong issuer/audience/origin/scope rejection, unadmitted-user rejection, private photo/draft ownership, same shared workspace, idempotent retries, quota exhaustion, token renewal/logout and the authorized bounded photo-to-shared-draft journey. Browser submission remains blocked until the independent stream issue is resolved. Do not call a historical Cognito journey proof a Clerk validation.

## Reviewed reset and public opening

This operation deletes all active records in this app's two tables and every evidence object version/delete marker/multipart upload. It must follow successful Clerk validation and explicit authorization for those exact data consequences. Deployment assets, application infrastructure and account audit logs are not reset.

1. Close admission with `admission --generation clerk-validation-v1 --admission maintenance --apply`; preview without --apply first.
2. Run `quiesce --generation clerk-validation-v1`, then the reviewed `--apply` to stop only this stack's running workflows and custom Browser sessions.
3. Deploy `DataGeneration=clerk-public-v1`, still retaining the old pool. Existing control remains on the old generation, so API and worker paths fail closed.
4. Create the private reset manifest:

```sh
.venv/bin/python scripts/manage_aws_demo.py plan-reset \
  --expected-account "$NF_DEPLOY_ACCOUNT" --region "$AWS_REGION" \
  --generation clerk-validation-v1 --next-generation clerk-public-v1 \
  --subject CLERK_USER_A --subject CLERK_USER_B \
  --manifest .local/clerk-reset.json
```

Every administrator command requires the account, region and generation flags shown above; `--stack-name` defaults to NeighborhoodFixer. The plan pins the stack ARN and owned resources, scans every DynamoDB page, enumerates all S3 versions/delete markers/multipart pages, and prints only counts plus its SHA256. Review that inventory. Wait the required 360-second drain window; plans expire after one hour.

```sh
.venv/bin/python scripts/manage_aws_demo.py apply-reset \
  --expected-account "$NF_DEPLOY_ACCOUNT" --region "$AWS_REGION" \
  --generation clerk-public-v1 --manifest .local/clerk-reset.json \
  --manifest-sha256 REVIEWED_MANIFEST_SHA256
# To apply the exact reviewed deletion, add --validated-clerk --apply.
```

Apply repeats account/resource/generation/admission/work checks and refuses new inventory entries. Any deletion error stops before initialization. Explicit `--resume` permits only a remaining subset of the same reviewed manifest, within its expiry; inspect failures rather than creating an automatic new plan.

The reset seeds the new generation in validation admission and verifies exactly one sample incident/observation, no job/ticket/approval/attempt/callback, empty portal data and no S3 versions/uploads. `verify-reset --generation clerk-public-v1` repeats this read-only check. It must pass before public admission.

After a fresh hosted read-only validation against the new generation, remove the temporary legacy helper/import/context/test from source and deploy the final Clerk-only template. The old pool is retained by CloudFormation. Delete only the exact pool captured in the reviewed manifest with `delete-legacy-cognito --generation clerk-public-v1 --manifest .local/clerk-reset.json --manifest-sha256 REVIEWED_MANIFEST_SHA256 --validated-clerk --apply`, plus the required account/region flags. The tool refuses a pool still managed by the current stack. Then retire this migration-only delete command/source.

Finally configure Clerk public signup, and run the reviewed `admission --generation clerk-public-v1 --admission public --validated-clerk --apply`. This command reruns the clean sample verification before opening the server policy. Verify a fresh signup joins the server-selected Demo Borough, sees the single sample, and remains subject to ownership checks and quotas.

Before deletion, rollback may restore the previous application, authorizer and frontend because the legacy pool/data remain. After data deletion or pool deletion, that rollback is no longer available. Generation fencing prevents old jobs from recreating active state; it does not erase AWS execution history, logs, DynamoDB point-in-time recovery or backups. Their retention/deletion needs a separate scoped inventory. Never equate this app reset with erasing all historical cloud data.

## Fictional portal operation and teardown

`DemoStatusManagementEnabled` defaults false. A reviewed demo may enable it for the portal only; the endpoint additionally requires AWS demo mode and the service secret. The administrator `scripts/set_demo_ticket_status.py` derives the exact portal and secret ARN from stack outputs, checks mode, previews without --confirm and sends one confirmed status update. Use --inspect after an ambiguous response. It never changes AWS time or enables browser identity switching/reset. Agency CLOSED still requires optional resident verification.

AWS images are capped at 4 MiB; combined submission attachments also cap at 4 MiB for Lambda's request envelope. Worker timeout is five minutes, model/tool calls and status checks are bounded, and ambiguous external writes are never automatically retried. WorkerReservedConcurrency=0 omits a reservation rather than setting a zero-concurrency throttle. Logs omit prompts, tool arguments, images and arbitrary exception text, while private workflow execution history remains restricted operator data.

After explicit teardown approval, review the current diff before destroying application resources. Evidence, both DynamoDB tables and asset S3/ECR are retained; inspect runtime-created log groups and historical recovery data separately. No retained resource is implicitly authorized for deletion by an infrastructure destroy.
