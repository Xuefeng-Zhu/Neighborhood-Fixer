"""Account-scoped demo administration. Every command is a preview unless --apply.

No HTTP reset endpoint exists. A reset requires a private inventory manifest,
its SHA256, closed admission, a newly deployed data generation, no running work,
and a 360-second drain window. Deletion is limited to this stack's two data tables
and versioned evidence bucket. Deployment assets and account audit logs are untouched.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_ITEMS = 100_000
DRAIN_SECONDS = 360
MAX_PLAN_AGE = 3600


class UnsafeReset(ValueError):
    """Only fixed, nonsecret messages are placed in this exception."""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def bounded(items):
    if len(items) > MAX_ITEMS:
        raise UnsafeReset(
            "Inventory exceeds the demo safety limit; review it manually."
        )
    return items


def table_keys(client, table):
    """Read keys only, with every strongly consistent scan page included."""
    items, cursor = [], None
    while True:
        args = {
            "TableName": table,
            "ConsistentRead": True,
            "ProjectionExpression": "#p,#s",
            "ExpressionAttributeNames": {"#p": "pk", "#s": "sk"},
        }
        if cursor:
            args["ExclusiveStartKey"] = cursor
        page = client.scan(**args)
        items.extend(page.get("Items", []))
        bounded(items)
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))


def object_versions(client, bucket):
    items, cursor = [], {}
    while True:
        page = client.list_object_versions(Bucket=bucket, **cursor)
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            items.append({"Key": item["Key"], "VersionId": item["VersionId"]})
        bounded(items)
        if not page.get("IsTruncated"):
            return sorted(items, key=lambda item: (item["Key"], item["VersionId"]))
        cursor = {
            "KeyMarker": page["NextKeyMarker"],
            "VersionIdMarker": page["NextVersionIdMarker"],
        }


def multipart_uploads(client, bucket):
    items, cursor = [], {}
    while True:
        page = client.list_multipart_uploads(Bucket=bucket, **cursor)
        items.extend(
            {"Key": item["Key"], "UploadId": item["UploadId"]}
            for item in page.get("Uploads", [])
        )
        bounded(items)
        if not page.get("IsTruncated"):
            return sorted(items, key=lambda item: (item["Key"], item["UploadId"]))
        cursor = {
            "KeyMarker": page["NextKeyMarker"],
            "UploadIdMarker": page["NextUploadIdMarker"],
        }


def paginated(client, method, result, **args):
    values, cursor = [], None
    while True:
        page = getattr(client, method)(
            **args, **({"nextToken": cursor} if cursor else {})
        )
        values.extend(page.get(result, []))
        bounded(values)
        cursor = page.get("nextToken")
        if not cursor:
            return values


@dataclass
class Target:
    account: str
    region: str
    stack_name: str
    stack_id: str
    outputs: dict
    resources: dict

    def binding(self):
        return {
            "account": self.account,
            "region": self.region,
            "stack_name": self.stack_name,
            "stack_id": self.stack_id,
            "records_table": self.outputs["RecordsTable"],
            "portal_table": self.outputs["PortalRecordsTable"],
            "evidence_bucket": self.outputs["EvidenceBucket"],
            "workspace_id": self.outputs["SharedWorkspaceId"],
            "issuer": self.outputs["ClerkIssuerUrl"],
            "workflow_arn": self.outputs["WorkflowArn"],
            "browser_id": self.outputs["BrowserId"],
        }


def resolve_target(clients, account, region, stack_name):
    if not re.fullmatch(r"\d{12}", account) or not re.fullmatch(
        r"[a-z]{2}(?:-[a-z]+)+-\d", region
    ):
        raise UnsafeReset("Explicit valid account and region are required.")
    identity = clients["sts"].get_caller_identity()
    if identity.get("Account") != account:
        raise UnsafeReset(
            "Credential account does not match the explicitly selected account."
        )
    stack = clients["cloudformation"].describe_stacks(StackName=stack_name)["Stacks"][0]
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise UnsafeReset("The stack must be in a completed deployment state.")
    arn = stack["StackId"].split(":", 5)
    if arn[2:5] != ["cloudformation", region, account]:
        raise UnsafeReset("Stack account or region does not match the reviewed target.")
    rows, cursor = [], None
    while True:
        page = clients["cloudformation"].list_stack_resources(
            StackName=stack_name, **({"NextToken": cursor} if cursor else {})
        )
        rows.extend(page["StackResourceSummaries"])
        cursor = page.get("NextToken")
        if not cursor:
            break
    resources = {
        row["PhysicalResourceId"]: row["ResourceType"]
        for row in rows
        if row.get("PhysicalResourceId")
    }
    outputs = {row["OutputKey"]: row["OutputValue"] for row in stack.get("Outputs", [])}
    for key, kind in {
        "RecordsTable": "AWS::DynamoDB::Table",
        "PortalRecordsTable": "AWS::DynamoDB::Table",
        "EvidenceBucket": "AWS::S3::Bucket",
        "WorkflowArn": "AWS::StepFunctions::StateMachine",
        "BrowserId": "AWS::BedrockAgentCore::BrowserCustom",
    }.items():
        if resources.get(outputs.get(key)) != kind:
            raise UnsafeReset(
                "A data output does not identify a resource owned by this stack."
            )
    for key in [
        "SharedWorkspaceId",
        "DataGeneration",
        "ClerkIssuerUrl",
        "AuthAudience",
        "WebUrl",
        "WorkflowArn",
        "BrowserId",
    ]:
        if not outputs.get(key):
            raise UnsafeReset("The stack is missing required Clerk cutover outputs.")
    if (
        not re.fullmatch(
            r"https://[A-Za-z0-9-]+\.clerk\.accounts\.dev", outputs["ClerkIssuerUrl"]
        )
        or outputs["AuthAudience"] != "neighborhood-fixer-api"
    ):
        raise UnsafeReset(
            "Only the reviewed Clerk development configuration is supported."
        )
    if outputs["RecordsTable"] == outputs["PortalRecordsTable"]:
        raise UnsafeReset("Application and portal tables must be distinct.")
    return Target(account, region, stack_name, stack["StackId"], outputs, resources)


def control(clients, target):
    workspace = target.outputs["SharedWorkspaceId"]
    item = (
        clients["dynamodb"]
        .get_item(
            TableName=target.outputs["RecordsTable"],
            Key={"pk": {"S": "W#" + workspace}, "sk": {"S": "workspace#" + workspace}},
            ConsistentRead=True,
        )
        .get("Item")
    )
    return json.loads(item["data"]["S"]) if item else None


def running_work(clients, target):
    executions = paginated(
        clients["stepfunctions"],
        "list_executions",
        "executions",
        stateMachineArn=target.outputs["WorkflowArn"],
        statusFilter="RUNNING",
    )
    sessions = paginated(
        clients["bedrock-agentcore"],
        "list_browser_sessions",
        "items",
        browserIdentifier=target.outputs["BrowserId"],
    )
    return executions, [s for s in sessions if s["status"] != "TERMINATED"]


def require_closed(clients, target, generation):
    current = control(clients, target)
    if (
        not current
        or current.get("generation") != generation
        or current.get("admission") != "maintenance"
    ):
        raise UnsafeReset(
            "The exact previous generation must be in maintenance before reset."
        )
    return current


def inventory(clients, target):
    return {
        "records": table_keys(clients["dynamodb"], target.outputs["RecordsTable"]),
        "portal": table_keys(clients["dynamodb"], target.outputs["PortalRecordsTable"]),
        "versions": object_versions(clients["s3"], target.outputs["EvidenceBucket"]),
        "multipart": multipart_uploads(clients["s3"], target.outputs["EvidenceBucket"]),
    }


def verify_seeded(clients, target):
    current = control(clients, target)
    if (
        not current
        or current.get("generation") != target.outputs["DataGeneration"]
        or current.get("admission") != "validation"
    ):
        raise UnsafeReset(
            "The seeded workspace must be in validation with the deployed generation."
        )
    state = inventory(clients, target)
    if state["portal"] or state["versions"] or state["multipart"]:
        raise UnsafeReset(
            "The fresh sample must not leave portal records or uploaded evidence."
        )
    kinds = {}
    for key in state["records"]:
        kind = key["sk"]["S"].split("#", 1)[0]
        kinds.setdefault(kind, []).append(key)
    if any(
        kinds.get(kind)
        for kind in ["job", "operation", "ticket", "approval", "attempt", "callback"]
    ):
        raise UnsafeReset(
            "Operational records remain after reset; public admission is refused."
        )
    for kind in ["incident", "observation"]:
        if len(kinds.get(kind, [])) != 1:
            raise UnsafeReset(
                "Reset must leave exactly one sample incident and one sample observation."
            )
        item = (
            clients["dynamodb"]
            .get_item(
                TableName=target.outputs["RecordsTable"],
                Key=kinds[kind][0],
                ConsistentRead=True,
            )
            .get("Item")
        )
        data = json.loads(item["data"]["S"]) if item else {}
        if (
            data.get("is_sample") is not True
            or data.get("workspace_id") != target.outputs["SharedWorkspaceId"]
            or data.get("ticket")
            or data.get("attempt_id")
        ):
            raise UnsafeReset(
                "The retained case must be a non-actionable sample without a receipt or approval."
            )
    return {
        "generation_matches": True,
        "admission": "validation",
        "sample_incidents": 1,
        "sample_observations": 1,
        "operational_records": 0,
        "uploaded_objects": 0,
    }


def make_plan(clients, target, generation, next_generation, subjects, now=None):
    from services.api.policy import Policy

    Policy._validate_admission("validation", subjects)
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", next_generation)
        or generation == next_generation
    ):
        raise UnsafeReset("Reset requires a distinct explicit next generation.")
    if target.outputs["DataGeneration"] != next_generation:
        raise UnsafeReset(
            "Deploy the next generation first so API and worker requests fail closed."
        )
    require_closed(clients, target, generation)
    if any(running_work(clients, target)):
        raise UnsafeReset(
            "Stop the stack's running workflows and browser sessions before planning a reset."
        )
    now = int(time.time() if now is None else now)
    legacy = target.outputs.get("LegacyUserPoolId")
    if legacy and target.resources.get(legacy) != "AWS::Cognito::UserPool":
        raise UnsafeReset("Legacy pool output does not belong to the selected stack.")
    return {
        "schema_version": 1,
        "kind": "neighborhood_fixer_reset",
        "target": target.binding(),
        "previous_generation": generation,
        "next_generation": next_generation,
        "allowed_subjects": sorted(set(subjects)),
        "created_at": now,
        "not_before": now + DRAIN_SECONDS,
        "expires_at": now + MAX_PLAN_AGE,
        "legacy_user_pool_id": legacy,
        "inventory": inventory(clients, target),
    }


def delete_table_keys(client, table, keys, sleep=time.sleep):
    for start in range(0, len(keys), 25):
        pending = [{"DeleteRequest": {"Key": key}} for key in keys[start : start + 25]]
        for attempt in range(6):
            if not pending:
                break
            response = client.batch_write_item(RequestItems={table: pending})
            pending = response.get("UnprocessedItems", {}).get(table, [])
            if pending:
                sleep(min(2**attempt, 8))
        if pending:
            raise UnsafeReset(
                "DynamoDB deletions remain unprocessed; admission stays closed."
            )


def apply_reset(
    clients, target, plan, expected_digest, now=None, seed=None, resume=False
):
    if (
        digest(plan) != expected_digest
        or plan.get("kind") != "neighborhood_fixer_reset"
        or plan.get("schema_version") != 1
    ):
        raise UnsafeReset("Reset manifest integrity check failed.")
    if (
        target.binding() != plan["target"]
        or target.outputs["DataGeneration"] != plan["next_generation"]
    ):
        raise UnsafeReset("Deployment no longer matches the reviewed reset manifest.")
    now = time.time() if now is None else now
    if (
        plan["not_before"] < plan["created_at"] + DRAIN_SECONDS
        or not plan["not_before"] <= now <= plan["expires_at"]
        or plan["expires_at"] > plan["created_at"] + MAX_PLAN_AGE
    ):
        raise UnsafeReset("Reset manifest is outside its drain or expiry window.")
    current = control(clients, target)
    if current is not None or not resume:
        require_closed(clients, target, plan["previous_generation"])
    if any(running_work(clients, target)):
        raise UnsafeReset("Running work appeared; reset is refused.")
    remaining = inventory(clients, target)
    if (not resume and remaining != plan["inventory"]) or any(
        not {digest(item) for item in items}.issubset(
            {digest(item) for item in plan["inventory"][kind]}
        )
        for kind, items in remaining.items()
    ):
        raise UnsafeReset(
            "Data changed after inventory; no unreviewed additions will be deleted."
        )
    bucket = target.outputs["EvidenceBucket"]
    for upload in remaining["multipart"]:
        clients["s3"].abort_multipart_upload(Bucket=bucket, **upload)
    versions = remaining["versions"]
    for start in range(0, len(versions), 1000):
        result = clients["s3"].delete_objects(
            Bucket=bucket,
            Delete={"Objects": versions[start : start + 1000], "Quiet": True},
        )
        if result.get("Errors"):
            raise UnsafeReset(
                "S3 reported incomplete deletion; admission stays closed."
            )
    delete_table_keys(
        clients["dynamodb"], target.outputs["PortalRecordsTable"], remaining["portal"]
    )
    delete_table_keys(
        clients["dynamodb"], target.outputs["RecordsTable"], remaining["records"]
    )
    if any(inventory(clients, target).values()):
        raise UnsafeReset("Residual application data remains; no sample was seeded.")
    (
        seed
        or (
            lambda: domain_for(target).initialize_shared_workspace(
                admission="validation", allowed_subjects=plan["allowed_subjects"]
            )
        )
    )()
    return {
        "applied": True,
        "data_deleted": True,
        "sample_seeded": True,
        "verification": verify_seeded(clients, target),
    }


def domain_for(target):
    from services.agents.aws_storage import DynamoStore
    from services.api.config import Settings
    from services.api.domain import Domain

    o = target.outputs
    settings = Settings(
        mode="aws",
        environment="demo",
        region=target.region,
        table_name=o["RecordsTable"],
        evidence_bucket=o["EvidenceBucket"],
        auth_provider="clerk",
        clerk_issuer=o["ClerkIssuerUrl"],
        auth_audience=o["AuthAudience"],
        authorized_parties=(o["WebUrl"],),
        shared_workspace_id=o["SharedWorkspaceId"],
        data_generation=o["DataGeneration"],
    )
    return Domain(
        settings=settings,
        store=DynamoStore(o["RecordsTable"], region_name=target.region),
    )


def private_path(value, must_exist=False):
    path = Path(value).absolute()
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT / ".local") or path != resolved:
        raise UnsafeReset(
            "Reset manifests must be regular private files under ignored .local without symlinks."
        )
    if must_exist and (not path.is_file() or path.stat().st_mode & 0o077):
        raise UnsafeReset(
            "Existing reset manifests must be regular files with mode 0600."
        )
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "inspect",
            "initialize",
            "admission",
            "quiesce",
            "plan-reset",
            "apply-reset",
            "verify-reset",
            "delete-legacy-cognito",
        ],
    )
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", default="NeighborhoodFixer")
    parser.add_argument("--generation", required=True)
    parser.add_argument("--next-generation")
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument(
        "--admission",
        choices=["validation", "maintenance", "public"],
        default="validation",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument(
        "--validated-clerk",
        action="store_true",
        help="Operator attests the required live Clerk validation passed",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply this exact reviewed operation; omitted means read-only preview",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume partial deletion of only a subset of the same reviewed manifest",
    )
    args = parser.parse_args(argv)
    try:
        import boto3
        from botocore.config import Config

        config = Config(
            connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 2}
        )
        clients = {
            name: boto3.client(name, region_name=args.region, config=config)
            for name in [
                "sts",
                "cloudformation",
                "dynamodb",
                "s3",
                "stepfunctions",
                "bedrock-agentcore",
                "cognito-idp",
            ]
        }
        target = resolve_target(
            clients, args.expected_account, args.region, args.stack_name
        )
        result = {"command": args.command, "applied": False}
        if args.command == "plan-reset":
            if not args.manifest or not args.next_generation:
                raise UnsafeReset("Planning requires --manifest and --next-generation.")
            plan = make_plan(
                clients, target, args.generation, args.next_generation, args.subject
            )
            path = private_path(args.manifest)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as f:
                json.dump(plan, f, indent=2)
            result.update(
                manifest_sha256=digest(plan),
                counts={k: len(v) for k, v in plan["inventory"].items()},
                drain_seconds=DRAIN_SECONDS,
            )
        elif args.command in ("apply-reset", "delete-legacy-cognito"):
            if not args.manifest or not args.manifest_sha256:
                raise UnsafeReset(
                    "The reviewed private manifest and SHA256 are required."
                )
            plan = json.loads(private_path(args.manifest, True).read_text())
            if (
                digest(plan) != args.manifest_sha256
                or plan.get("target") != target.binding()
                or args.generation != plan.get("next_generation")
            ):
                raise UnsafeReset(
                    "The manifest does not match this target and generation."
                )
            if args.apply and not args.validated_clerk:
                raise UnsafeReset(
                    "Complete Clerk validation and explicitly attest --validated-clerk before deletion."
                )
            if args.command == "apply-reset":
                if args.apply:
                    result = apply_reset(
                        clients, target, plan, args.manifest_sha256, resume=args.resume
                    )
                else:
                    result.update(
                        counts={k: len(v) for k, v in plan["inventory"].items()}
                    )
            else:
                pool = plan.get("legacy_user_pool_id")
                if not pool or not pool.startswith(args.region + "_"):
                    raise UnsafeReset(
                        "The manifest has no region-matching legacy pool."
                    )
                if pool in target.resources:
                    raise UnsafeReset(
                        "Remove legacy Cognito constructs in the final deployment before deleting the retained pool."
                    )
                current = control(clients, target)
                if not current or current.get("generation") != args.generation:
                    raise UnsafeReset("The fresh Clerk workspace is not initialized.")
                clients["cognito-idp"].describe_user_pool(UserPoolId=pool)
                if args.apply:
                    clients["cognito-idp"].delete_user_pool(UserPoolId=pool)
                    result["applied"] = True
        else:
            if target.outputs["DataGeneration"] != args.generation:
                raise UnsafeReset("The generation does not match the deployed stack.")
            current = control(clients, target)
            result.update(
                admission=(current or {}).get("admission"), initialized=bool(current)
            )
            if args.command == "initialize":
                from services.api.policy import Policy

                Policy._validate_admission("validation", args.subject)
                if args.apply:
                    domain_for(target).initialize_shared_workspace(
                        admission="validation", allowed_subjects=args.subject
                    )
            elif args.command == "verify-reset":
                result.update(verify_seeded(clients, target))
            elif args.command == "admission":
                from services.api.policy import Policy

                Policy._validate_admission(args.admission, args.subject)
                if (
                    args.admission == "public"
                    and args.apply
                    and not args.validated_clerk
                ):
                    raise UnsafeReset(
                        "Validate the reset Clerk application before opening public admission."
                    )
                if args.apply:
                    if args.admission == "public":
                        verify_seeded(clients, target)
                    domain_for(target).set_admission(
                        args.admission, allowed_subjects=args.subject
                    )
            elif args.command == "quiesce":
                require_closed(clients, target, args.generation)
                executions, sessions = running_work(clients, target)
                result.update(
                    running_executions=len(executions), running_sessions=len(sessions)
                )
                if args.apply:
                    for run in executions:
                        clients["stepfunctions"].stop_execution(
                            executionArn=run["executionArn"], error="ReviewedDemoReset"
                        )
                    for session in sessions:
                        clients["bedrock-agentcore"].stop_browser_session(
                            browserIdentifier=target.outputs["BrowserId"],
                            sessionId=session["sessionId"],
                        )
            result["applied"] = args.apply and args.command not in (
                "inspect",
                "verify-reset",
            )
        print(json.dumps(result))
        return 0
    except UnsafeReset as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - provider diagnostics must never expose secrets
        # AWS errors may include account IDs, token material or object keys.
        print(
            "Administrative operation failed ("
            + type(exc).__name__
            + "); no sensitive diagnostics were emitted.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
