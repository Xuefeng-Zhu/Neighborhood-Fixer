import * as cdk from 'aws-cdk-lib';
import { NeighborhoodFixerStack } from './stack';
const app = new cdk.App();
new NeighborhoodFixerStack(app, 'NeighborhoodFixer', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'us-west-2',
  },
});
