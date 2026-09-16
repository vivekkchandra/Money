"""Server-only configuration with explicit local-development exceptions."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OperatorSettings(BaseSettings):
    """Offline database administration must remain possible during provider outages."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, hide_input_in_errors=True)
    money_env: Literal["development", "test", "preview", "production"] = "production"
    database_url: SecretStr
    money_workspace_id: str = Field(default="private", pattern=r"^[A-Za-z0-9_-]{1,80}$")
    money_version: str = Field(default="0.1.0", max_length=40)
    money_git_sha: str = Field(default="unknown", pattern=r"^(unknown|[0-9a-f]{7,40})$")

    @model_validator(mode="after")
    def production_database(self) -> Self:
        if self.money_env not in {
            "development",
            "test",
        } and not self.database_url.get_secret_value().startswith(
            ("postgres://", "postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError("Production administration requires PostgreSQL")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, hide_input_in_errors=True)

    money_env: Literal["development", "test", "preview", "production"] = "production"
    money_research_mode: Literal["unconfigured", "demo", "live", "live_rnd"] = "unconfigured"
    money_deployment_env: Literal["local", "hosted"] = "local"
    money_sec_user_agent: str | None = Field(default=None, min_length=10, max_length=200)
    money_rnd_provider_timeout_seconds: float = Field(default=30, ge=5, le=60)
    companies_house_api_key: SecretStr | None = None
    money_rnd_company_numbers: dict[str, str] = Field(default_factory=dict)
    fred_api_key: SecretStr | None = None
    money_live_manifest: Path | None = None
    money_live_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    database_url: SecretStr
    research_api_token: SecretStr | None = None
    money_allow_unauthenticated_dev: bool = False
    money_queue_capacity: int = Field(default=100, ge=1, le=10000)
    money_enqueue_per_minute: int = Field(default=20, ge=1, le=1000)
    money_request_max_bytes: int = Field(default=16384, ge=1024, le=1048576)
    money_request_timeout_seconds: float = Field(default=10, ge=0.1, le=60)
    money_worker_poll_seconds: float = Field(default=2, ge=0.1, le=30)
    money_worker_lease_seconds: int = Field(default=120, ge=30, le=3600)
    money_job_timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    money_job_max_attempts: int = Field(default=3, ge=1, le=5)
    money_workspace_id: str = Field(default="private", pattern=r"^[A-Za-z0-9_-]{1,80}$")
    money_auth_mode: Literal["private", "saas"] = "private"
    money_enable_synthetic_demo: bool = False
    money_email_encryption_key: SecretStr | None = None
    money_public_web_url: str = "http://localhost:3000"
    money_account_session_hours: int = Field(default=12, ge=1, le=168)
    money_login_per_minute: int = Field(default=5, ge=1, le=20)
    money_login_global_per_minute: int = Field(default=30, ge=1, le=100)
    money_version: str = Field(default="0.1.0", max_length=40)
    money_git_sha: str = Field(default="unknown", pattern=r"^(unknown|[0-9a-f]{7,40})$")
    money_db_pool_size: int = Field(default=5, ge=1, le=30)
    money_db_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    money_db_statement_timeout_ms: int = Field(default=15000, ge=1000, le=60000)

    @model_validator(mode="after")
    def secure_configuration(self) -> Self:
        local = self.money_env in {"development", "test"} and self.money_deployment_env == "local"
        if self.money_research_mode == "live_rnd" and self.money_env not in {"development", "test"}:
            raise ValueError("Personal R&D data is forbidden in commercial production/preview")
        if self.money_research_mode == "live_rnd" and self.money_enable_synthetic_demo:
            raise ValueError("Personal R&D cannot enable synthetic fallback")
        if self.money_research_mode == "live" and (
            self.money_live_manifest is None
            or self.money_live_manifest_sha256 is None
            or not self.money_live_manifest.is_file()
        ):
            raise ValueError("Live research requires a pinned qualification manifest")
        if not local and (
            self.money_research_mode == "demo" or self.money_enable_synthetic_demo
        ):
            raise ValueError("Synthetic demo mode is forbidden in production")
        if (
            self.money_env == "production"
            and self.money_research_mode != "live"
            and self.money_auth_mode != "saas"
        ):
            raise ValueError("Production requires explicitly qualified live research configuration")
        if self.money_auth_mode == "saas":
            from urllib.parse import urlsplit

            from cryptography.fernet import Fernet

            if self.money_email_encryption_key is None:
                raise ValueError("SaaS requires MONEY_EMAIL_ENCRYPTION_KEY for the email outbox")
            Fernet(self.money_email_encryption_key.get_secret_value().encode())
            origin = urlsplit(self.money_public_web_url)
            if (
                origin.scheme not in ({"https", "http"} if local else {"https"})
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.query
                or origin.fragment
                or origin.path not in {"", "/"}
            ):
                raise ValueError("MONEY_PUBLIC_WEB_URL must be a trusted web origin")
            if self.money_allow_unauthenticated_dev:
                raise ValueError("SaaS account APIs require authenticated service access")
        if self.money_research_mode == "live":
            from money.research.live import load_manifest
            from money.schemas.contracts import utc_now

            assert self.money_live_manifest is not None
            assert self.money_live_manifest_sha256 is not None
            manifest = load_manifest(self.money_live_manifest, self.money_live_manifest_sha256)
            for provider in manifest.provider_qualifications:
                for dataset in provider.datasets:
                    provider.require(dataset, utc_now())
        if self.money_allow_unauthenticated_dev and not local:
            raise ValueError("Unauthenticated access is forbidden in production")
        if not self.money_allow_unauthenticated_dev and (
            self.research_api_token is None or len(self.research_api_token.get_secret_value()) < 32
        ):
            raise ValueError("RESEARCH_API_TOKEN must contain at least 32 characters")
        if not local and not self.database_url.get_secret_value().startswith(
            (
                "postgres://",
                "postgresql://",
                "postgresql+psycopg://",
            )
        ):
            raise ValueError("Production requires PostgreSQL")
        return self
