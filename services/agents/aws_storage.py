"""DynamoDB implementation of the shared atomic record store.

A workspace revision guard prevents query phantoms and write skew. All writes
in a workspace serialize on this guard; it is a deliberate small-demo tradeoff,
not a claim of unlimited single-partition throughput. Normal reads use bounded
strongly consistent Query/GetItem operations, never Scan or an eventual lock.
"""

import json
import uuid
from contextlib import contextmanager
from copy import deepcopy


class ConcurrentUpdate(RuntimeError):
    pass


class DynamoStore:
    def __init__(self, table_name: str, region_name: str | None = None, client=None):
        if not table_name:
            raise ValueError("NF_DYNAMODB_TABLE is required in AWS mode")
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "dynamodb",
                region_name=region_name,
                config=Config(retries={"max_attempts": 2, "mode": "standard"}),
            )
        self.client, self.table_name = client, table_name

    @contextmanager
    def atomic(self, workspace_id):
        if not workspace_id or "#" in workspace_id:
            raise ValueError("Invalid workspace identifier")
        tx = DynamoTransaction(self, workspace_id)
        yield tx
        tx.commit()


class DynamoTransaction:
    def __init__(self, store, workspace_id):
        self.store, self.workspace_id = store, workspace_id
        self.pk = f"W#{workspace_id}"
        self.pending, self.cache = {}, {}
        self.guard_version = None

    def _key(self, sk):
        return {"pk": {"S": self.pk}, "sk": {"S": sk}}

    def _read(self, sk):
        return self.store.client.get_item(
            TableName=self.store.table_name, Key=self._key(sk), ConsistentRead=True
        ).get("Item")

    def _guard(self):
        if self.guard_version is None:
            item = self._read("!revision")
            self.guard_version = int(item["revision"]["N"]) if item else 0

    def get(self, kind, record_id):
        self._guard()
        sk = f"{kind}#{record_id}"
        if sk in self.pending:
            return deepcopy(self.pending[sk])
        if sk not in self.cache:
            item = self._read(sk)
            self.cache[sk] = json.loads(item["data"]["S"]) if item else None
        return deepcopy(self.cache[sk])

    def put(self, kind, record_id, data):
        self._guard()
        if data.get("workspace_id", self.workspace_id) != self.workspace_id:
            raise PermissionError("Cross-workspace record write rejected")
        if data.get("id", record_id) != record_id:
            raise ValueError("Record id mismatch")
        if len(json.dumps(data).encode()) > 350000:
            raise ValueError("Record too large; store evidence in S3")
        self.pending[f"{kind}#{record_id}"] = deepcopy(
            dict(data, id=record_id, workspace_id=self.workspace_id)
        )

    def delete(self, kind, record_id):
        self._guard()
        self.pending[f"{kind}#{record_id}"] = None

    def list(self, kind, limit=200, after=None):
        self._guard()
        limit = min(max(int(limit), 1), 500)
        params = {
            "TableName": self.store.table_name,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": {
                ":pk": {"S": self.pk},
                ":prefix": {"S": f"{kind}#"},
            },
            "ConsistentRead": True,
            "Limit": limit,
        }
        if after:
            params["ExclusiveStartKey"] = self._key(f"{kind}#{after}")
        response = self.store.client.query(**params)
        values = {
            item["sk"]["S"]: json.loads(item["data"]["S"])
            for item in response.get("Items", [])
        }
        for sk, value in self.pending.items():
            if sk.startswith(f"{kind}#") and (not after or sk > f"{kind}#{after}"):
                if value is None:
                    values.pop(sk, None)
                else:
                    values[sk] = value
        return deepcopy([v for _, v in sorted(values.items())][:limit])

    def commit(self):
        if not self.pending:
            return
        if len(self.pending) > 99:
            raise ValueError(
                "Atomic command exceeds 99 records; use bounded domain commands"
            )
        version = self.guard_version or 0
        guard = {
            "TableName": self.store.table_name,
            "Key": self._key("!revision"),
            "UpdateExpression": "SET revision = :next",
            "ConditionExpression": "attribute_not_exists(revision)"
            if version == 0
            else "revision = :previous",
            "ExpressionAttributeValues": {":next": {"N": str(version + 1)}},
        }
        if version:
            guard["ExpressionAttributeValues"][":previous"] = {"N": str(version)}
        writes = [{"Update": guard}]
        for sk, data in self.pending.items():
            if data is None:
                writes.append(
                    {
                        "Delete": {
                            "TableName": self.store.table_name,
                            "Key": self._key(sk),
                        }
                    }
                )
            else:
                item = {
                    **self._key(sk),
                    "data": {"S": json.dumps(data, separators=(",", ":"))},
                }
                if sk.startswith("callback#") and isinstance(
                    data.get("expires_at"), int
                ):
                    item["expires_epoch"] = {"N": str(data["expires_at"])}
                if sk.startswith("contact_research#") and isinstance(
                    data.get("expires_epoch"), int
                ):
                    item["expires_epoch"] = {"N": str(data["expires_epoch"])}
                if sk.startswith("outreach_request#") and isinstance(
                    data.get("expires_epoch"), int
                ):
                    item["expires_epoch"] = {"N": str(data["expires_epoch"])}
                writes.append(
                    {"Put": {"TableName": self.store.table_name, "Item": item}}
                )
        try:
            self.store.client.transact_write_items(
                TransactItems=writes, ClientRequestToken=str(uuid.uuid4())
            )
        except Exception as exc:
            if (
                getattr(exc, "response", {}).get("Error", {}).get("Code")
                == "TransactionCanceledException"
            ):
                raise ConcurrentUpdate(
                    "Workspace changed; retry this domain command with fresh state"
                ) from exc
            raise


def download_evidence(bucket, key, expected_sha256):
    """Materialize approved image bytes in ephemeral private storage and verify hash."""
    import hashlib
    import os
    import tempfile
    from pathlib import Path

    import boto3

    if not bucket or not key or not expected_sha256:
        raise ValueError("Evidence reference and expected hash are required")
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"]
    try:
        data = body.read(5_000_001)
    finally:
        body.close()
    if len(data) > 5_000_000 or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError(
            "Evidence content no longer matches the approved attachment hash"
        )
    folder = Path(tempfile.mkdtemp(prefix="nf-evidence-"))
    os.chmod(folder, 0o700)
    destination = folder / "evidence.png"
    destination.write_bytes(data)
    os.chmod(destination, 0o600)
    return destination
