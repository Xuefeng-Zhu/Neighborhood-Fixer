/** Temporary cutover support. Final synthesis omits every Cognito resource. */
import * as cdk from 'aws-cdk-lib';
import { aws_cognito as cognito } from 'aws-cdk-lib';

export function retainLegacyLogin(stack: cdk.Stack, frontendOrigin: string) {
  // Preserve original construct paths so validation does not replace old accounts.
  const pool = new cognito.UserPool(stack, 'Residents', {
    selfSignUpEnabled: false,
    signInAliases: { email: true },
    autoVerify: { email: true },
    passwordPolicy: {
      minLength: 12,
      requireDigits: true,
      requireLowercase: true,
      requireUppercase: true,
    },
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });
  pool.addClient('Web', {
    generateSecret: false,
    authFlows: { userSrp: true },
    oAuth: {
      flows: { authorizationCodeGrant: true },
      scopes: [
        cognito.OAuthScope.OPENID,
        cognito.OAuthScope.EMAIL,
        cognito.OAuthScope.PROFILE,
      ],
      callbackUrls: [cdk.Fn.join('', [frontendOrigin, '/'])],
      logoutUrls: [cdk.Fn.join('', [frontendOrigin, '/'])],
    },
    preventUserExistenceErrors: true,
  });
  pool.addDomain('HostedLogin', {
    cognitoDomain: {
      domainPrefix: cdk.Fn.join('-', [
        'neighborhood-fixer',
        stack.account,
        stack.region,
      ]),
    },
  });
  new cdk.CfnOutput(stack, 'LegacyUserPoolId', { value: pool.userPoolId });
}
