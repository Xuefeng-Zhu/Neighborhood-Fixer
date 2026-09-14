"""Server-owned identity, admission and daily reservation policy.

The gateway verifies JWT signatures. This layer validates the trusted claims and
never accepts a caller-supplied resident, workspace, quota or generation.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
import uuid


def fail(code, message, status=403, retryable=False, details=None):
    from .domain import DomainError

    error = DomainError(code, message, status, retryable)
    error.details = details
    raise error


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class Policy:
    def initialize_shared_workspace(
        self, admission="validation", allowed_subjects=None
    ):
        """Administrative bootstrap only; never called by an HTTP request."""
        if self.settings.mode != "aws":
            raise ValueError("Cloud workspace initialization requires AWS mode")
        self._validate_admission(admission, allowed_subjects)
        with self.store.atomic(self.settings.shared_workspace_id) as tx:
            current = tx.get("workspace", tx.workspace_id)
            if current and current.get("generation") != self.settings.data_generation:
                raise ValueError("A different generation requires reviewed reset")
            if not current:
                current = {
                    "generation": self.settings.data_generation,
                    "admission": admission,
                    "allowed_subjects": sorted(set(allowed_subjects or [])),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "clock_offset": 0,
                    "lost_receipt": False,
                }
                tx.put("workspace", tx.workspace_id, current)
            self.seed(tx)
        return current

    @staticmethod
    def _validate_admission(admission, allowed_subjects):
        if admission not in ("validation", "public", "maintenance"):
            raise ValueError("Invalid admission state")
        if len(allowed_subjects or []) > 20 or any(
            not re.fullmatch(r"user_[A-Za-z0-9_-]{1,128}", s)
            for s in (allowed_subjects or [])
        ):
            raise ValueError("Invalid validation subjects")
        if admission == "validation" and not allowed_subjects:
            raise ValueError("Validation admission requires explicit Clerk subjects")

    def set_admission(self, admission, allowed_subjects=None):
        self._validate_admission(admission, allowed_subjects)
        with self.store.atomic(self.settings.shared_workspace_id) as tx:
            control = self.require(tx, "workspace", tx.workspace_id)
            if control.get("generation") != self.settings.data_generation:
                raise ValueError("Generation mismatch")
            control.update(
                admission=admission,
                allowed_subjects=sorted(set(allowed_subjects or [])),
            )
            tx.put("workspace", tx.workspace_id, control)
        return control

    def check_generation(self, tx, principal=None):
        if self.settings.mode != "aws":
            return
        control = tx.get("workspace", tx.workspace_id)
        if (
            tx.workspace_id != self.settings.shared_workspace_id
            or not control
            or control.get("generation") != self.settings.data_generation
            or (
                principal is not None
                and principal.get("generation") != self.settings.data_generation
            )
        ):
            fail(
                "WORKSPACE_UNAVAILABLE",
                "This demo generation is unavailable. Refresh after maintenance.",
                503,
                True,
            )
        return control

    def check_admission(self, tx, principal=None):
        if self.settings.mode != "aws":
            return
        control = self.check_generation(tx, principal)
        if control.get("admission") not in ("validation", "public"):
            fail(
                "DEMO_MAINTENANCE",
                "The demo is temporarily closed for maintenance.",
                503,
                True,
            )
        if principal is not None:
            if control["admission"] == "validation" and principal.get(
                "subject"
            ) not in control.get("allowed_subjects", []):
                fail(
                    "ADMISSION_CLOSED",
                    "The demo is being validated before public signup opens.",
                )
            user = tx.get("user", principal.get("user", {}).get("id", ""))
            if user and user.get("disabled"):
                fail("RESIDENT_DISABLED", "This resident account is disabled.")

    @contextmanager
    def authorized(self, principal):
        with self.store.atomic(principal["workspace_id"]) as tx:
            self.check_admission(tx, principal)
            yield tx

    @contextmanager
    def job_transaction(self, workspace_id, job, settlement=False):
        with self.store.atomic(workspace_id) as tx:
            if settlement:
                self.check_generation(tx, job["principal"])
            else:
                self.check_admission(tx, job["principal"])
            if (
                self.settings.mode == "aws"
                and job.get("generation") != self.settings.data_generation
            ):
                fail(
                    "STALE_GENERATION",
                    "This operation belongs to an earlier demo generation.",
                    409,
                )
            yield tx

    def clerk_principal(self, claims):
        if not self.settings.clerk_issuer or not self.settings.authorized_parties:
            fail(
                "AUTH_CONFIGURATION_REQUIRED",
                "Clerk issuer and authorized frontend origins must be configured.",
                503,
            )
        if not isinstance(claims, dict):
            fail("UNAUTHENTICATED", "A verified Clerk session is required.", 401)
        audience = claims.get("aud")
        scopes = claims.get("scope", "")
        if (
            claims.get("iss") != self.settings.clerk_issuer
            or not (
                audience == self.settings.auth_audience
                or isinstance(audience, list)
                and self.settings.auth_audience in audience
            )
            or claims.get("azp") not in self.settings.authorized_parties
            or not isinstance(scopes, str)
            or "nf:resident" not in scopes.split()
            or not re.fullmatch(
                r"user_[A-Za-z0-9_-]{1,128}", str(claims.get("sub", ""))
            )
            or not re.fullmatch(
                r"sess_[A-Za-z0-9_-]{1,128}", str(claims.get("sid", ""))
            )
            or str(claims.get("v")) != "2"
            or claims.get("sts") not in (None, "active")
        ):
            fail(
                "UNAUTHENTICATED",
                "A verified active Clerk session from this application is required.",
                401,
            )
        try:
            expiry, not_before = float(claims["exp"]), float(claims.get("nbf", 0))
            if (
                not math.isfinite(expiry)
                or not math.isfinite(not_before)
                or expiry <= time.time()
                or not_before > time.time() + 5
            ):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            fail("SESSION_EXPIRED", "Your session expired. Sign in again.", 401)
        identity_id = fingerprint([claims["iss"], claims["sub"]])
        # Deterministic opaque internal identity, stable across concurrent first requests.
        resident_id = uuid.uuid5(uuid.NAMESPACE_URL, identity_id).hex
        p = {
            "user": {
                "id": resident_id,
                "name": str(claims.get("name") or "Resident")[:100],
                "resident": "clerk",
            },
            "workspace_id": self.settings.shared_workspace_id,
            "mode": "aws",
            "subject": claims["sub"],
            "generation": self.settings.data_generation,
        }
        with self.authorized(p) as tx:
            if not tx.get("user", resident_id):
                tx.put(
                    "user",
                    resident_id,
                    {**p["user"], "identity_id": identity_id, "disabled": False},
                )
        with self.store.atomic("auth") as tx:
            existing = tx.get("identity", identity_id)
            if existing and existing.get("resident_id") != resident_id:
                fail(
                    "IDENTITY_CONFLICT",
                    "The resident identity mapping requires administrative review.",
                    503,
                )
            if not existing:
                tx.put(
                    "identity",
                    identity_id,
                    {
                        "resident_id": resident_id,
                        "issuer": claims["iss"],
                        "subject": claims["sub"],
                    },
                )
        return p

    def quota_day(self):
        now = datetime.fromtimestamp(time.time(), timezone.utc)
        day = now.strftime("%Y-%m-%d")
        reset = datetime.fromtimestamp(
            (int(now.timestamp()) // 86400 + 1) * 86400, timezone.utc
        )
        return day, reset.isoformat().replace("+00:00", "Z")

    def quota_record(self, tx, principal, day=None):
        day = day or self.quota_day()[0]
        record_id = principal["user"]["id"] + ":" + day
        return record_id, {
            "day": day,
            "reports": 0,
            "uploads": 0,
            "upload_reserved": 0,
            "reasoning": 0,
            **(tx.get("quota", record_id) or {}),
        }

    def workspace_quota_record(self, tx, day=None):
        day = day or self.quota_day()[0]
        record_id = "workspace:" + day
        return record_id, {
            "day": day,
            "reports": 0,
            "uploads": 0,
            "upload_reserved": 0,
            "reasoning": 0,
            **(tx.get("quota", record_id) or {}),
        }

    def quota_limits(self, kind):
        return (
            {
                "reports": self.settings.reports_per_day,
                "uploads": self.settings.uploads_per_day,
                "reasoning": self.settings.reasoning_jobs_per_day,
            }[kind],
            {
                "reports": self.settings.workspace_reports_per_day,
                "uploads": self.settings.workspace_uploads_per_day,
                "reasoning": self.settings.workspace_reasoning_jobs_per_day,
            }[kind],
        )

    def quota_snapshot(self, principal):
        day, reset_at = self.quota_day()
        with self.authorized(principal) as tx:
            _, row = self.quota_record(tx, principal, day)
        result = {"date_utc": row["day"], "reset_at": reset_at}
        for kind, limit in (
            ("reports", self.settings.reports_per_day),
            ("uploads", self.settings.uploads_per_day),
            ("reasoning", self.settings.reasoning_jobs_per_day),
        ):
            pending = row["upload_reserved"] if kind == "uploads" else 0
            result[kind] = {
                "limit": limit,
                "used": row[kind],
                "reserved": pending,
                "remaining": max(0, limit - row[kind] - pending),
            }
        return result

    def consume_quota(self, tx, principal, kind, reserve=False):
        if self.settings.mode != "aws":
            return
        self.check_admission(tx, principal)
        day, reset_at = self.quota_day()
        resident = self.quota_record(tx, principal, day)
        workspace = self.workspace_quota_record(tx, day)
        resident_limit, workspace_limit = self.quota_limits(kind)
        rows = (
            ("resident", resident, resident_limit),
            ("workspace", workspace, workspace_limit),
        )
        for scope, (_, row), limit in rows:
            used = row[kind] + (row["upload_reserved"] if kind == "uploads" else 0)
            if used >= limit:
                subject = "Your" if scope == "resident" else "The shared workspace's"
                fail(
                    "DAILY_LIMIT_REACHED",
                    f"{subject} daily {kind} limit has been reached. Try after the next UTC reset.",
                    429,
                    True,
                    {
                        "resource": kind,
                        "scope": scope,
                        "limit": limit,
                        "remaining": 0,
                        "reset_at": reset_at,
                    },
                )
        field = "upload_reserved" if reserve else kind
        for _, (key, row), _ in rows:
            row[field] += 1
            tx.put("quota", key, row)
        return day

    def settle_upload_quota(self, tx, principal, day, completed):
        """Move or release both reservation counters in the request transaction."""
        for key, row in (
            self.quota_record(tx, principal, day),
            self.workspace_quota_record(tx, day),
        ):
            # A reservation created before workspace caps existed has no global
            # counter. Account for a confirmed object without underflowing it.
            if row["upload_reserved"]:
                row["upload_reserved"] -= 1
            if completed:
                row["uploads"] += 1
            tx.put("quota", key, row)

    def request_record(self, tx, principal, kind, key, body_hash):
        if self.settings.mode != "aws":
            return None, None
        try:
            if not key or str(uuid.UUID(key)) != key.lower():
                raise ValueError()
        except (ValueError, AttributeError):
            fail(
                "IDEMPOTENCY_KEY_REQUIRED",
                "Provide a UUID Idempotency-Key for this new request.",
                400,
            )
        request_id = fingerprint([principal["user"]["id"], kind, key])
        current = tx.get("request", request_id)
        if current and current["body_hash"] != body_hash:
            fail(
                "IDEMPOTENCY_CONFLICT",
                "This request key was already used with different content.",
                409,
            )
        return request_id, current

    def reserve_upload(self, principal, key, body_hash, evidence):
        with self.authorized(principal) as tx:
            request_id, current = self.request_record(
                tx, principal, "upload", key, body_hash
            )
            if current and current["status"] == "completed":
                return current, False
            if current and current["status"] == "reserved":
                return current, False
            quota_day = self.consume_quota(tx, principal, "uploads", reserve=True)
            request = {
                "body_hash": body_hash,
                "status": "reserved",
                "evidence": evidence,
                "quota_day": quota_day,
                "lease_until": time.time() + 120,
                "lease_owner": uuid.uuid4().hex,
                "owner_id": principal["user"]["id"],
            }
            tx.put("request", request_id, request)
            return {**request, "id": request_id}, True

    def reclaim_upload(self, principal, request):
        with self.authorized(principal) as tx:
            current = self.require(tx, "request", request["id"])
            if current["status"] == "completed":
                return current, False
            if current["lease_until"] > time.time():
                fail(
                    "UPLOAD_PENDING",
                    "This upload is still being checked. Retry with the same request key.",
                    409,
                    True,
                )
            current.update(lease_owner=uuid.uuid4().hex, lease_until=time.time() + 120)
            tx.put("request", current["id"], current)
            return current, True

    def finish_upload(self, principal, request):
        with self.authorized(principal) as tx:
            current = self.require(tx, "request", request["id"])
            if current["status"] == "completed":
                return current["evidence"]
            if current["status"] != "reserved":
                fail(
                    "UPLOAD_PENDING",
                    "This upload requires retry with its original request key.",
                    409,
                    True,
                )
            self.settle_upload_quota(
                tx, principal, current["quota_day"], completed=True
            )
            tx.put("evidence", current["evidence"]["id"], current["evidence"])
            current["status"] = "completed"
            tx.put("request", current["id"], current)
            return current["evidence"]

    def fail_upload(self, principal, request):
        """Release only a definitively rejected write, never a timeout/unknown."""
        with self.authorized(principal) as tx:
            current = self.require(tx, "request", request["id"])
            if (
                current["status"] != "reserved"
                or current["lease_owner"] != request["lease_owner"]
            ):
                return
            self.settle_upload_quota(
                tx, principal, current["quota_day"], completed=False
            )
            current["status"] = "failed"
            tx.put("request", current["id"], current)

    @staticmethod
    def writable_incident(incident):
        if incident.get("is_sample") or incident.get("seeded"):
            fail(
                "SAMPLE_READ_ONLY",
                "This is an illustrative sample. Create your own report to take action.",
            )

    def persist_cloud_upload(
        self, principal, key, body_hash, evidence, data, client=None
    ):
        import boto3
        from botocore.config import Config

        evidence = {
            **evidence,
            "object_key": principal["workspace_id"] + "/" + evidence["id"] + ".jpg",
        }
        evidence["s3_key"] = evidence["object_key"]
        request, write = self.reserve_upload(principal, key, body_hash, evidence)
        if request["status"] == "completed":
            return request["evidence"]
        evidence = request["evidence"]
        client = client or boto3.client(
            "s3",
            region_name=self.settings.region,
            config=Config(
                connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1}
            ),
        )

        def already_stored():
            try:
                head = client.head_object(
                    Bucket=self.settings.evidence_bucket, Key=evidence["object_key"]
                )
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return False
                fail(
                    "UPLOAD_PENDING",
                    "The upload outcome is being checked. Retry with the same request key.",
                    503,
                    True,
                )
            if (
                head.get("ContentLength") != evidence["size"]
                or head.get("Metadata", {}).get("nf-sha256") != evidence["sha256"]
            ):
                fail(
                    "UPLOAD_INTEGRITY_ERROR",
                    "Stored photo verification failed. Contact the demo operator.",
                    409,
                )
            return True

        if not write:
            if already_stored():
                return self.finish_upload(principal, request)
            request, write = self.reclaim_upload(principal, request)
            if not write:
                return request["evidence"]
        # Recheck the fence after sanitizing/recovering, immediately before storage.
        with self.authorized(principal):
            pass
        try:
            client.put_object(
                Bucket=self.settings.evidence_bucket,
                Key=evidence["object_key"],
                Body=data,
                ContentType="image/jpeg",
                ServerSideEncryption="AES256",
                Metadata={"nf-sha256": evidence["sha256"]},
                IfNoneMatch="*",
            )
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "412") and already_stored():
                return self.finish_upload(principal, request)
            if code in (
                "AccessDenied",
                "InvalidArgument",
                "InvalidRequest",
                "NoSuchBucket",
                "403",
                "400",
            ):
                self.fail_upload(principal, request)
                fail(
                    "UPLOAD_REJECTED",
                    "Photo storage rejected this upload. Retry after configuration is corrected.",
                    503,
                    True,
                )
            # Timeout/unknown writes retain their slot and are recovered by HEAD;
            # an immediate retry cannot create another object or consume a new slot.
            fail(
                "UPLOAD_PENDING",
                "The upload outcome is uncertain. Retry with the same request key.",
                503,
                True,
            )
        return self.finish_upload(principal, request)
