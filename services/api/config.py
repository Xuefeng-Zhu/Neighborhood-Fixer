from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class Settings:
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

    def __post_init__(self):
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

    @property
    def local_controls(self) -> bool:
        return self.mode == "local" and self.environment == "development"

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
