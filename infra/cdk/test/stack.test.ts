import { test } from 'node:test';
import assert from 'node:assert/strict';
import { App } from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { NeighborhoodFixerStack } from '../stack';
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
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    AuthorizationType: 'JWT',
    RouteKey: 'ANY /api/{proxy+}',
  });
  template.hasResourceProperties('AWS::Cognito::UserPool', {
    AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
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
  }
  template.resourceCountIs('AWS::EC2::VPC', 0);
});
test('Cognito callback and logout match and secret bootstrap grants stay aligned', () => {
  const clients = Object.values(
    template.findResources('AWS::Cognito::UserPoolClient'),
  ) as any[];
  assert.deepEqual(
    clients[0].Properties.CallbackURLs,
    clients[0].Properties.LogoutURLs,
  );
  const funcs = Object.values(
    template.findResources('AWS::Lambda::Function'),
  ) as any[];
  const api = funcs.find(
    (f) => f.Properties.ImageConfig.Command[0] === 'services.api.main.handler',
  );
  assert.equal(api.Properties.Environment.Variables.NF_PORTAL_SECRET_ARN, '');
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
