"""Provision a narrowly authorized Cognito demo workspace; dry-run by default.

This administration command has no public HTTP counterpart. Existing membership
in a different workspace is never silently replaced. It does not create users,
passwords, or a shared administrator login.
"""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.agents.aws_storage import DynamoStore

IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def provision(store, workspace_id, user_subs, apply=False):
    if not IDENTIFIER.fullmatch(workspace_id) or workspace_id == "auth":
        raise ValueError(
            "Use a nonreserved workspace identifier with letters, digits, hyphens or underscores"
        )
    if not user_subs or len(user_subs) > 20 or len(user_subs) != len(set(user_subs)):
        raise ValueError("Supply 1–20 unique existing Cognito subject identifiers")
    if any(not IDENTIFIER.fullmatch(sub) for sub in user_subs):
        raise ValueError("Invalid Cognito subject identifier")
    with store.atomic("auth") as tx:
        for sub in user_subs:
            existing = tx.get("membership", sub)
            if existing and (
                existing.get("target_workspace_id") != workspace_id
                or existing.get("disabled")
            ):
                raise ValueError(
                    "An existing membership requires separate administrative review before reassignment"
                )
        if apply:
            for sub in user_subs:
                tx.put(
                    "membership",
                    sub,
                    {
                        "id": sub,
                        "target_workspace_id": workspace_id,
                        "scope": "demo",
                        "disabled": False,
                        "provisioned_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
    return {
        "applied": apply,
        "resident_count": len(user_subs),
        "workspace_id": workspace_id,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--user-sub", action="append", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write reviewed memberships to the explicitly authorized AWS account/table",
    )
    args = parser.parse_args()
    table = os.environ.get("NF_TABLE_NAME")
    if not table:
        parser.error("Set NF_TABLE_NAME for the authorized environment")
    result = provision(
        DynamoStore(table, region_name=os.environ.get("AWS_REGION")),
        args.workspace,
        args.user_subs,
        args.apply,
    )
    print(
        ("Provisioned" if result["applied"] else "Validated dry run for")
        + f" {result['resident_count']} resident memberships; no user passwords or credentials were accessed."
    )


if __name__ == "__main__":
    main()
