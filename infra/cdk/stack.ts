import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as path from 'node:path';
import {
  aws_dynamodb as ddb,
  aws_s3 as s3,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_logs as logs,
  aws_apigatewayv2 as apigw,
  aws_apigatewayv2_integrations as integrations,
  aws_apigatewayv2_authorizers as authorizers,
  aws_stepfunctions as sfn,
  aws_stepfunctions_tasks as tasks,
  aws_bedrockagentcore as agentcore,
  aws_ecr_assets as assets,
  aws_secretsmanager as secrets,
  aws_amplify as amplify,
  aws_location as location,
} from 'aws-cdk-lib';

export class NeighborhoodFixerStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const root = path.resolve(__dirname, '../..');
    const customFrontendOrigin = new cdk.CfnParameter(this, 'FrontendOrigin', {
      type: 'String',
      default: '',
      description:
        'Optional custom HTTPS origin without trailing slash; empty uses the generated Amplify main branch URL',
      allowedPattern: '^$|https://[a-zA-Z0-9.-]+',
    });
    // Keep the app independent of backend resources. Branch settings may refer
    // to them after its generated domain has supplied CORS and authorized-party URLs.
    const clerkIssuer = new cdk.CfnParameter(this, 'ClerkIssuerUrl', {
      type: 'String',
      allowedPattern: '^https://[a-zA-Z0-9-]+\\.clerk\\.accounts\\.dev$',
      description:
        'Exact Clerk development instance issuer, without trailing slash',
    });
    const clerkKey = new cdk.CfnParameter(this, 'ClerkPublishableKey', {
      type: 'String',
      allowedPattern: '^pk_test_[a-zA-Z0-9_-]+$',
      description:
        'Public Clerk development publishable key; never a secret key',
    });
    const audience = new cdk.CfnParameter(this, 'AuthAudience', {
      type: 'String',
      default: 'neighborhood-fixer-api',
      allowedValues: ['neighborhood-fixer-api'],
    });
    const sharedWorkspace = new cdk.CfnParameter(this, 'SharedWorkspaceId', {
      type: 'String',
      default: 'demo-borough-v1',
      allowedPattern: '^[a-zA-Z0-9_-]{1,64}$',
    });
    const generation = new cdk.CfnParameter(this, 'DataGeneration', {
      type: 'String',
      allowedPattern: '^[a-zA-Z0-9_-]{1,64}$',
      description:
        'Explicit data generation; changed only by the reviewed cutover procedure',
    });
    const quota = (name: string, value: number, maximum: number) =>
      new cdk.CfnParameter(this, name, {
        type: 'Number',
        default: value,
        minValue: 1,
        maxValue: maximum,
      });
    const reports = quota('ReportsPerDay', 10, 100);
    const uploads = quota('UploadsPerDay', 25, 250);
    const reasoning = quota('ReasoningJobsPerDay', 30, 300);
    const workspaceReports = quota('WorkspaceReportsPerDay', 100, 1000);
    const workspaceUploads = quota('WorkspaceUploadsPerDay', 250, 1000);
    const workspaceReasoning = quota('WorkspaceReasoningJobsPerDay', 300, 1000);
    const apiRate = quota('ApiRequestsPerSecond', 10, 100);
    const apiBurst = quota('ApiBurstLimit', 20, 200);
    const hosting = new amplify.CfnApp(this, 'Hosting', {
      name: 'Neighborhood Fixer',
      platform: 'WEB',
      buildSpec:
        'version: 1\nfrontend:\n  phases:\n    preBuild:\n      commands:\n        - npm ci\n    build:\n      commands:\n        - npm run build\n  artifacts:\n    baseDirectory: apps/web/dist\n    files:\n      - "**/*"\n  cache:\n    paths:\n      - node_modules/**/*\n',
      customRules: [
        { source: '/<*>', target: '/index.html', status: '404-200' },
      ],
      customHeaders: cdk.Fn.join('', [
        'customHeaders:\n  - pattern: "**/*"\n    headers:\n',
        '      - key: Strict-Transport-Security\n        value: "max-age=31536000; includeSubDomains"\n',
        '      - key: X-Content-Type-Options\n        value: "nosniff"\n',
        '      - key: Referrer-Policy\n        value: "strict-origin-when-cross-origin"\n',
        '      - key: X-Frame-Options\n        value: "DENY"\n',
        '      - key: Permissions-Policy\n        value: "camera=(), microphone=(), geolocation=(self)"\n',
        "      - key: Content-Security-Policy\n        value: \"default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; script-src 'self' ",
        clerkIssuer.valueAsString,
        " https://challenges.cloudflare.com; connect-src 'self' ",
        clerkIssuer.valueAsString,
        ` https://*.execute-api.${this.region}.amazonaws.com https://maps.geo.${this.region}.amazonaws.com https://challenges.cloudflare.com; img-src 'self' data: blob: https://img.clerk.com https://images.clerk.dev; style-src 'self' 'unsafe-inline'; font-src 'self' data:; worker-src 'self' blob:; frame-src `,
        clerkIssuer.valueAsString,
        " https://challenges.cloudflare.com; form-action 'self'; upgrade-insecure-requests\"\n",
      ]),
    });
    const useCustomFrontendOrigin = new cdk.CfnCondition(
      this,
      'UseCustomFrontendOrigin',
      {
        expression: cdk.Fn.conditionNot(
          cdk.Fn.conditionEquals(customFrontendOrigin.valueAsString, ''),
        ),
      },
    );
    const frontendOrigin = cdk.Fn.conditionIf(
      useCustomFrontendOrigin.logicalId,
      customFrontendOrigin.valueAsString,
      cdk.Fn.join('', ['https://main.', hosting.attrDefaultDomain]),
    ).toString();
    const workerConcurrency = new cdk.CfnParameter(
      this,
      'WorkerReservedConcurrency',
      {
        type: 'Number',
        default: 0,
        minValue: 0,
        maxValue: 10,
        description:
          'Optional worker reserved concurrency; 0 leaves it unreserved for accounts with low Lambda concurrency quotas',
      },
    );
    const reserveWorkerConcurrency = new cdk.CfnCondition(
      this,
      'ReserveWorkerConcurrency',
      {
        expression: cdk.Fn.conditionNot(
          cdk.Fn.conditionEquals(workerConcurrency.valueAsNumber, 0),
        ),
      },
    );
    const modelId = new cdk.CfnParameter(this, 'BedrockModelId', {
      type: 'String',
      default: 'us.amazon.nova-2-lite-v1:0',
    });
    const demoStatusManagement = new cdk.CfnParameter(
      this,
      'DemoStatusManagementEnabled',
      {
        type: 'String',
        default: 'false',
        allowedValues: ['true', 'false'],
        description:
          'Explicit opt-in for secret-authenticated fictional ticket status changes in AWS demo mode only',
      },
    );
    const mapKeyExpiry = new cdk.CfnParameter(this, 'MapKeyExpiry', {
      type: 'String',
      default: '2027-09-13T00:00:00Z',
      description:
        'Explicit expiration for the referrer-restricted map-only API key',
    });
    const evidence = new s3.Bucket(this, 'Evidence', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          id: 'expire-transfer-grants',
          prefix: 'transfer/',
          expiration: cdk.Duration.days(1),
        },
      ],
    });
    const records = new ddb.Table(this, 'Records', {
      timeToLiveAttribute: 'expires_epoch',
      partitionKey: { name: 'pk', type: ddb.AttributeType.STRING },
      sortKey: { name: 'sk', type: ddb.AttributeType.STRING },
      billingMode: ddb.BillingMode.PAY_PER_REQUEST,
      encryption: ddb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const portalRecords = new ddb.Table(this, 'PortalRecords', {
      partitionKey: { name: 'pk', type: ddb.AttributeType.STRING },
      sortKey: { name: 'sk', type: ddb.AttributeType.STRING },
      billingMode: ddb.BillingMode.PAY_PER_REQUEST,
      encryption: ddb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const secret = new secrets.Secret(this, 'PortalSecret', {
      generateSecretString: { passwordLength: 48, excludePunctuation: true },
    });
    const common = {
      NF_MODE: 'aws',
      NF_ENVIRONMENT: 'demo',
      NF_TABLE_NAME: records.tableName,
      NF_EVIDENCE_BUCKET: evidence.bucketName,
      NF_DATA_DIR: '/tmp/neighborhood-fixer',
      NF_ALLOWED_ORIGINS: frontendOrigin,
      NF_PORTAL_SECRET_ARN: secret.secretArn,
      NF_AUTH_PROVIDER: 'clerk',
      NF_CLERK_ISSUER: clerkIssuer.valueAsString,
      NF_AUTH_AUDIENCE: audience.valueAsString,
      NF_AUTHORIZED_PARTIES: frontendOrigin,
      NF_SHARED_WORKSPACE_ID: sharedWorkspace.valueAsString,
      NF_DATA_GENERATION: generation.valueAsString,
      NF_REPORTS_PER_DAY: reports.valueAsString,
      NF_UPLOADS_PER_DAY: uploads.valueAsString,
      NF_REASONING_JOBS_PER_DAY: reasoning.valueAsString,
      NF_WORKSPACE_REPORTS_PER_DAY: workspaceReports.valueAsString,
      NF_WORKSPACE_UPLOADS_PER_DAY: workspaceUploads.valueAsString,
      NF_WORKSPACE_REASONING_JOBS_PER_DAY: workspaceReasoning.valueAsString,
    };
    const log = (name: string) =>
      new logs.LogGroup(this, name, {
        retention: logs.RetentionDays.TWO_WEEKS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });
    const makeLambda = (
      name: string,
      handler: string,
      environment: Record<string, string>,
      timeout = 30,
    ) =>
      new lambda.DockerImageFunction(this, name, {
        code: lambda.DockerImageCode.fromImageAsset(root, {
          file: 'infra/cdk/Dockerfile.lambda',
          cmd: [handler],
          platform: assets.Platform.LINUX_ARM64,
          ignoreMode: cdk.IgnoreMode.DOCKER,
          exclude: [
            '.git',
            '.venv',
            'node_modules',
            'infra/cdk/node_modules',
            'infra/cdk/cdk.out',
            '.local',
            'apps/web/dist',
            '.env',
            '**/.env*',
            '.codex',
            '.agents',
            '**/node_modules',
            '**/cdk.out',
          ],
        }),
        architecture: lambda.Architecture.ARM_64,
        memorySize: 1024,
        timeout: cdk.Duration.seconds(timeout),
        environment,
        logGroup: log(name + 'Logs'),
        tracing: lambda.Tracing.ACTIVE,
      });
    const portal = makeLambda('Portal', 'services.portal.main.handler', {
      ...common,
      NF_PORTAL_TABLE: portalRecords.tableName,
      NF_ENABLE_DEMO_STATUS_MANAGEMENT: demoStatusManagement.valueAsString,
      NF_SECRET_BOOTSTRAP: '1',
    });
    portalRecords.grantReadWriteData(portal);
    evidence.grantPut(portal, 'portal/*');
    secret.grantRead(portal);
    const portalUrl = portal.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.BUFFERED,
    });
    const browserRole = new iam.Role(this, 'BrowserRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: {
            'aws:SourceArn': `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:*`,
          },
        },
      }),
    });
    const browser = new agentcore.CfnBrowserCustom(this, 'Browser', {
      name: 'neighborhood_fixer_browser',
      networkConfiguration: { networkMode: 'PUBLIC' },
      executionRoleArn: browserRole.roleArn,
      recordingConfig: { enabled: false },
      description:
        'Bounded fictional portal automation; no persistent recording of resident data',
    });
    browser.node.addDependency(browserRole);
    const runtimeRole = new iam.Role(this, 'RuntimeRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: {
            'aws:SourceArn': `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:*`,
          },
        },
      }),
    });
    const runtimeLogs = log('RuntimeLogs');
    runtimeLogs.grantWrite(runtimeRole);
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'logs:DescribeLogStreams',
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutLogEvents',
        ],
        resources: [
          `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        ],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'xray:PutTraceSegments',
          'xray:PutTelemetryRecords',
          'xray:GetSamplingRules',
          'xray:GetSamplingTargets',
        ],
        resources: ['*'],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['logs:DescribeLogGroups'],
        resources: [
          `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:*`,
        ],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['logs:PutResourcePolicy'],
        resources: [
          `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/neighborhood_fixer_agents-*`,
        ],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: {
          StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' },
        },
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:GetWorkloadAccessToken'],
        resources: [
          `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
          `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/neighborhood_fixer_agents-*`,
        ],
      }),
    );
    const foundation = `arn:${this.partition}:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0`;
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: [
          foundation,
          `arn:${this.partition}:bedrock:${this.region}:${this.account}:inference-profile/${modelId.valueAsString}`,
        ],
      }),
    );
    evidence.grantRead(runtimeRole);
    const runtimeImage = new assets.DockerImageAsset(this, 'RuntimeImage', {
      directory: root,
      file: 'infra/cdk/Dockerfile.runtime',
      platform: assets.Platform.LINUX_ARM64,
      ignoreMode: cdk.IgnoreMode.DOCKER,
      exclude: [
        '.git',
        '.venv',
        'node_modules',
        'infra/cdk/node_modules',
        'infra/cdk/cdk.out',
        '.local',
        'apps/web/dist',
        '.env',
        '**/.env*',
        '.codex',
        '.agents',
        '**/node_modules',
        '**/cdk.out',
      ],
    });
    runtimeImage.repository.grantPull(runtimeRole);
    const runtime = new agentcore.CfnRuntime(this, 'AgentRuntime', {
      agentRuntimeName: 'neighborhood_fixer_agents',
      agentRuntimeArtifact: {
        containerConfiguration: { containerUri: runtimeImage.imageUri },
      },
      networkConfiguration: { networkMode: 'PUBLIC' },
      roleArn: runtimeRole.roleArn,
      protocolConfiguration: 'HTTP',
      environmentVariables: {
        NF_MODE: 'aws',
        AWS_REGION: this.region,
        NF_BEDROCK_MODEL_ID: modelId.valueAsString,
        NF_EVIDENCE_BUCKET: evidence.bucketName,
        NF_AGENT_TIMEOUT_SECONDS: '120',
        NF_AGENT_MAX_TURNS: '8',
        NF_AGENT_MAX_TOOLS: '16',
        OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: 'false',
      },
      lifecycleConfiguration: {
        idleRuntimeSessionTimeout: 60,
        maxLifetime: 600,
      },
    });
    // AgentCore validates ECR access during creation. RoleArn orders the role
    // alone; include its policy resources so image validation cannot race them.
    runtime.node.addDependency(runtimeRole);
    const workflowName = 'NeighborhoodFixerWorkflow';
    const workflowArn = this.formatArn({
      service: 'states',
      resource: 'stateMachine',
      resourceName: workflowName,
      arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });
    const worker = makeLambda(
      'Worker',
      'services.agents.aws_workflow.handler',
      {
        ...common,
        NF_PORTAL_URL: portalUrl.url,
        NF_AGENTCORE_RUNTIME_ARN: runtime.attrAgentRuntimeArn,
        NF_AGENTCORE_BROWSER_ID: browser.attrBrowserId,
        NF_STATE_MACHINE_ARN: workflowArn,
        NF_BEDROCK_MODEL_ID: modelId.valueAsString,
        NF_WORKER_LEASE_SECONDS: '360',
        NF_STATUS_INTERVAL_SECONDS: '60',
        NF_MAX_STATUS_CHECKS: '12',
        NF_SECRET_BOOTSTRAP: '1',
      },
      300,
    );
    const workerResource = worker.node.defaultChild as lambda.CfnFunction;
    workerResource.addPropertyOverride(
      'ReservedConcurrentExecutions',
      cdk.Fn.conditionIf(
        reserveWorkerConcurrency.logicalId,
        workerConcurrency.valueAsNumber,
        cdk.Aws.NO_VALUE,
      ),
    );
    records.grantReadWriteData(worker);
    evidence.grantReadWrite(worker);
    secret.grantRead(worker);
    worker.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [
          runtime.attrAgentRuntimeArn,
          `${runtime.attrAgentRuntimeArn}/runtime-endpoint/*`,
        ],
      }),
    );
    worker.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock-agentcore:StartBrowserSession',
          'bedrock-agentcore:StopBrowserSession',
          'bedrock-agentcore:GetBrowserSession',
          'bedrock-agentcore:ConnectBrowserAutomationStream',
        ],
        resources: [browser.attrBrowserArn],
      }),
    );
    const finish = new sfn.Succeed(this, 'Finished');
    const fail = new sfn.Fail(this, 'NeedsReview', {
      cause:
        'Operation failed or exhausted bounded checks; application record remains available',
    });
    const run = new tasks.LambdaInvoke(this, 'RunDomainCommand', {
      lambdaFunction: worker,
      payload: sfn.TaskInput.fromObject({
        phase: 'run',
        'workspace_id.$': '$.workspace_id',
        'operation_id.$': '$.operation_id',
      }),
      payloadResponseOnly: true,
      resultPath: '$.result',
      retryOnServiceExceptions: false,
    });
    run.addRetry({
      errors: ['ConcurrentUpdate'],
      interval: cdk.Duration.seconds(1),
      maxAttempts: 3,
      backoffRate: 2,
    });
    run.addCatch(fail, { resultPath: sfn.JsonPath.DISCARD });
    const callback = new tasks.LambdaInvoke(
      this,
      'AwaitExactRevisionApproval',
      {
        lambdaFunction: worker,
        integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
        taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(1)),
        payload: sfn.TaskInput.fromObject({
          phase: 'register_approval',
          'workspace_id.$': '$.workspace_id',
          'operation_id.$': '$.operation_id',
          'incident_id.$': '$.result.incident_id',
          'draft_id.$': '$.result.draft_id',
          task_token: sfn.JsonPath.taskToken,
        }),
        resultPath: sfn.JsonPath.DISCARD,
        retryOnServiceExceptions: false,
      },
    );
    callback.addCatch(finish, { resultPath: sfn.JsonPath.DISCARD });
    callback.next(finish);
    const wait = new sfn.Wait(this, 'WaitForStatusDue', {
      time: sfn.WaitTime.secondsPath('$.result.wait_seconds'),
    });
    const next = new sfn.Pass(this, 'SelectNextPersistedJob', {
      parameters: {
        'workspace_id.$': '$.workspace_id',
        'operation_id.$': '$.result.next_operation_id',
      },
    });
    wait.next(next).next(run);
    const choose = new sfn.Choice(this, 'SelectDurablePhase');
    run.next(choose);
    choose
      .when(
        sfn.Condition.booleanEquals('$.result.await_approval', true),
        callback,
      )
      .when(sfn.Condition.booleanEquals('$.result.has_next_job', true), wait)
      .otherwise(finish);
    const workflow = new sfn.StateMachine(this, 'Workflow', {
      stateMachineName: workflowName,
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromChainable(run),
      timeout: cdk.Duration.days(2),
      logs: {
        destination: log('WorkflowLogs'),
        level: sfn.LogLevel.ERROR,
        includeExecutionData: false,
      },
      tracingEnabled: true,
    });
    // Callback APIs do not support resource-level IAM; token material stays in
    // private storage and is bound to the revision by domain authorization.
    worker.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['states:SendTaskSuccess', 'states:SendTaskFailure'],
        resources: ['*'],
      }),
    );
    worker.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['states:StartExecution'],
        resources: [workflowArn],
      }),
    );
    const api = makeLambda('Api', 'services.api.main.handler', {
      ...common,
      NF_PORTAL_SECRET_ARN: '',
      NF_PORTAL_URL: portalUrl.url,
      NF_STATE_MACHINE_ARN: workflow.stateMachineArn,
      NF_AGENTCORE_RUNTIME_ARN: runtime.attrAgentRuntimeArn,
      NF_AGENTCORE_BROWSER_ID: browser.attrBrowserId,
      NF_BEDROCK_MODEL_ID: modelId.valueAsString,
      NF_SECRET_BOOTSTRAP: '1',
    });
    records.grantReadWriteData(api);
    evidence.grantReadWrite(api);
    workflow.grantStartExecution(api);
    api.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['states:SendTaskSuccess', 'states:SendTaskFailure'],
        resources: ['*'],
      }),
    );
    const http = new apigw.HttpApi(this, 'HttpApi', {
      corsPreflight: {
        allowOrigins: [frontendOrigin],
        allowMethods: [
          apigw.CorsHttpMethod.GET,
          apigw.CorsHttpMethod.POST,
          apigw.CorsHttpMethod.PATCH,
          apigw.CorsHttpMethod.OPTIONS,
        ],
        allowHeaders: ['authorization', 'content-type', 'idempotency-key'],
        allowCredentials: true,
      },
    });
    const integration = new integrations.HttpLambdaIntegration('FastApi', api);
    const authorizer = new authorizers.HttpJwtAuthorizer(
      'ResidentJwt',
      clerkIssuer.valueAsString,
      {
        jwtAudience: [audience.valueAsString],
        identitySource: ['$request.header.Authorization'],
      },
    );
    http.addRoutes({
      path: '/api/{proxy+}',
      methods: [apigw.HttpMethod.ANY],
      integration,
      authorizer,
      authorizationScopes: ['nf:resident'],
    });
    // A method-specific route takes precedence over the authenticated ANY
    // route, allowing API Gateway's configured CORS preflight response.
    http.addRoutes({
      path: '/api/{proxy+}',
      methods: [apigw.HttpMethod.OPTIONS],
      integration,
      authorizer: new apigw.HttpNoneAuthorizer(),
    });
    http.addRoutes({
      path: '/api/health',
      methods: [apigw.HttpMethod.GET],
      integration,
    });
    const stage = http.defaultStage!.node.defaultChild as apigw.CfnStage;
    stage.defaultRouteSettings = {
      throttlingRateLimit: apiRate.valueAsNumber,
      throttlingBurstLimit: apiBurst.valueAsNumber,
    };
    const map = new location.CfnMap(this, 'NeighborhoodMap', {
      mapName: 'NeighborhoodFixer',
      configuration: { style: 'VectorEsriLightGrayCanvas' },
    });
    const key = new location.CfnAPIKey(this, 'MapKey', {
      keyName: 'NeighborhoodFixerMap',
      expireTime: mapKeyExpiry.valueAsString,
      restrictions: {
        allowActions: ['geo:GetMap*'],
        allowResources: [map.attrArn],
        allowReferers: [cdk.Fn.join('', [frontendOrigin, '/*'])],
      },
    });
    api.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['geo-places:ReverseGeocode'],
        resources: [
          `arn:${this.partition}:geo-places:${this.region}::provider/default`,
        ],
      }),
    );
    new amplify.CfnBranch(this, 'HostingBranch', {
      appId: hosting.attrAppId,
      branchName: 'main',
      enableAutoBuild: false,
      stage: 'DEVELOPMENT',
      environmentVariables: [
        { name: 'VITE_API_BASE_URL', value: http.apiEndpoint },
        { name: 'VITE_AWS_REGION', value: this.region },
        { name: 'VITE_CLERK_PUBLISHABLE_KEY', value: clerkKey.valueAsString },
        { name: 'VITE_LOCATION_MAP_NAME', value: map.ref },
      ],
    });
    for (const [name, value] of Object.entries({
      WebUrl: frontendOrigin,
      ApiUrl: http.apiEndpoint,
      PortalUrl: portalUrl.url,
      PortalSecretArn: secret.secretArn,
      PortalStatusManagementEnabled: demoStatusManagement.valueAsString,
      ClerkIssuerUrl: clerkIssuer.valueAsString,
      ClerkPublishableKey: clerkKey.valueAsString,
      AuthAudience: audience.valueAsString,
      SharedWorkspaceId: sharedWorkspace.valueAsString,
      DataGeneration: generation.valueAsString,
      EvidenceBucket: evidence.bucketName,
      RecordsTable: records.tableName,
      PortalRecordsTable: portalRecords.tableName,
      WorkflowArn: workflow.stateMachineArn,
      AgentRuntimeArn: runtime.attrAgentRuntimeArn,
      BrowserId: browser.attrBrowserId,
      AmplifyAppId: hosting.attrAppId,
      LocationMapName: map.ref,
      LocationKeyName: key.ref,
    }))
      new cdk.CfnOutput(this, name + 'Output', { value }).overrideLogicalId(
        name,
      );
  }
}
