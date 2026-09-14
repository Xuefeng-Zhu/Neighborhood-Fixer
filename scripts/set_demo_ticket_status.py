"""Explicit operator action for the deployed fictional portal; never a virtual clock.

The trusted stack supplies both destination and secret ARN. Secrets never appear
in command arguments, output, or exception text. Status writes are not retried.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from urllib.parse import urlsplit


class OperatorError(RuntimeError):
    """A sanitized operational error safe to display without secret values."""


def _portal_origin(value: str, region: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(f".lambda-url.{region}.on.aws")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise OperatorError(
            "The stack must expose its configured HTTPS fictional portal Lambda URL."
        )
    return f"https://{parsed.netloc}"


def operate(
    *,
    stack_name: str,
    region: str,
    receipt_id: str,
    status: str | None = None,
    closure_note: str = "",
    confirm: bool = False,
    inspect: bool = False,
    cloudformation=None,
    secrets_manager=None,
    http_client=None,
) -> dict:
    if not re.fullmatch(r"DB-[A-F0-9]{12}", receipt_id):
        raise OperatorError(
            "Provide the exact fictional DB receipt ID shown on the case."
        )
    if not inspect and status not in {"OPEN", "IN_PROGRESS", "CLOSED"}:
        raise OperatorError("Choose OPEN, IN_PROGRESS, or CLOSED.")
    if len(closure_note) > 1000:
        raise OperatorError("Closure notes must be 1,000 characters or fewer.")
    if not region or not stack_name:
        raise OperatorError(
            "An explicit AWS region and deployed stack name are required."
        )
    if cloudformation is None:
        import boto3
        from botocore.config import Config

        cloudformation = boto3.client(
            "cloudformation",
            region_name=region,
            config=Config(
                connect_timeout=5, read_timeout=15, retries={"total_max_attempts": 2}
            ),
        )
    try:
        response = cloudformation.describe_stacks(StackName=stack_name)
        stacks = response.get("Stacks", [])
        if len(stacks) != 1:
            raise OperatorError("The requested stack could not be resolved uniquely.")
        outputs = {
            item["OutputKey"]: item["OutputValue"]
            for item in stacks[0].get("Outputs", [])
        }
        portal = _portal_origin(outputs.get("PortalUrl", ""), region)
        secret_arn = outputs.get("PortalSecretArn", "")
        if not secret_arn.startswith(f"arn:aws:secretsmanager:{region}:"):
            raise OperatorError(
                "The stack is missing its configured regional portal secret ARN."
            )
    except OperatorError:
        raise
    except Exception:
        raise OperatorError(
            "Unable to read the deployed stack outputs. Check AWS credentials and CloudFormation permissions."
        ) from None
    if not confirm and not inspect:
        return {
            "action": "preview",
            "stack": stack_name,
            "portal": portal,
            "receipt_id": receipt_id,
            "requested_status": status,
            "closure_note": closure_note,
            "write_performed": False,
            "next_step": "Review these fields, then rerun with --confirm to change the fictional ticket status.",
        }

    if http_client is None:
        import httpx

        with httpx.Client(timeout=15, follow_redirects=False) as client:
            return _execute(
                client,
                secrets_manager,
                secret_arn,
                region,
                portal,
                receipt_id,
                status,
                closure_note,
                inspect,
            )
    return _execute(
        http_client,
        secrets_manager,
        secret_arn,
        region,
        portal,
        receipt_id,
        status,
        closure_note,
        inspect,
    )


def _execute(
    http_client,
    secrets_manager,
    secret_arn,
    region,
    portal,
    receipt_id,
    status,
    note,
    inspect,
):
    try:
        response = http_client.get(portal + "/health")
        if response.status_code != 200:
            raise OperatorError(
                "The configured fictional portal is unavailable; no secret was sent."
            )
        health = response.json()
        if (
            health.get("destination") != "fictional"
            or health.get("mode") != "aws"
            or health.get("environment") != "demo"
        ):
            raise OperatorError(
                "The destination is not an AWS fictional demo portal; no secret was sent."
            )
        if not inspect and health.get("status_management_enabled") is not True:
            raise OperatorError(
                "AWS demo status management is disabled. Explicitly enable the stack parameter before making operator changes."
            )
    except OperatorError:
        raise
    except Exception:
        raise OperatorError(
            "Unable to verify the configured fictional portal; no secret was sent."
        ) from None
    if secrets_manager is None:
        import boto3
        from botocore.config import Config

        secrets_manager = boto3.client(
            "secretsmanager",
            region_name=region,
            config=Config(
                connect_timeout=5, read_timeout=15, retries={"total_max_attempts": 2}
            ),
        )
    try:
        secret = secrets_manager.get_secret_value(SecretId=secret_arn).get(
            "SecretString"
        )
        if not isinstance(secret, str) or not secret:
            raise ValueError("Unsupported secret format")
    except Exception:
        raise OperatorError(
            "Unable to read the portal service secret. Check permission to the stack's exact secret ARN."
        ) from None
    headers = {"x-portal-secret": secret}
    try:
        if inspect:
            response = http_client.get(
                portal + "/internal/tickets/" + receipt_id, headers=headers
            )
        else:
            # Exactly one status write. An ambiguous network failure is inspected
            # with --inspect rather than automatically repeated.
            response = http_client.post(
                portal + "/internal/tickets/" + receipt_id + "/status",
                headers=headers,
                json={"status": status, "closure_note": note},
            )
    except Exception:
        message = (
            "Unable to read the ticket status."
            if inspect
            else "Status update outcome is uncertain. Use --inspect to read the persisted ticket before considering another change."
        )
        raise OperatorError(message) from None
    finally:
        headers.clear()
        secret = None
    if response.status_code != 200:
        raise OperatorError(
            f"The fictional portal rejected the {'read' if inspect else 'status change'} (HTTP {response.status_code}). No automatic retry was made."
        )
    try:
        ticket = response.json()
        if ticket.get("receipt_id") != receipt_id:
            raise ValueError("Receipt mismatch")
        # Never print the service response's contacts, payload, or receipt token.
        return {
            "receipt_id": receipt_id,
            "agency_status": ticket["normalized_status"],
            "raw_status": ticket["raw_status"],
            "updated_at": ticket.get("updated_at"),
            "write_performed": not inspect,
            "physical_resolution_changed": False,
        }
    except Exception:
        raise OperatorError(
            "The portal response could not be verified. Inspect the ticket before repeating a status change."
        ) from None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Change an explicitly enabled AWS fictional portal ticket. No real agency integration."
    )
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--region", default=os.getenv("AWS_REGION"))
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--status", choices=("OPEN", "IN_PROGRESS", "CLOSED"))
    parser.add_argument("--closure-note", default="")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Explicitly authorize one fictional ticket status write",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Read the current ticket status without changing it",
    )
    args = parser.parse_args(argv)
    try:
        print(json.dumps(operate(**vars(args)), indent=2))
    except OperatorError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
