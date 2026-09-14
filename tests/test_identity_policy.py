"""Cloud policy tests use SQLite serialization and deterministic I/O doubles only."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import time
import uuid

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
import pytest

from services.api.config import Settings
from services.api.domain import Domain, DomainError
from services.api.main import create_app
from services.api.store import SQLiteStore
from services.api import fixtures


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    settings = Settings(
        mode="aws",
        environment="demo",
        data_dir=tmp_path,
        clerk_issuer="https://nf.clerk.accounts.dev",
        authorized_parties=("https://app.example",),
        allowed_origins=("https://app.example",),
        data_generation="test-generation",
        evidence_bucket="test-private-evidence",
    )
    domain = Domain(settings, SQLiteStore(tmp_path / "cloud-test.sqlite3"))
    domain.initialize_shared_workspace(admission="public")
    monkeypatch.setattr(domain, "engine", lambda: fixtures)
    return domain


def claims(domain, sub="user_alex", **updates):
    return {
        "iss": domain.settings.clerk_issuer,
        "aud": domain.settings.auth_audience,
        "azp": "https://app.example",
        "scope": "nf:resident",
        "sub": sub,
        "sid": "sess_example",
        "v": 2,
        "exp": time.time() + 300,
        **updates,
    }


def resident(domain, sub="user_alex"):
    return domain.clerk_principal(claims(domain, sub))


def test_workspace_daily_limit_defaults_are_finite_and_bounded(tmp_path, monkeypatch):
    for name in (
        "NF_WORKSPACE_REPORTS_PER_DAY",
        "NF_WORKSPACE_UPLOADS_PER_DAY",
        "NF_WORKSPACE_REASONING_JOBS_PER_DAY",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(data_dir=tmp_path)
    assert settings.workspace_reports_per_day == 100
    assert settings.workspace_uploads_per_day == 250
    assert settings.workspace_reasoning_jobs_per_day == 300
    with pytest.raises(ValueError, match="Workspace daily limits"):
        Settings(data_dir=tmp_path, workspace_reports_per_day=0)


def body(**updates):
    return {
        "description": "The curb ramp has broken pavement along the crossing.",
        "category": "damaged_sidewalk",
        "latitude": 47.615,
        "longitude": -122.335,
        "location_label": "Cedar Street at 4th Avenue, Demo Borough",
        "location_confirmed": True,
        "asset_public": "yes",
        "evidence_ids": [],
        "share_public": True,
        **updates,
    }


def report(domain, p, **updates):
    return domain.create_observation(p, body(**updates), str(uuid.uuid4()))


def execute(domain, p, operation):
    asyncio.run(domain.run_job(p["workspace_id"], operation))
    with domain.store.atomic(p["workspace_id"]) as tx:
        return tx.get("operation", operation)


def case(domain, p):
    obs = report(domain, p)
    assert (
        execute(domain, p, domain.start_analysis(p, obs["id"]))["status"] == "completed"
    )
    result = execute(domain, p, domain.decide(p, obs["id"], None, True))
    assert result["status"] == "completed", result
    return domain.incident_detail(p, result["result"]["incident_id"])


class GatewayClaims:
    def __init__(self, app, signed_claims):
        self.app, self.signed_claims = app, signed_claims

    async def __call__(self, scope, receive, send):
        scope["aws.event"] = {
            "requestContext": {"authorizer": {"jwt": {"claims": self.signed_claims}}}
        }
        await self.app(scope, receive, send)


def client(domain, signed_claims=None):
    return TestClient(
        GatewayClaims(
            create_app(domain.settings, domain), signed_claims or claims(domain)
        )
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"iss": "https://other.clerk.accounts.dev"},
        {"aud": "wrong"},
        {"azp": "https://evil.example"},
        {"azp": "https://app.example/"},
        {"scope": "nf:resident-extra"},
        {"scope": ["nf:resident"]},
        {"sub": "old-cognito-sub"},
        {"sid": None},
        {"sid": "user_not_a_session"},
        {"v": 1},
        {"sts": "pending"},
        {"sts": "ended"},
        {"exp": 0},
        {"exp": float("nan")},
        {"nbf": float("inf")},
        {"nbf": time.time() + 600},
    ],
)
def test_signed_claims_still_require_exact_active_clerk_boundary(cloud, updates):
    with pytest.raises(DomainError) as exc:
        cloud.clerk_principal(claims(cloud, **updates))
    assert exc.value.status == 401
    with cloud.store.atomic(cloud.settings.shared_workspace_id) as tx:
        assert not tx.list("user")


def test_stable_internal_identity_shared_workspace_and_private_authority(cloud):
    alex, sam = resident(cloud), resident(cloud, "user_sam")
    refreshed = cloud.clerk_principal(
        claims(
            cloud,
            sid="sess_refreshed",
            sts="active",
            aud=["other", cloud.settings.auth_audience],
        )
    )
    assert alex["user"]["id"] == refreshed["user"]["id"]
    assert alex["user"]["id"] != sam["user"]["id"]
    assert "user_alex" not in alex["user"]["id"]
    assert alex["workspace_id"] == sam["workspace_id"] == "demo-borough-v1"
    obs = report(cloud, alex)
    with pytest.raises(DomainError, match="reporting resident"):
        cloud.patch_observation(sam, obs["id"], {"description": "Changed"})
    with client(cloud) as api:
        session = api.get(
            "/api/session", headers={"X-Workspace-ID": "other", "X-User-ID": "other"}
        ).json()
    assert session["user"] == alex["user"]
    assert session["generation"] == "test-generation"
    assert "subject" not in session
    assert session["quotas"]["reports"]["used"] == 1


def test_admission_missing_generation_and_disabled_users_fail_closed(cloud):
    p = resident(cloud)
    cloud.set_admission("validation", ["user_sam"])
    with pytest.raises(DomainError) as denied:
        resident(cloud)
    assert denied.value.code == "ADMISSION_CLOSED"
    resident(cloud, "user_sam")
    cloud.set_admission("maintenance")
    with pytest.raises(DomainError) as denied:
        report(cloud, p)
    assert denied.value.code == "DEMO_MAINTENANCE"
    cloud.set_admission("public")
    with cloud.store.atomic(p["workspace_id"]) as tx:
        user = tx.get("user", p["user"]["id"])
        user["disabled"] = True
        tx.put("user", user["id"], user)
    with pytest.raises(DomainError) as denied:
        resident(cloud)
    assert denied.value.code == "RESIDENT_DISABLED"
    cloud.settings.data_generation = "new-generation"
    with pytest.raises(ValueError, match="different generation"):
        cloud.initialize_shared_workspace("public")
    with pytest.raises(DomainError) as denied:
        resident(cloud)
    assert denied.value.code == "WORKSPACE_UNAVAILABLE"
    with cloud.store.atomic(p["workspace_id"]) as tx:
        tx.delete("workspace", p["workspace_id"])
    with pytest.raises(DomainError) as denied:
        resident(cloud)
    assert denied.value.code == "WORKSPACE_UNAVAILABLE"


def test_sample_idempotent_no_quota_jobs_or_actions_and_excluded_from_matching(cloud):
    p = resident(cloud)
    cloud.initialize_shared_workspace("public")
    with cloud.authorized(p) as tx:
        samples = tx.list("incident")
        assert len(samples) == len(tx.list("observation")) == 1
        assert not tx.list("job") and not tx.list("approval") and not tx.list("ticket")
        sample = samples[0]
        assert sample["is_sample"]
        assert (
            cloud.candidates(
                tx,
                body(
                    category="pothole",
                    latitude=sample["latitude"],
                    longitude=sample["longitude"],
                ),
                p,
            )
            == []
        )
        with pytest.raises(DomainError) as error:
            cloud.subscribe(tx, sample["id"], p["user"]["id"])
        assert error.value.code == "SAMPLE_READ_ONLY"
    with pytest.raises(DomainError) as error:
        cloud.verification(p, sample["id"], {"choice": "looks_fixed"})
    assert error.value.code == "SAMPLE_READ_ONLY"
    with pytest.raises(DomainError) as error:
        cloud.approve(p, sample["id"], {})
    assert error.value.code == "SAMPLE_READ_ONLY"
    assert cloud.quota_snapshot(p)["reports"]["used"] == 0
    assert cloud.incident_detail(p, sample["id"])["is_sample"]


def test_report_replays_atomically_and_changed_content_conflicts(cloud):
    p, key = resident(cloud), str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: cloud.create_observation(p, body(), key), range(12))
        )
    assert len({r["id"] for r in results}) == 1
    assert cloud.quota_snapshot(p)["reports"]["used"] == 1
    with pytest.raises(DomainError) as error:
        cloud.create_observation(p, body(description="Different report"), key)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(DomainError) as error:
        cloud.create_observation(p, body())
    assert error.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    assert cloud.quota_snapshot(p)["reports"]["used"] == 1


def test_report_limit_concurrency_and_account_isolation(cloud):
    p = resident(cloud)

    def create(_):
        try:
            report(cloud, p)
            return 201
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(18)))
    assert results.count(201) == 10
    assert results.count(429) == 8
    assert cloud.quota_snapshot(p)["reports"] == {
        "limit": 10,
        "used": 10,
        "reserved": 0,
        "remaining": 0,
    }
    other = resident(cloud, "user_sam")
    report(cloud, other)
    assert cloud.quota_snapshot(other)["reports"]["remaining"] == 9


def test_workspace_report_cap_serializes_accounts_without_partial_charge(cloud):
    alex, sam = resident(cloud), resident(cloud, "user_sam")
    cloud.settings.workspace_reports_per_day = 3

    def create(index):
        principal = alex if index % 2 else sam
        try:
            report(cloud, principal, description=f"Distinct public report {index}")
            return principal["user"]["id"], 201
        except DomainError as exc:
            return principal["user"]["id"], exc.status

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(12)))

    assert [status for _, status in results].count(201) == 3
    assert [status for _, status in results].count(429) == 9
    used = sum(
        cloud.quota_snapshot(principal)["reports"]["used"] for principal in (alex, sam)
    )
    assert used == 3
    with cloud.authorized(alex) as tx:
        _, workspace = cloud.workspace_quota_record(tx)
        created = [
            item
            for item in tx.list("observation")
            if item["owner_id"] in {alex["user"]["id"], sam["user"]["id"]}
        ]
    assert workspace["reports"] == 3
    assert len(created) == 3


@pytest.mark.parametrize(
    ("kind", "reserve"),
    (("reports", False), ("uploads", True), ("reasoning", False)),
)
def test_workspace_cap_rejection_never_partially_consumes_resident_quota(
    cloud, kind, reserve
):
    alex, sam = resident(cloud), resident(cloud, "user_sam")
    setattr(
        cloud.settings,
        {
            "reports": "workspace_reports_per_day",
            "uploads": "workspace_uploads_per_day",
            "reasoning": "workspace_reasoning_jobs_per_day",
        }[kind],
        1,
    )
    with cloud.authorized(alex) as tx:
        cloud.consume_quota(tx, alex, kind, reserve=reserve)
    with pytest.raises(DomainError) as denied, cloud.authorized(sam) as tx:
        cloud.consume_quota(tx, sam, kind, reserve=reserve)
    assert denied.value.code == "DAILY_LIMIT_REACHED"
    assert denied.value.details["scope"] == "workspace"
    snapshot = cloud.quota_snapshot(sam)[kind]
    assert snapshot["used"] == 0 and snapshot["reserved"] == 0


def test_workspace_upload_reservation_is_released_and_reused(cloud):
    alex, sam = resident(cloud), resident(cloud, "user_sam")
    cloud.settings.workspace_uploads_per_day = 1
    request, fresh = cloud.reserve_upload(
        alex,
        str(uuid.uuid4()),
        "rejected-content",
        {"id": "rejected-evidence", "owner_id": alex["user"]["id"]},
    )
    assert fresh
    with pytest.raises(DomainError) as denied:
        cloud.reserve_upload(
            sam,
            str(uuid.uuid4()),
            "other-content",
            {"id": "other-evidence", "owner_id": sam["user"]["id"]},
        )
    assert denied.value.details["scope"] == "workspace"
    cloud.fail_upload(alex, request)
    _, fresh = cloud.reserve_upload(
        sam,
        str(uuid.uuid4()),
        "other-content",
        {"id": "other-evidence", "owner_id": sam["user"]["id"]},
    )
    assert fresh


def test_quota_uses_utc_calendar_not_virtual_demo_clock(cloud, monkeypatch):
    now = 1_800_057_599  # A fixed UTC time; midnight derived by policy.
    monkeypatch.setattr("services.api.policy.time.time", lambda: now)
    p = resident(cloud)
    report(cloud, p)
    before = cloud.quota_snapshot(p)
    with cloud.authorized(p) as tx:
        workspace = tx.get("workspace", p["workspace_id"])
        workspace["clock_offset"] = 86400 * 365
        tx.put("workspace", workspace["id"], workspace)
    assert cloud.quota_snapshot(p) == before
    now = (now // 86400 + 1) * 86400
    after = cloud.quota_snapshot(p)
    assert after["date_utc"] != before["date_utc"]
    assert after["reports"]["used"] == 0


def test_reasoning_revision_deduplication_and_failed_retry_are_metered(cloud):
    p = resident(cloud)
    obs = report(cloud, p)
    op = cloud.start_analysis(p, obs["id"])
    assert cloud.start_analysis(p, obs["id"]) == op
    assert cloud.quota_snapshot(p)["reasoning"]["used"] == 1
    assert execute(cloud, p, op)["status"] == "completed"
    assert cloud.start_analysis(p, obs["id"]) == op
    cloud.patch_observation(p, obs["id"], {"description": "Changed resident detail."})
    second = cloud.start_analysis(p, obs["id"])
    assert second != op
    with cloud.authorized(p) as tx:
        operation = tx.get("operation", second)
        operation["status"] = "failed"
        tx.put("operation", second, operation)
    third = cloud.start_analysis(p, obs["id"])
    assert third != second
    assert cloud.quota_snapshot(p)["reasoning"]["used"] == 3
    for i in range(27):
        cloud.patch_observation(p, obs["id"], {"description": f"Revision {i}"})
        cloud.start_analysis(p, obs["id"])
    cloud.patch_observation(p, obs["id"], {"description": "Excess reasoning"})
    with pytest.raises(DomainError) as error:
        cloud.start_analysis(p, obs["id"])
    assert error.value.code == "DAILY_LIMIT_REACHED"
    assert error.value.details["resource"] == "reasoning"
    assert cloud.quota_snapshot(p)["reasoning"]["used"] == 30


def test_api_retry_after_and_cors_expose_only_supported_request_header(cloud):
    cloud.settings.reports_per_day = 1
    with client(cloud) as api:
        first = api.post(
            "/api/observations",
            json=body(),
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert first.status_code == 201
        denied = api.post(
            "/api/observations",
            json=body(),
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert denied.status_code == 429
        assert int(denied.headers["Retry-After"]) > 0
        assert denied.json()["error"]["details"]["resource"] == "reports"
        assert denied.json()["error"]["details"]["limit"] == 1
        cors = api.options(
            "/api/observations",
            headers={
                "Origin": "https://app.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
            },
        )
        assert cors.status_code == 200
        assert "idempotency-key" in cors.headers["access-control-allow-headers"].lower()


class FakeS3:
    def __init__(self, failure=None):
        self.objects = {}
        self.puts = 0
        self.failure = failure

    def put_object(self, **kwargs):
        self.puts += 1
        key = kwargs["Key"]
        assert kwargs["IfNoneMatch"] == "*"
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        if self.failure == "denied":
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        if self.failure == "timeout_before":
            raise TimeoutError()
        self.objects[key] = {
            "ContentLength": len(kwargs["Body"]),
            "Metadata": kwargs["Metadata"],
        }
        if self.failure == "timeout_after":
            raise TimeoutError()

    def head_object(self, **kwargs):
        if kwargs["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return self.objects[kwargs["Key"]]


def upload(domain, p, s3, key, content=b"sanitized-test-photo"):
    sha = hashlib.sha256(content).hexdigest()
    evidence = {
        "id": uuid.uuid4().hex,
        "owner_id": p["user"]["id"],
        "size": len(content),
        "sha256": sha,
        "content_type": "image/jpeg",
        "sanitized": True,
        "public_approved": False,
    }
    return domain.persist_cloud_upload(p, key, sha, evidence, content, client=s3)


def test_ambiguous_upload_recovers_without_second_write_or_quota_charge(cloud):
    p, s3, key = resident(cloud), FakeS3("timeout_after"), str(uuid.uuid4())
    with pytest.raises(DomainError) as error:
        upload(cloud, p, s3, key)
    assert error.value.code == "UPLOAD_PENDING"
    assert cloud.quota_snapshot(p)["uploads"]["reserved"] == 1
    evidence = upload(cloud, p, s3, key)
    assert upload(cloud, p, s3, key) == evidence
    assert s3.puts == 1
    assert cloud.quota_snapshot(p)["uploads"] == {
        "limit": 25,
        "used": 1,
        "reserved": 0,
        "remaining": 24,
    }
    with pytest.raises(DomainError) as error:
        upload(cloud, p, s3, key, b"different-photo")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with cloud.authorized(p) as tx:
        assert len(tx.list("evidence")) == 1


def test_definite_upload_rejection_releases_slot_and_can_retry(cloud):
    p, s3, key = resident(cloud), FakeS3("denied"), str(uuid.uuid4())
    with pytest.raises(DomainError) as error:
        upload(cloud, p, s3, key)
    assert error.value.code == "UPLOAD_REJECTED"
    assert cloud.quota_snapshot(p)["uploads"]["remaining"] == 25
    s3.failure = None
    upload(cloud, p, s3, key)
    assert cloud.quota_snapshot(p)["uploads"]["used"] == 1


def test_upload_absent_unknown_lease_blocks_blind_retry_then_recovers(cloud):
    p, s3, key = resident(cloud), FakeS3("timeout_before"), str(uuid.uuid4())
    with pytest.raises(DomainError):
        upload(cloud, p, s3, key)
    with pytest.raises(DomainError) as error:
        upload(cloud, p, s3, key)
    assert error.value.status == 409
    assert s3.puts == 1
    with cloud.authorized(p) as tx:
        request = tx.list("request")[0]
        request["lease_until"] = 0
        tx.put("request", request["id"], request)
    s3.failure = None
    upload(cloud, p, s3, key)
    assert s3.puts == 2 and len(s3.objects) == 1
    assert cloud.quota_snapshot(p)["uploads"]["used"] == 1


def test_upload_limits_count_pending_reservations_and_preserve_owner_privacy(cloud):
    p, s3 = resident(cloud), FakeS3()
    evidence = upload(cloud, p, s3, str(uuid.uuid4()))
    for _ in range(24):
        upload(cloud, p, s3, str(uuid.uuid4()))
    with pytest.raises(DomainError) as error:
        upload(cloud, p, s3, str(uuid.uuid4()))
    assert error.value.status == 429 and s3.puts == 25
    sam = resident(cloud, "user_sam")
    with cloud.authorized(sam) as tx:
        with pytest.raises(DomainError) as error:
            cloud.evidence(tx, evidence["id"], sam, allow_public=True)
    assert error.value.status == 403


def test_maintenance_and_old_generation_stop_jobs_before_model(cloud, monkeypatch):
    p = resident(cloud)
    obs = report(cloud, p)
    op = cloud.start_analysis(p, obs["id"])
    monkeypatch.setattr(cloud, "engine", lambda: pytest.fail("model must not run"))
    cloud.set_admission("maintenance")
    with pytest.raises(DomainError) as error:
        execute(cloud, p, op)
    assert error.value.code == "DEMO_MAINTENANCE"
    cloud.set_admission("public")
    cloud.settings.data_generation = "replacement-generation"
    with pytest.raises(DomainError) as error:
        execute(cloud, p, op)
    assert error.value.code == "WORKSPACE_UNAVAILABLE"
    with cloud.store.atomic(p["workspace_id"]) as tx:
        assert tx.get("operation", op)["status"] == "pending"


def test_maintenance_between_specialists_stops_next_model_call(cloud, monkeypatch):
    p = resident(cloud)
    obs = report(cloud, p)
    execute(cloud, p, cloud.start_analysis(p, obs["id"]))
    operation = cloud.decide(p, obs["id"], None, True)

    def route(observation, principal):
        cloud.set_admission("maintenance")
        return fixtures.route(observation, principal)

    class Engine:
        pass

    engine = Engine()
    engine.route = route
    engine.prepare = lambda *args: pytest.fail("prepare must not run after maintenance")
    monkeypatch.setattr(cloud, "engine", lambda: engine)
    result = execute(cloud, p, operation)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "DEMO_MAINTENANCE"
    with cloud.store.atomic(p["workspace_id"]) as tx:
        assert len(tx.list("incident")) == 1  # only the illustrative sample


def test_prewrite_fence_and_known_receipt_settlement_during_maintenance(
    cloud, monkeypatch
):
    import services.worker.browser as browser

    p = resident(cloud)
    incident = case(cloud, p)
    draft = incident["draft"]
    op = cloud.approve(
        p,
        incident["id"],
        {
            "draft_id": draft["id"],
            "payload_hash": draft["payload_hash"],
            "publish_consent": False,
        },
    )
    writes = []

    async def stopped(payload, attempt, paths, screenshot_path=None, before_write=None):
        cloud.set_admission("maintenance")
        before_write()
        writes.append(attempt)

    monkeypatch.setattr(browser, "submit_report", stopped)
    result = execute(cloud, p, op)
    assert result["status"] == "failed" and not writes
    with cloud.store.atomic(p["workspace_id"]) as tx:
        assert (
            tx.get("incident", incident["id"])["submission_status"]
            == "FAILED_BEFORE_SUBMISSION"
        )
    cloud.set_admission("public")
    incident = case(cloud, p)
    draft = incident["draft"]
    op = cloud.approve(
        p,
        incident["id"],
        {
            "draft_id": draft["id"],
            "payload_hash": draft["payload_hash"],
            "publish_consent": False,
        },
    )

    async def confirmed(
        payload, attempt, paths, screenshot_path=None, before_write=None
    ):
        before_write()
        writes.append(attempt)
        cloud.set_admission("maintenance")
        return {
            "receipt_id": "DEMO-confirmed",
            "url": "https://portal.example/receipt/private",
            "normalized_status": "RECEIVED",
        }

    monkeypatch.setattr(browser, "submit_report", confirmed)
    result = execute(cloud, p, op)
    assert result["status"] == "completed"
    assert result["result"]["submission_status"] == "RECEIPT_CONFIRMED"
    with cloud.store.atomic(p["workspace_id"]) as tx:
        assert len(tx.list("ticket")) == 1
        assert not [job for job in tx.list("job") if job["kind"] == "check_status"]


def test_two_clerk_residents_link_once_and_replay_without_extra_reasoning(cloud):
    alex, sam = resident(cloud), resident(cloud, "user_sam")
    first = case(cloud, alex)
    second = report(
        cloud, sam, description="I also saw broken pavement at this same crossing."
    )
    analysis = execute(cloud, sam, cloud.start_analysis(sam, second["id"]))
    assert analysis["result"]["duplicate_candidates"][0]["id"] == first["id"]
    before = cloud.quota_snapshot(sam)["reasoning"]["used"]
    operation = cloud.decide(sam, second["id"], first["id"], False)
    linked = execute(cloud, sam, operation)
    assert linked["result"]["incident_id"] == first["id"]
    detail = cloud.incident_detail(sam, first["id"])
    assert detail["observation_count"] == 2 and detail.get("draft") is None
    assert cloud.quota_snapshot(sam)["reasoning"]["used"] == before + 1
    assert cloud.decide(sam, second["id"], first["id"], False) == operation
    assert cloud.quota_snapshot(sam)["reasoning"]["used"] == before + 1
    with cloud.authorized(alex) as tx:
        assert len(tx.list("draft")) == 1
        assert not tx.list("attempt")
    with pytest.raises(DomainError) as error:
        cloud.approve(
            sam,
            first["id"],
            {
                "draft_id": first["draft"]["id"],
                "payload_hash": first["draft"]["payload_hash"],
                "publish_consent": True,
            },
        )
    assert error.value.status == 403


def test_reset_fence_blocks_old_callback_registration_and_wake(cloud, monkeypatch):
    from services.agents import aws_workflow
    from services.api import domain as domain_module

    p = resident(cloud)
    incident = case(cloud, p)
    monkeypatch.setattr(domain_module, "Domain", lambda: cloud)
    monkeypatch.setattr(
        aws_workflow,
        "_client",
        lambda: pytest.fail("No callback API under maintenance"),
    )
    cloud.set_admission("maintenance")
    event = {
        "phase": "register_approval",
        "workspace_id": p["workspace_id"],
        "incident_id": incident["id"],
        "draft_id": incident["draft"]["id"],
        "task_token": "synthetic-test-only-token",
    }
    with pytest.raises(DomainError) as error:
        aws_workflow.handler(event, None)
    assert error.value.code == "DEMO_MAINTENANCE"
    with pytest.raises(DomainError):
        aws_workflow.wake_approval(
            p["workspace_id"], incident["id"], incident["draft"]["id"], cloud
        )
    with cloud.store.atomic(p["workspace_id"]) as tx:
        assert not tx.list("callback")
        tx.delete("workspace", p["workspace_id"])
    with pytest.raises(DomainError) as error:
        aws_workflow.handler(event, None)
    assert error.value.code == "WORKSPACE_UNAVAILABLE"
    with cloud.store.atomic(p["workspace_id"]) as tx:
        assert not tx.list("callback")


def test_upload_reservation_keeps_the_charged_day_across_midnight(cloud, monkeypatch):
    p = resident(cloud)
    days = iter(
        [("2026-09-14", "2026-09-15T00:00:00Z"), ("2026-09-15", "2026-09-16T00:00:00Z")]
    )
    monkeypatch.setattr(cloud, "quota_day", lambda: next(days))
    key = str(uuid.uuid4())
    request, fresh = cloud.reserve_upload(
        p, key, "same-content", {"id": "midnight-photo", "owner_id": p["user"]["id"]}
    )
    assert fresh and request["quota_day"] == "2026-09-14"
    cloud.finish_upload(p, request)
    with cloud.authorized(p) as tx:
        _, old_day = cloud.quota_record(tx, p, "2026-09-14")
        _, new_day = cloud.quota_record(tx, p, "2026-09-15")
    assert old_day["uploads"] == 1 and old_day["upload_reserved"] == 0
    assert new_day["uploads"] == new_day["upload_reserved"] == 0
