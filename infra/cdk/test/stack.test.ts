import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { App, CliCredentialsStackSynthesizer } from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { NeighborhoodFixerStack } from '../stack';
import { NeighborhoodFixerAssetStack } from '../asset-stack';
const app = new App();
const stack = new NeighborhoodFixerStack(app, 'TestStack', {
  env: { account: '111122223333', region: 'us-west-2' },
});
const template = Template.fromStack(stack);
test('private retained storage and real agent resources', () => {
  template.resourceCountIs('AWS::DynamoDB::Table', 2);
  template.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: {
      BlockPublicAcls: true,
      BlockPublicPolicy: true,
      IgnorePublicAcls: true,
      RestrictPublicBuckets: true,
    },
  });
  template.resourceCountIs('AWS::BedrockAgentCore::Runtime', 1);
  template.resourceCountIs('AWS::BedrockAgentCore::BrowserCustom', 1);
  template.hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
    ProtocolConfiguration: 'HTTP',
    NetworkConfiguration: { NetworkMode: 'PUBLIC' },
  });
});
test('durable standard callback workflow has no automatic submit retry', () => {
  template.hasResourceProperties('AWS::StepFunctions::StateMachine', {
    StateMachineType: 'STANDARD',
    LoggingConfiguration: Match.objectLike({ IncludeExecutionData: false }),
  });
  const data = JSON.stringify(
    template.findResources('AWS::StepFunctions::StateMachine'),
  );
  assert.match(data, /waitForTaskToken/);
  assert.match(data, /WaitForStatusDue/);
  assert.doesNotMatch(data, /Lambda\.ServiceException/);
});
test('JWT authorizer protects application API', () => {
  template.hasResourceProperties('AWS::ApiGatewayV2::Authorizer', {
    AuthorizerType: 'JWT',
    JwtConfiguration: {
      Issuer: { Ref: 'ClerkIssuerUrl' },
      Audience: [{ Ref: 'AuthAudience' }],
    },
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    AuthorizationType: 'JWT',
    RouteKey: 'ANY /api/{proxy+}',
    AuthorizationScopes: ['nf:resident'],
  });
  for (const kind of ['UserPool', 'UserPoolClient', 'UserPoolDomain'])
    template.resourceCountIs(`AWS::Cognito::${kind}`, 0);
});
test('CORS preflight is unauthenticated while application requests remain JWT protected', () => {
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    AuthorizationType: 'NONE',
    AuthorizerId: Match.absent(),
    RouteKey: 'OPTIONS /api/{proxy+}',
    Target: Match.anyValue(),
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    AuthorizationType: 'JWT',
    AuthorizerId: Match.anyValue(),
    RouteKey: 'ANY /api/{proxy+}',
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Api', {
    CorsConfiguration: Match.objectLike({
      AllowHeaders: ['authorization', 'content-type', 'idempotency-key'],
      AllowMethods: ['GET', 'POST', 'PATCH', 'OPTIONS'],
      AllowOrigins: Match.anyValue(),
    }),
  });
});
test('map key and execution environments are restricted', () => {
  template.hasResourceProperties('AWS::Location::APIKey', {
    Restrictions: {
      AllowActions: ['geo:GetMap*'],
      AllowResources: Match.anyValue(),
      AllowReferers: Match.anyValue(),
    },
    ExpireTime: Match.anyValue(),
  });
  const functions = template.findResources('AWS::Lambda::Function');
  for (const fn of Object.values(functions) as any[]) {
    assert.equal(fn.Properties.Environment.Variables.NF_MODE, 'aws');
    assert.equal(fn.Properties.Environment.Variables.NF_ENVIRONMENT, 'demo');
    assert.equal(fn.Properties.Architectures[0], 'arm64');
    assert.equal(fn.Properties.Environment.Variables.AWS_REGION, undefined);
    assert.equal(
      fn.Properties.Environment.Variables.AWS_DEFAULT_REGION,
      undefined,
    );
  }
  template.resourceCountIs('AWS::EC2::VPC', 0);
});

