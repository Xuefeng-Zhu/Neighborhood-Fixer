# Provider API verification record

Checked **2026-09-13/14** against official documentation, pinned packages, and the deployed `NeighborhoodFixer` stack in `us-west-2`.

| Area | Implemented contract | Evidence |
|---|---|---|
| Clerk session | Ordinary session token with exact `aud=neighborhood-fixer-api` and `scope=nf:resident`; the frontend retains normal Clerk session binding and receives no secret key | [Clerk session customization](https://clerk.com/docs/guides/sessions/customize-session-tokens) |
| API authorization | HTTP API JWT authorizer validates exact issuer and audience and requires `nf:resident`; backend also checks trusted `azp`, subject/session binding, identity version, expiry, server admission, and server workspace | [API Gateway JWT authorization](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html) |
| Public signup controls | Clerk public signup with Smart CAPTCHA; allowlist restriction disabled after validation cleanup | [Clerk bot protection](https://clerk.com/docs/guides/secure/bot-protection), [instance restrictions](https://clerk.com/docs/reference/backend/instance/update-restrictions) |
| CORS | Public health and explicit OPTIONS route; exact Amplify origin; Authorization, Content-Type, and Idempotency-Key allowed | [HTTP API CORS](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html) |
| Strands structured output | Agent uses `structured_output_model`; service consumes `AgentResult.structured_output` and validates the result before domain use | [Strands structured output](https://strandsagents.com/docs/user-guide/concepts/agents/structured-output/) |
| Model and tool bounds | Before-model, before-tool, and after-tool hooks plus bounded graph nodes and deadlines | [Strands hooks](https://strandsagents.com/docs/user-guide/concepts/agents/hooks/) |
| Multimodal model | `us.amazon.nova-2-lite-v1:0` through US geographic inference in `us-west-2`, with image input and client-side tools | [Nova 2 Lite model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html), [Nova 2 guide](https://docs.aws.amazon.com/nova/latest/nova2-userguide/what-is-nova-2.html) |
| AgentCore Runtime | ARM64 Linux container on port 8080 with `BedrockAgentCoreApp`; IAM invocation and a fresh runtime session ID per request | [Runtime HTTP contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html), [Runtime invocation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html) |
| AgentCore Browser | Start bounded sessions, sign the automation stream, connect with Playwright CDP, and stop in `finally` | [Session management](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-managing-sessions.html), [Playwright quickstart](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-quickstart-playwright.html) |
| CDK resources | Native `CfnRuntime` and `CfnBrowserCustom` resources with scoped IAM; the final application template contains no Cognito resource | [CfnRuntime](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_bedrockagentcore.CfnRuntime.html), [CfnBrowserCustom](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_bedrockagentcore.CfnBrowserCustom.html) |
| Durable orchestration | Step Functions Standard with Lambda task-token callback and explicit bounded waits | [Workflow types](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html), [integration patterns](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html) |
| Atomic reservations | Strong reads and conditional DynamoDB transactions keyed by application operation IDs | [DynamoDB transactions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html) |
| Hosting | Amplify `DefaultDomain` determines the deployed origin; the prebuilt Vite artifact embeds reviewed public settings | [Amplify App](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-amplify-app.html), [Amplify Branch](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-amplify-branch.html) |
| Location | MapLibre with a map-only, map-scoped, referrer-restricted, expiring Amazon Location key; no reverse-geocoding endpoint is implemented | [Location API keys](https://docs.aws.amazon.com/location/latest/developerguide/using-apikeys.html) |
| Deployment assets | Optional CLI-credentials synthesizer and dedicated retained S3/ECR asset stack, without persistent administrator deployment roles | [CDK credential synthesizer](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.CliCredentialsStackSynthesizer.html) |

Pinned versions at verification: `strands-agents==1.55.1`, `bedrock-agentcore==1.23.0`, `boto3==1.43.93`, `aws-cdk-lib==2.269.0`, `aws-cdk==2.1141.0`, and `constructs==10.8.1`.

## Live contract evidence

The live frontend is [https://main.d12il66dljooo6.amplifyapp.com/](https://main.d12il66dljooo6.amplifyapp.com/) and the API is [https://09iviho9v3.execute-api.us-west-2.amazonaws.com](https://09iviho9v3.execute-api.us-west-2.amazonaws.com). Clerk issuer `https://working-turtle-1775.clerk.accounts.dev` supplies the exact audience and scope above. Hosted checks verified public signup, token claims, authenticated reads, renewal, refresh, logout, and rejection of local-only controls. During closed validation, an otherwise valid unadmitted user received 403; a tampered token received 401.

The Amazon Location check fetched the configured style and tiles, rendered a canvas, and displayed attribution. The key was verified as map-only and restricted to the exact Amplify referrer.

Two residents then exercised the real private-photo path through S3, Step Functions, AgentCore Runtime, and Bedrock/Strands. Authorized evidence reads and nearby-incident queries completed. The two observations linked to one canonical case, with one immutable draft. The neighboring resident could not read the owner's private photo or draft. Model-only questions remained non-blocking when the server already had every required fact.

The report quota reached 10/10. A new request returned 429, while replaying an accepted idempotency key returned its original 201 and did not change the count.

After the reviewed reset, generation `clerk-public-v1` contained one inert sample incident/observation and no QA identities, operations, tickets, approvals, portal records, or evidence objects. Public admission and fresh hosted signup passed. All disposable users were deleted; the Clerk instance ended with zero users, zero allowlist entries, and the allowlist restriction disabled. CloudFormation ended with zero Cognito resources, and the exact former pool was deleted.

Automated release verification passed 182 Python tests with 4 explicitly skipped live-AWS cases and 3 dependency warnings, 29 frontend tests, 2 local Playwright journeys against Neighborhood Fixer on port 5174, 11 hosted-helper tests, and 14 CDK assertions. Web typecheck/build and CDK build/synthesis passed. The Vite bundle-size advisory is non-blocking.

## Unverified provider path

AgentCore Browser remains blocked before navigation. Both custom and built-in sessions report READY with automation enabled, but signed CDP connections return HTTP 404. Browser start and stream-signing inputs were reviewed without finding a demonstrated IAM or credential mismatch. The fictional portal's HTTP 200 response establishes endpoint reachability only.

The live journey stopped before approval. No agency write, attachment transfer, receipt, status poll, closure, or resident verification occurred. See the [AWS status](AWS-STATUS.md) and AWS [Browser troubleshooting guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool-troubleshooting.html).

The default-disabled fictional status operator remains guarded by AWS demo mode, the portal service secret, an exact stack-derived destination, preview, and explicit confirmation. Its live status-to-verification path has not been exercised.
