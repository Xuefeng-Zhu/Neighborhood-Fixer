import os
from dataclasses import dataclass, field
from pathlib import Path


def _enabled(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().lower()
    if value not in ("0", "1", "false", "true"):
        raise ValueError(f"{name} must be true or false")
    return value in ("1", "true")


@dataclass
class Settings:
    auth_provider: str = field(
        default_factory=lambda: os.getenv("NF_AUTH_PROVIDER", "clerk")
    )
    clerk_issuer: str = field(default_factory=lambda: os.getenv("NF_CLERK_ISSUER", ""))
    auth_audience: str = field(
        default_factory=lambda: os.getenv("NF_AUTH_AUDIENCE", "neighborhood-fixer-api")
    )
    authorized_parties: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            filter(
                None,
                os.getenv(
                    "NF_AUTHORIZED_PARTIES", os.getenv("NF_ALLOWED_ORIGINS", "")
                ).split(","),
            )
        )
    )
    shared_workspace_id: str = field(
        default_factory=lambda: os.getenv("NF_SHARED_WORKSPACE_ID", "demo-borough-v1")
    )
    data_generation: str = field(
        default_factory=lambda: os.getenv("NF_DATA_GENERATION", "clerk-v1")
    )
    reports_per_day: int = field(
        default_factory=lambda: int(os.getenv("NF_REPORTS_PER_DAY", "10"))
    )
    uploads_per_day: int = field(
        default_factory=lambda: int(os.getenv("NF_UPLOADS_PER_DAY", "25"))
    )
    reasoning_jobs_per_day: int = field(
        default_factory=lambda: int(os.getenv("NF_REASONING_JOBS_PER_DAY", "30"))
    )
    workspace_reports_per_day: int = field(
        default_factory=lambda: int(os.getenv("NF_WORKSPACE_REPORTS_PER_DAY", "100"))
    )
    workspace_uploads_per_day: int = field(
        default_factory=lambda: int(os.getenv("NF_WORKSPACE_UPLOADS_PER_DAY", "250"))
    )
    workspace_reasoning_jobs_per_day: int = field(
        default_factory=lambda: int(
            os.getenv("NF_WORKSPACE_REASONING_JOBS_PER_DAY", "300")
        )
    )
    mode: str = field(default_factory=lambda: os.getenv("NF_MODE", "local"))
    environment: str = field(
        default_factory=lambda: os.getenv("NF_ENVIRONMENT", "development")
    )
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("NF_DATA_DIR", ".local/data")).resolve()
    )
    portal_url: str = field(
        default_factory=lambda: os.getenv("NF_PORTAL_URL", "http://127.0.0.1:8001")
    )
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            os.getenv(
                "NF_ALLOWED_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
            ).split(",")
        )
    )
    table_name: str = field(default_factory=lambda: os.getenv("NF_TABLE_NAME", ""))
    evidence_bucket: str = field(
        default_factory=lambda: os.getenv("NF_EVIDENCE_BUCKET", "")
    )
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    approval_seconds: int = field(
        default_factory=lambda: int(os.getenv("NF_APPROVAL_SECONDS", "3600"))
    )
    lease_seconds: int = field(
        default_factory=lambda: int(
            os.getenv("NF_WORKER_LEASE_SECONDS", os.getenv("NF_LEASE_SECONDS", "360"))
        )
    )
    max_status_checks: int = field(
        default_factory=lambda: int(os.getenv("NF_MAX_STATUS_CHECKS", "12"))
    )
    status_interval: int = field(
        default_factory=lambda: int(os.getenv("NF_STATUS_INTERVAL_SECONDS", "60"))
    )
    contact_research_enabled: bool = field(
        default_factory=lambda: _enabled("NF_CONTACT_RESEARCH_ENABLED")
    )
    brave_search_secret_arn: str = field(
        default_factory=lambda: os.getenv("NF_BRAVE_SEARCH_SECRET_ARN", "")
    )
    voice_transcripts_table: str = field(
        default_factory=lambda: os.getenv("NF_VOICE_TRANSCRIPTS_TABLE", "")
    )
    outreach_provider_function: str = field(
        default_factory=lambda: os.getenv("NF_OUTREACH_PROVIDER_FUNCTION", "")
    )
    contact_research_function: str = field(
        default_factory=lambda: os.getenv("NF_CONTACT_RESEARCH_FUNCTION", "")
    )
    contact_research_daily_limit: int = field(
        default_factory=lambda: int(os.getenv("NF_CONTACT_RESEARCH_DAILY_LIMIT", "10"))
    )
    workspace_contact_research_daily_limit: int = field(
        default_factory=lambda: int(
            os.getenv("NF_WORKSPACE_CONTACT_RESEARCH_DAILY_LIMIT", "100")
        )
    )
    voice_simulation_daily_limit: int = field(
        default_factory=lambda: int(os.getenv("NF_VOICE_SIMULATION_DAILY_LIMIT", "3"))
    )
    workspace_voice_simulation_daily_limit: int = field(
        default_factory=lambda: int(
            os.getenv("NF_WORKSPACE_VOICE_SIMULATION_DAILY_LIMIT", "30")
        )
    )
    official_domain_exceptions: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            value.strip().lower()
            for value in os.getenv("NF_OFFICIAL_DOMAIN_EXCEPTIONS", "").split(",")
            if value.strip()
        )
    )

    def __post_init__(self):
        import re

        if self.auth_provider != "clerk":
            raise ValueError("NF_AUTH_PROVIDER must be clerk")
        if (
            any(
                not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
                for value in (self.shared_workspace_id, self.data_generation)
            )
            or self.shared_workspace_id == "auth"
        ):
            raise ValueError("Invalid shared workspace or data generation")
        if any(
            not 1 <= value <= 1000
            for value in (
                self.reports_per_day,
                self.uploads_per_day,
                self.reasoning_jobs_per_day,
            )
        ):
            raise ValueError("Daily limits must be finite positive values")
        if any(
            not 1 <= value <= 10000
            for value in (
                self.workspace_reports_per_day,
                self.workspace_uploads_per_day,
                self.workspace_reasoning_jobs_per_day,
            )
        ):
            raise ValueError("Workspace daily limits must be finite positive values")
        if self.mode not in ("local", "aws"):
            raise ValueError(
                "NF_MODE must be local or aws; unknown modes never fall back to fixtures"
            )
        if (
            not 60 <= self.approval_seconds <= 86400
            or not 1 <= self.lease_seconds <= 900
            or not 1 <= self.max_status_checks <= 100
            or not 1 <= self.status_interval <= 86400
        ):
            raise ValueError("Workflow bounds are outside the supported finite ranges")
        if self.environment not in ("development", "test", "demo", "production"):
            raise ValueError(
                "NF_ENVIRONMENT must be development, test, demo, or production"
            )
        if any(
            not 1 <= value <= 10000
            for value in (
                self.contact_research_daily_limit,
                self.workspace_contact_research_daily_limit,
                self.voice_simulation_daily_limit,
                self.workspace_voice_simulation_daily_limit,
            )
        ):
            raise ValueError("Outreach daily limits must be finite positive values")
        import re

        if any(
            not re.fullmatch(
                r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
                value,
            )
            for value in self.official_domain_exceptions
        ):
            raise ValueError("Official domain exceptions must be exact hostnames")

    @property
    def local_controls(self) -> bool:
        return self.mode == "local" and self.environment == "development"

    @property
    def outreach_enabled(self) -> bool:
        return self.mode == "local" or self.contact_research_enabled

    def secret(self) -> str:
        value = os.getenv("NF_SESSION_SECRET")
        if value:
            return value
        if self.mode != "local":
            raise RuntimeError("NF_SESSION_SECRET must be configured in AWS mode")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        secret_path = self.data_dir / ".session-secret"
        try:
            return secret_path.read_text().strip()
        except FileNotFoundError:
            import secrets

            value = secrets.token_urlsafe(48)
            try:
                fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write(value)
            except FileExistsError:
                return secret_path.read_text().strip()
            return value
