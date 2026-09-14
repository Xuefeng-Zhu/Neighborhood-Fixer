"""Resolve generated server secrets in memory before importing the Lambda app."""

import os
import sys
import boto3


def main():
    for name in ("NF_PORTAL_SECRET", "NF_SESSION_SECRET"):
        arn = os.environ.get(name + "_ARN")
        if arn:
            os.environ[name] = boto3.client("secretsmanager").get_secret_value(
                SecretId=arn
            )["SecretString"]
    from awslambdaric.__main__ import main as runtime_main

    runtime_main([__file__, sys.argv[1]])


if __name__ == "__main__":
    main()
