import * as cdk from 'aws-cdk-lib';
import { NeighborhoodFixerStack } from './stack';
import { NeighborhoodFixerAssetStack } from './asset-stack';
const app = new cdk.App();
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? 'us-west-2',
};
const bucketName = process.env.NF_ASSET_BUCKET;
const repositoryName = process.env.NF_ASSET_REPOSITORY;
if (Boolean(bucketName) !== Boolean(repositoryName)) {
  throw new Error(
    'Set both NF_ASSET_BUCKET and NF_ASSET_REPOSITORY, or neither',
  );
}
if (bucketName && repositoryName) {
  new NeighborhoodFixerAssetStack(app, 'NeighborhoodFixerAssets', {
    env,
    bucketName,
    repositoryName,
  });
}
new NeighborhoodFixerStack(app, 'NeighborhoodFixer', {
  env,
  retainLegacyCognito: app.node.tryGetContext('retainLegacyCognito') === 'true',
  synthesizer:
    bucketName && repositoryName
      ? new cdk.CliCredentialsStackSynthesizer({
          fileAssetsBucketName: bucketName,
          imageAssetsRepositoryName: repositoryName,
        })
      : undefined,
});