test('AgentCore creation waits for execution roles and attached ECR permissions', () => {
  const policies = template.findResources('AWS::IAM::Policy');
  for (const [type, roleProperty] of [
    ['AWS::BedrockAgentCore::Runtime', 'RoleArn'],
    ['AWS::BedrockAgentCore::BrowserCustom', 'ExecutionRoleArn'],
  ]) {
    for (const resource of Object.values(template.findResources(type))) {
      const roleId = resource.Properties[roleProperty]['Fn::GetAtt'][0];
      const dependencies = resource.DependsOn || [];
      assert.ok(
        dependencies.includes(roleId),
        `${type} must wait for its role`,
      );
      for (const [policyId, policy] of Object.entries(policies)) {
        if (!policy.Properties.Roles?.some((role: any) => role.Ref === roleId))
          continue;
        assert.ok(
          dependencies.includes(policyId),
          `${type} must wait for ${policyId}`,
        );
      }
      if (type !== 'AWS::BedrockAgentCore::Runtime') continue;
      const statements = Object.values(policies)
        .filter((policy) =>
          policy.Properties.Roles?.some((role: any) => role.Ref === roleId),
        )
        .flatMap((policy) => policy.Properties.PolicyDocument.Statement);
      const allows = (action: string) =>
        statements.find(
          (statement: any) =>
            statement.Effect === 'Allow' &&
            [statement.Action].flat().includes(action),
        );
      assert.equal(allows('ecr:GetAuthorizationToken')?.Resource, '*');
      for (const action of [
        'ecr:BatchGetImage',
        'ecr:GetDownloadUrlForLayer',
      ]) {
        assert.match(JSON.stringify(allows(action)?.Resource), /:repository\//);
      }
    }
  }
});
test('secret bootstrap grants stay aligned and no Cognito configuration leaks into final API', () => {
  const funcs = Object.values(
    template.findResources('AWS::Lambda::Function'),
  ) as any[];
  const api = funcs.find(
    (f) => f.Properties.ImageConfig.Command[0] === 'services.api.main.handler',
  );
  assert.equal(api.Properties.Environment.Variables.NF_PORTAL_SECRET_ARN, '');
  assert.equal(
    api.Properties.Environment.Variables.NF_COGNITO_USER_POOL_ID,
    undefined,
  );
  assert.equal(
    api.Properties.Environment.Variables.NF_COGNITO_CLIENT_ID,
    undefined,
  );
  template.resourceCountIs('AWS::SecretsManager::Secret', 1);
  for (const f of funcs) {
    assert.equal(
      f.Properties.Environment.Variables.NF_SESSION_SECRET_ARN,
      undefined,
    );
  }
});

test('cloud ticket status controls require an explicit portal-only opt-in', () => {
  template.hasParameter('DemoStatusManagementEnabled', {
    Type: 'String',
    Default: 'false',
    AllowedValues: ['true', 'false'],
  });
  const functions = Object.values(
    template.findResources('AWS::Lambda::Function'),
  ) as any[];
  for (const fn of functions) {
    const settings = fn.Properties.Environment.Variables;
    if (
      fn.Properties.ImageConfig.Command[0] === 'services.portal.main.handler'
    ) {
      assert.deepEqual(settings.NF_ENABLE_DEMO_STATUS_MANAGEMENT, {
        Ref: 'DemoStatusManagementEnabled',
      });
    } else {
      assert.equal(settings.NF_ENABLE_DEMO_STATUS_MANAGEMENT, undefined);
    }
  }
  template.hasOutput('PortalSecretArn', { Value: Match.anyValue() });
});

test('generated Amplify origin bootstraps all consumers without a dependency cycle', () => {
  template.hasParameter('FrontendOrigin', { Type: 'String', Default: '' });
  const apps = template.findResources('AWS::Amplify::App');
  const appId = Object.keys(apps)[0];
  assert.equal(apps[appId].Properties.EnvironmentVariables, undefined);
  const origin = {
    'Fn::If': [
      'UseCustomFrontendOrigin',
      { Ref: 'FrontendOrigin' },
      {
        'Fn::Join': [
          '',
          ['https://main.', { 'Fn::GetAtt': [appId, 'DefaultDomain'] }],
        ],
      },
    ],
  };
  template.hasOutput('WebUrl', { Value: origin });
  template.hasResourceProperties('AWS::ApiGatewayV2::Api', {
    CorsConfiguration: Match.objectLike({ AllowOrigins: [origin] }),
  });
  template.hasResourceProperties('AWS::Amplify::Branch', {
    BranchName: 'main',
    EnvironmentVariables: Match.arrayWith([
      { Name: 'VITE_API_BASE_URL', Value: Match.anyValue() },
      {
        Name: 'VITE_CLERK_PUBLISHABLE_KEY',
        Value: { Ref: 'ClerkPublishableKey' },
      },
    ]),
  });
});

test('Clerk instance, quota and generation configuration is explicit and server controlled', () => {
  template.hasParameter('ClerkIssuerUrl', {
    Type: 'String',
    Default: Match.absent(),
  });
  template.hasParameter('ClerkPublishableKey', {
    Type: 'String',
    Default: Match.absent(),
  });
  template.hasParameter('DataGeneration', {
    Type: 'String',
    Default: Match.absent(),
  });
  for (const [name, value] of [
    ['ReportsPerDay', 10],
    ['UploadsPerDay', 25],
    ['ReasoningJobsPerDay', 30],
    ['WorkspaceReportsPerDay', 100],
    ['WorkspaceUploadsPerDay', 250],
    ['WorkspaceReasoningJobsPerDay', 300],
  ])
    template.hasParameter(name as string, { Default: value });
  template.hasResourceProperties('AWS::ApiGatewayV2::Stage', {
    DefaultRouteSettings: {
      ThrottlingRateLimit: { Ref: 'ApiRequestsPerSecond' },
      ThrottlingBurstLimit: { Ref: 'ApiBurstLimit' },
    },
  });
  const api = Object.values(
    template.findResources('AWS::Lambda::Function'),
  ).find(
    (r: any) =>
      r.Properties.ImageConfig.Command[0] === 'services.api.main.handler',
  )!;
  assert.deepEqual(api.Properties.Environment.Variables.NF_DATA_GENERATION, {
    Ref: 'DataGeneration',
  });
  assert.deepEqual(api.Properties.Environment.Variables.NF_CLERK_ISSUER, {
    Ref: 'ClerkIssuerUrl',
  });
  assert.deepEqual(
    api.Properties.Environment.Variables.NF_WORKSPACE_REPORTS_PER_DAY,
    { Ref: 'WorkspaceReportsPerDay' },
  );
  assert.deepEqual(
    api.Properties.Environment.Variables.NF_WORKSPACE_UPLOADS_PER_DAY,
    { Ref: 'WorkspaceUploadsPerDay' },
  );
  assert.deepEqual(
    api.Properties.Environment.Variables.NF_WORKSPACE_REASONING_JOBS_PER_DAY,
    { Ref: 'WorkspaceReasoningJobsPerDay' },
  );
  assert.equal(api.Properties.Environment.Variables.NF_AUTH_PROVIDER, 'clerk');
  for (const name of ['UserPoolId', 'UserPoolClientId', 'CognitoDomain'])
    assert.equal(template.toJSON().Outputs[name], undefined);
  for (const name of [
    'ClerkIssuerUrl',
    'ClerkPublishableKey',
    'AuthAudience',
    'SharedWorkspaceId',
    'DataGeneration',
  ])
    template.hasOutput(name, { Value: Match.anyValue() });
});

test('legacy login survives only an explicit migration synthesis and never authorizes the new API', () => {
  const migration = Template.fromStack(
    new NeighborhoodFixerStack(new App(), 'Migration', {
      env: { account: '111122223333', region: 'us-west-2' },
      retainLegacyCognito: true,
    }),
  );
  for (const kind of ['UserPool', 'UserPoolClient', 'UserPoolDomain'])
    migration.resourceCountIs(`AWS::Cognito::${kind}`, 1);
  migration.hasResourceProperties('AWS::ApiGatewayV2::Authorizer', {
    JwtConfiguration: {
      Issuer: { Ref: 'ClerkIssuerUrl' },
      Audience: [{ Ref: 'AuthAudience' }],
    },
  });
  const pool = Object.values(
    migration.findResources('AWS::Cognito::UserPool'),
  )[0];
  assert.equal(pool.DeletionPolicy, 'Retain');
});

test('hosted frontend headers permit Clerk and map execution with no unsafe script evaluation', () => {
  const app = Object.values(template.findResources('AWS::Amplify::App'))[0];
  const headers = JSON.stringify(app.Properties.CustomHeaders);
  for (const value of [
    'Strict-Transport-Security',
    'Content-Security-Policy',
    'X-Content-Type-Options',
    'Referrer-Policy',
    'frame-ancestors',
    'ClerkIssuerUrl',
    'challenges.cloudflare.com',
    'img.clerk.com',
    'strict-origin-when-cross-origin',
  ])
    assert.ok(headers.includes(value));
  assert.ok(!headers.includes('unsafe-eval'));
  assert.ok(!headers.includes('value: "no-referrer"'));
});

test('worker concurrency reservation is optional for accounts with low quotas', () => {
  template.hasParameter('WorkerReservedConcurrency', {
    Type: 'Number',
    Default: 0,
    MinValue: 0,
    MaxValue: 10,
  });
  const functions = Object.values(
    template.findResources('AWS::Lambda::Function'),
  ) as any[];
  for (const fn of functions) {
    if (
      fn.Properties.ImageConfig.Command[0] ===
      'services.agents.aws_workflow.handler'
    ) {
      assert.deepEqual(fn.Properties.ReservedConcurrentExecutions, {
        'Fn::If': [
          'ReserveWorkerConcurrency',
          { Ref: 'WorkerReservedConcurrency' },
          { Ref: 'AWS::NoValue' },
        ],
      });
    } else {
      assert.equal(fn.Properties.ReservedConcurrentExecutions, undefined);
    }
  }
});

test('application asset stack creates storage without persistent deployment roles', () => {
  const assetApp = new App();
  const assetStack = new NeighborhoodFixerAssetStack(assetApp, 'AssetTest', {
    env: { account: '111122223333', region: 'us-west-2' },
    bucketName: 'nf-assets-111122223333-us-west-2',
    repositoryName: 'neighborhood-fixer-assets',
  });
  const assets = Template.fromStack(assetStack);
  assets.resourceCountIs('AWS::IAM::Role', 0);
  assets.hasResourceProperties('AWS::S3::Bucket', {
    BucketName: 'nf-assets-111122223333-us-west-2',
    VersioningConfiguration: { Status: 'Enabled' },
    PublicAccessBlockConfiguration: {
      BlockPublicAcls: true,
      BlockPublicPolicy: true,
      IgnorePublicAcls: true,
      RestrictPublicBuckets: true,
    },
  });
  assets.hasResourceProperties('AWS::ECR::Repository', {
    ImageScanningConfiguration: { ScanOnPush: true },
    ImageTagMutability: 'IMMUTABLE',
    LifecyclePolicy: Match.anyValue(),
  });
  for (const resource of Object.values(
    assets.findResources('AWS::S3::Bucket'),
  ).concat(Object.values(assets.findResources('AWS::ECR::Repository'))))
    assert.equal(resource.DeletionPolicy, 'Retain');
  const artifact = assetApp.synth().getStackArtifact(assetStack.artifactId);
  assert.equal(artifact.assumeRoleArn, undefined);
  assert.equal(artifact.cloudFormationExecutionRoleArn, undefined);
  assert.equal(artifact.requiresBootstrapStackVersion, undefined);
});

test('current-credential synthesis has no bootstrap role assumption or SSM prerequisite', () => {
  const directApp = new App();
  const directStack = new NeighborhoodFixerStack(directApp, 'DirectTest', {
    env: { account: '111122223333', region: 'us-west-2' },
    synthesizer: new CliCredentialsStackSynthesizer({
      fileAssetsBucketName: 'nf-assets-111122223333-us-west-2',
      imageAssetsRepositoryName: 'neighborhood-fixer-assets',
    }),
  });
  const assembly = directApp.synth();
  const artifact = assembly.getStackArtifact(directStack.artifactId);
  assert.equal(artifact.assumeRoleArn, undefined);
  assert.equal(artifact.cloudFormationExecutionRoleArn, undefined);
  assert.equal(artifact.requiresBootstrapStackVersion, undefined);
  assert.equal(artifact.template.Parameters?.BootstrapVersion, undefined);
  const manifest = JSON.parse(
    fs.readFileSync(
      path.join(assembly.directory, 'DirectTest.assets.json'),
      'utf8',
    ),
  );
  for (const asset of Object.values(manifest.dockerImages) as any[]) {
    for (const destination of Object.values(asset.destinations) as any[]) {
      assert.equal(destination.assumeRoleArn, undefined);
    }
    const directory = path.join(assembly.directory, asset.source.directory);
    for (const filename of fs.readdirSync(directory, {
      recursive: true,
    }) as string[]) {
      if (!fs.statSync(path.join(directory, filename)).isFile()) continue;
      assert.ok(
        ['.dockerignore', 'pyproject.toml', 'uv.lock'].includes(filename) ||
          /^(services|fixtures|infra\/cdk)\//.test(filename),
        `Unexpected deployment asset file: ${filename}`,
      );
      assert.doesNotMatch(
        filename,
        /(^|\/)(\.env[^/]*|\.local|\.git|\.venv|__pycache__|node_modules|cdk\.out)(\/|$)/,
      );
    }
    for (const filename of [
      'pyproject.toml',
      'uv.lock',
      'infra/cdk/bootstrap.py',
      'services/api/main.py',
      'services/agents/runtime.py',
      'fixtures/agency-registry.json',
    ]) {
      assert.ok(
        fs.existsSync(path.join(directory, filename)),
        `Missing deployment source: ${filename}`,
      );
    }
  }
});
