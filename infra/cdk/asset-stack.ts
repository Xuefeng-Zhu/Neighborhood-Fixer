import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { aws_s3 as s3, aws_ecr as ecr } from 'aws-cdk-lib';

export interface NeighborhoodFixerAssetStackProps extends cdk.StackProps {
  bucketName: string;
  repositoryName: string;
}

/** Asset storage only; deployments use the operator's current credentials. */
export class NeighborhoodFixerAssetStack extends cdk.Stack {
  constructor(
    scope: Construct,
    id: string,
    props: NeighborhoodFixerAssetStackProps,
  ) {
    super(scope, id, {
      ...props,
      // This stack contains no assets; publish its small template directly
      // with the current credentials, without bootstrap-role references.
      synthesizer: new cdk.LegacyStackSynthesizer(),
    });
    const bucket = new s3.Bucket(this, 'Assets', {
      bucketName: props.bucketName,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(7),
          noncurrentVersionExpiration: cdk.Duration.days(30),
        },
      ],
    });
    const repository = new ecr.Repository(this, 'Images', {
      repositoryName: props.repositoryName,
      imageScanOnPush: true,
      imageTagMutability: ecr.TagMutability.IMMUTABLE,
      encryption: ecr.RepositoryEncryption.AES_256,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          description: 'Expire only untagged assets after seven days',
          tagStatus: ecr.TagStatus.UNTAGGED,
          maxImageAge: cdk.Duration.days(7),
        },
      ],
    });
    new cdk.CfnOutput(this, 'AssetBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'AssetRepositoryName', {
      value: repository.repositoryName,
    });
  }
}
