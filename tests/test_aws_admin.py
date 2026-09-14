"""Offline tests of destructive boundaries using in-memory AWS adapters only."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.manage_aws_demo import (
    Target,
    UnsafeReset,
    apply_reset,
    delete_table_keys,
    digest,
    inventory,
    make_plan,
    private_path,
    resolve_target,
    table_keys,
    validate_manifest,
    verify_seeded,
)


def key(kind, value):
    return {
        "pk": {"S": "W#demo"},
        "sk": {"S": "!revision" if kind == "!revision" else kind + "#" + value},
    }


class DDB:
    def __init__(self):
        self.data = {"records": {}, "portal": {}}
        self.calls = []
        self.unprocessed = 0

    def put(self, table, kind, value, data):
        k = key(kind, value)
        self.put_key(table, k, data)

    def put_key(self, table, key_value, data):
        k = deepcopy(key_value)
        self.data[table][digest(k)] = {**k, "data": {"S": json.dumps(data)}}

    def put_raw(self, table, key_value, values):
        k = deepcopy(key_value)
        self.data[table][digest(k)] = {**k, **deepcopy(values)}

    def get_item(self, TableName, Key, **kwargs):
        assert kwargs["ConsistentRead"] is True
        value = self.data[TableName].get(digest(Key))
        return {"Item": deepcopy(value)} if value else {}

    def scan(self, TableName, **kwargs):
        assert kwargs["ConsistentRead"] is True
        assert kwargs["ProjectionExpression"] == "#p,#s"
        keys = sorted(
            ({"pk": x["pk"], "sk": x["sk"]} for x in self.data[TableName].values()),
            key=digest,
        )
        start = (
            keys.index(kwargs["ExclusiveStartKey"]) + 1
            if kwargs.get("ExclusiveStartKey")
            else 0
        )
        page = keys[start : start + 2]
        return {
            "Items": deepcopy(page),
            **({"LastEvaluatedKey": page[-1]} if start + 2 < len(keys) else {}),
        }

    def batch_write_item(self, RequestItems):
        self.calls.append(deepcopy(RequestItems))
        if self.unprocessed:
            self.unprocessed -= 1
            return {"UnprocessedItems": RequestItems}
        for table, entries in RequestItems.items():
            for item in entries:
                self.data[table].pop(digest(item["DeleteRequest"]["Key"]), None)
        return {}


class S3:
    def __init__(self):
        self.versions = [
            {"Key": "old.jpg", "VersionId": "v1"},
            {"Key": "old.jpg", "VersionId": "v2"},
            {"Key": "old.jpg", "VersionId": "delete"},
        ]
        self.uploads = [
            {"Key": "partial", "UploadId": "one"},
            {"Key": "partial", "UploadId": "two"},
        ]
        self.calls = []
        self.fail_delete = False

    def list_object_versions(self, Bucket, **kwargs):
        assert Bucket == "evidence"
        self.calls.append(("list_versions", deepcopy(kwargs)))
        start = next(
            (
                i + 1
                for i, x in enumerate(self.versions)
                if x["Key"] == kwargs.get("KeyMarker")
                and x["VersionId"] == kwargs.get("VersionIdMarker")
            ),
            0,
        )
        items = self.versions[start : start + 1]
        result = {
            "Versions": [x for x in items if x["VersionId"] != "delete"],
            "DeleteMarkers": [x for x in items if x["VersionId"] == "delete"],
            "IsTruncated": start + 1 < len(self.versions),
        }
        if result["IsTruncated"]:
            result.update(
                NextKeyMarker=items[-1]["Key"],
                NextVersionIdMarker=items[-1]["VersionId"],
            )
        return result

    def list_multipart_uploads(self, Bucket, **kwargs):
        assert Bucket == "evidence"
        start = next(
            (
                i + 1
                for i, x in enumerate(self.uploads)
                if x["Key"] == kwargs.get("KeyMarker")
                and x["UploadId"] == kwargs.get("UploadIdMarker")
            ),
            0,
        )
        items = self.uploads[start : start + 1]
        return {
            "Uploads": items,
            "IsTruncated": start + 1 < len(self.uploads),
            **(
                {
                    "NextKeyMarker": items[-1]["Key"],
                    "NextUploadIdMarker": items[-1]["UploadId"],
                }
                if start + 1 < len(self.uploads)
                else {}
            ),
        }

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.calls.append(("abort", {"Key": Key, "UploadId": UploadId}))
        self.uploads = [
            x for x in self.uploads if x != {"Key": Key, "UploadId": UploadId}
        ]

    def delete_objects(self, Bucket, Delete):
        self.calls.append(("delete", deepcopy(Delete)))
        if self.fail_delete:
            return {"Errors": [{"Code": "AccessDenied"}]}
        self.versions = [x for x in self.versions if x not in Delete["Objects"]]
        return {}


@pytest.fixture
def cloud():
    outputs = {
        "RecordsTable": "records",
        "PortalRecordsTable": "portal",
        "EvidenceBucket": "evidence",
        "SharedWorkspaceId": "demo",
        "DataGeneration": "g1",
        "ClerkIssuerUrl": "https://example.clerk.accounts.dev",
        "AuthAudience": "neighborhood-fixer-api",
        "WebUrl": "https://main.example.amplifyapp.com",
        "WorkflowArn": "workflow",
        "BrowserId": "browser",
    }
    resources = {
        "records": "AWS::DynamoDB::Table",
        "portal": "AWS::DynamoDB::Table",
        "evidence": "AWS::S3::Bucket",
        "workflow": "AWS::StepFunctions::StateMachine",
        "browser": "AWS::BedrockAgentCore::BrowserCustom",
    }
    target = Target(
        "111122223333",
        "us-west-2",
        "NeighborhoodFixer",
        "arn:aws:cloudformation:us-west-2:111122223333:stack/NeighborhoodFixer/one",
        outputs,
        resources,
    )
    ddb = DDB()
    ddb.put(
        "records", "workspace", "demo", {"generation": "g0", "admission": "maintenance"}
    )
    ddb.put("records", "incident", "old", {"id": "old"})
    ddb.put("records", "job", "old", {"id": "old"})
    ddb.put("portal", "ticket", "old", {"id": "old"})
    clients = {
        "dynamodb": ddb,
        "s3": S3(),
        "stepfunctions": SimpleNamespace(
            list_executions=lambda **kw: {"executions": []}
        ),
        "bedrock-agentcore": SimpleNamespace(
            list_browser_sessions=lambda **kw: {"items": []}
        ),
    }

    def seed():
        ddb.put_raw("records", key("!revision", ""), {"revision": {"N": "1"}})
        ddb.put(
            "records",
            "workspace",
            "demo",
            {"generation": "g1", "admission": "validation"},
        )
        ddb.put(
            "records",
            "incident",
            "sample",
            {
                "id": "sample",
                "is_sample": True,
                "workspace_id": "demo",
                "observation_ids": ["sample-observation"],
                "observation_count": 1,
                "owner_id": "fixture",
                "agency_status": "NOT_SUBMITTED",
                "submission_status": "PREPARED",
                "resolution_status": "UNVERIFIED",
                "seeded": True,
                "cell": [1, 2],
            },
        )
        ddb.put(
            "records",
            "observation",
            "sample-observation",
            {
                "id": "sample-observation",
                "incident_id": "sample",
                "evidence_ids": [],
                "is_sample": True,
                "workspace_id": "demo",
                "owner_id": "fixture",
                "analysis": None,
                "share_evidence": False,
            },
        )
        event = {
            "id": "sample-event",
            "workspace_id": "demo",
            "incident_id": "sample",
            "type": "ILLUSTRATION",
            "message": "Illustrative fixture created; no report has been sent.",
            "operation_id": "sample-operation",
            "created_at": "2026-09-14T00:00:00Z",
        }
        ddb.put("records", "event", "sample-event", event)
        ddb.put("records", "event:sample", "sample-event", event)
        ddb.put(
            "records",
            "event_head",
            "sample",
            {
                "id": "sample",
                "workspace_id": "demo",
                "event_ids": ["sample-event"],
            },
        )
        ddb.put(
            "records",
            "geo:1:2",
            "sample",
            {
                "id": "sample",
                "workspace_id": "demo",
                "incident_id": "sample",
            },
        )

    return target, clients, seed


def test_inventory_includes_all_pages_versions_delete_markers_and_multipart(cloud):
    target, clients, _ = cloud
    result = inventory(clients, target)
    assert len(result["records"]) == 3
    assert len(result["portal"]) == 1
    assert len(result["versions"]) == 3
    assert len(result["multipart"]) == 2
    assert any(x["VersionId"] == "delete" for x in result["versions"])
    assert (
        "list_versions",
        {"KeyMarker": "old.jpg", "VersionIdMarker": "v1"},
    ) in clients["s3"].calls
    assert not clients["dynamodb"].calls
    assert not any(name in ("delete", "abort") for name, _ in clients["s3"].calls)


def test_reset_refuses_wrong_generation_open_admission_or_running_work(cloud):
    target, clients, _ = cloud
    with pytest.raises(UnsafeReset, match="Deploy"):
        make_plan(clients, target, "g0", "g2", ["user_test"], now=1000)
    clients["dynamodb"].put(
        "records", "workspace", "demo", {"generation": "g0", "admission": "public"}
    )
    with pytest.raises(UnsafeReset, match="maintenance"):
        make_plan(clients, target, "g0", "g1", ["user_test"], now=1000)
    clients["dynamodb"].put(
        "records", "workspace", "demo", {"generation": "g0", "admission": "maintenance"}
    )
    clients["stepfunctions"].list_executions = lambda **kw: {
        "executions": [{"executionArn": "old"}]
    }
    with pytest.raises(UnsafeReset, match="running"):
        make_plan(clients, target, "g0", "g1", ["user_test"], now=1000)


def test_drain_integrity_and_fresh_inventory_checks_precede_all_deletion(cloud):
    target, clients, seed = cloud
    plan = make_plan(clients, target, "g0", "g1", ["user_test"], now=1000)
    for sha, now in [("wrong", 1400), (digest(plan), 1200), (digest(plan), 5000)]:
        with pytest.raises(UnsafeReset):
            apply_reset(clients, target, plan, sha, now=now, seed=seed)
    clients["dynamodb"].put("records", "incident", "unreviewed", {})
    with pytest.raises(UnsafeReset, match="changed"):
        apply_reset(clients, target, plan, digest(plan), now=1400, seed=seed)
    assert not clients["dynamodb"].calls
    assert not any(name in ("delete", "abort") for name, _ in clients["s3"].calls)


def test_full_reset_verifies_fresh_seed_and_no_operational_records(cloud):
    target, clients, seed = cloud
    plan = make_plan(clients, target, "g0", "g1", ["user_test"], now=1000)
    result = apply_reset(clients, target, plan, digest(plan), now=1400, seed=seed)
    assert result["verification"] == {
        "generation_matches": True,
        "admission": "validation",
        "sample_incidents": 1,
        "sample_observations": 1,
        "operational_records": 0,
        "uploaded_objects": 0,
    }
    assert not clients["s3"].versions and not clients["s3"].uploads
    assert not clients["dynamodb"].data["portal"]
    assert all(
        table in ("records", "portal")
        for call in clients["dynamodb"].calls
        for table in call
    )


@pytest.mark.parametrize("field,value", [("kind", "other"), ("schema_version", 2)])
def test_recomputed_digest_cannot_bypass_manifest_structure(cloud, field, value):
    target, clients, _ = cloud
    plan = make_plan(clients, target, "g0", "g1", ["user_test"], now=1000)
    plan[field] = value
    with pytest.raises(UnsafeReset, match="integrity"):
        validate_manifest(plan, digest(plan))


@pytest.mark.parametrize(
    "corruption",
    [
        "job",
        "approval",
        "ticket",
        "user",
        "quota",
        "evidence",
        "draft",
        "auth_revision",
        "event_payload",
        "timeline_payload",
        "event_head_payload",
        "geo_payload",
        "revision_payload",
        "empty_ticket",
        "null_attempt",
        "extra_incident",
        "generation",
        "not_sample",
        "wrong_admission",
    ],
)
def test_post_reset_verification_fails_closed_for_each_corrupted_seed(
    cloud, corruption
):
    target, clients, seed = cloud
    clients["dynamodb"].data = {"records": {}, "portal": {}}
    clients["s3"].versions = []
    clients["s3"].uploads = []
    seed()
    if corruption in (
        "job",
        "approval",
        "ticket",
        "user",
        "quota",
        "evidence",
        "draft",
    ):
        clients["dynamodb"].put("records", corruption, "unexpected", {})
    elif corruption == "auth_revision":
        clients["dynamodb"].put_key(
            "records",
            {"pk": {"S": "W#auth"}, "sk": {"S": "!revision"}},
            {"revision": 1},
        )
    elif corruption in (
        "event_payload",
        "timeline_payload",
        "event_head_payload",
        "geo_payload",
        "empty_ticket",
        "null_attempt",
    ):
        corrupted_keys = {
            "event_payload": key("event", "sample-event"),
            "timeline_payload": key("event:sample", "sample-event"),
            "event_head_payload": key("event_head", "sample"),
            "geo_payload": key("geo:1:2", "sample"),
            "empty_ticket": key("incident", "sample"),
            "null_attempt": key("incident", "sample"),
        }
        record = clients["dynamodb"].data["records"][digest(corrupted_keys[corruption])]
        data = json.loads(record["data"]["S"])
        field, value = {
            "event_payload": ("type", "SUBMITTED"),
            "timeline_payload": ("message", "operational text"),
            "event_head_payload": ("event_ids", ["other-event"]),
            "geo_payload": ("incident_id", "other-incident"),
            "empty_ticket": ("ticket", {}),
            "null_attempt": ("attempt_id", None),
        }[corruption]
        data[field] = value
        record["data"]["S"] = json.dumps(data)
    elif corruption == "revision_payload":
        clients["dynamodb"].put_raw(
            "records", key("!revision", ""), {"revision": {"N": "0"}}
        )
    elif corruption == "extra_incident":
        clients["dynamodb"].put("records", "incident", "extra", {"is_sample": True})
    elif corruption == "not_sample":
        clients["dynamodb"].put(
            "records",
            "incident",
            "sample",
            {"is_sample": False, "workspace_id": "demo"},
        )
    else:
        clients["dynamodb"].put(
            "records",
            "workspace",
            "demo",
            {
                "generation": "wrong" if corruption == "generation" else "g1",
                "admission": "public"
                if corruption == "wrong_admission"
                else "validation",
            },
        )
    with pytest.raises(UnsafeReset):
        verify_seeded(clients, target)


def test_s3_errors_never_proceed_to_table_deletion_or_seed(cloud):
    target, clients, _ = cloud
    plan = make_plan(clients, target, "g0", "g1", ["user_test"], now=1000)
    clients["s3"].fail_delete = True
    with pytest.raises(UnsafeReset, match="S3"):
        apply_reset(
            clients,
            target,
            plan,
            digest(plan),
            now=1400,
            seed=lambda: pytest.fail("seed must not run"),
        )
    assert not clients["dynamodb"].calls


def test_partial_reset_can_resume_only_reviewed_remaining_keys(cloud):
    target, clients, seed = cloud
    plan = make_plan(clients, target, "g0", "g1", ["user_test"], now=1000)
    clients["s3"].versions = []
    clients["s3"].uploads = []
    clients["dynamodb"].data["records"].pop(digest(key("workspace", "demo")))
    assert apply_reset(
        clients, target, plan, digest(plan), now=1400, seed=seed, resume=True
    )["sample_seeded"]


def test_dynamo_batches_are_bounded_and_retry_unprocessed_items(cloud):
    _, clients, _ = cloud
    ddb = clients["dynamodb"]
    for i in range(31):
        ddb.put("portal", "ticket", str(i), {})
    keys = table_keys(ddb, "portal")
    ddb.unprocessed = 2
    delete_table_keys(ddb, "portal", keys, sleep=lambda _: None)
    assert not ddb.data["portal"]
    assert all(len(call["portal"]) <= 25 for call in ddb.calls)
    assert len(ddb.calls) == 4


def test_account_and_stack_ownership_checks_before_any_data_access(cloud):
    target, clients, _ = cloud
    clients["sts"] = SimpleNamespace(
        get_caller_identity=lambda: {"Account": "999999999999"}
    )
    with pytest.raises(UnsafeReset, match="account"):
        resolve_target(clients, target.account, target.region, target.stack_name)
    clients["sts"].get_caller_identity = lambda: {"Account": target.account}
    stack = {
        "StackId": target.stack_id,
        "StackStatus": "UPDATE_COMPLETE",
        "Outputs": [
            {"OutputKey": k, "OutputValue": v} for k, v in target.outputs.items()
        ],
    }
    summaries = [
        {"PhysicalResourceId": k, "ResourceType": v}
        for k, v in target.resources.items()
    ]
    # CloudFormation reports the bucket policy with the bucket's physical ID too.
    # A later duplicate must not hide the bucket resource from the ownership check.
    summaries.append(
        {
            "PhysicalResourceId": target.outputs["EvidenceBucket"],
            "ResourceType": "AWS::S3::BucketPolicy",
        }
    )
    clients["cloudformation"] = SimpleNamespace(
        describe_stacks=lambda **kw: {"Stacks": [stack]},
        list_stack_resources=lambda **kw: {"StackResourceSummaries": summaries},
    )
    assert (
        resolve_target(
            clients, target.account, target.region, target.stack_name
        ).binding()
        == target.binding()
    )
    stack["Outputs"].append(
        {"OutputKey": "EvidenceBucket", "OutputValue": "unrelated-bucket"}
    )
    with pytest.raises(UnsafeReset, match="owned"):
        resolve_target(clients, target.account, target.region, target.stack_name)


def test_manifest_cannot_be_written_to_source_or_follow_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.manage_aws_demo.ROOT", tmp_path)
    with pytest.raises(UnsafeReset):
        private_path(tmp_path / "tracked.json")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/link").symlink_to(tmp_path)
    with pytest.raises(UnsafeReset):
        private_path(tmp_path / ".local/link/secret.json")
