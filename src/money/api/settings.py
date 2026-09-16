"""Server-only configuration with explicit local-development exceptions."""

from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    money_env: Literal["development", "test", "production"] = "production"
    money_research_mode: Literal["unconfigured", "demo"] = "unconfigured"
    database_url: SecretStr
    research_api_token: SecretStr | None = None
    money_allow_unauthenticated_dev: bool = False
    money_queue_capacity: int = Field(default=100, ge=1, le=10000)
    money_enqueue_per_minute: int = Field(default=20, ge=1, le=1000)
    money_request_max_bytes: int = Field(default=16384, ge=1024, le=1048576)
    money_worker_poll_seconds: float = Field(default=2, ge=0.1, le=30)
    money_worker_lease_seconds: int = Field(default=120, ge=30, le=3600)

    @model_validator(mode="after")
    def secure_configuration(self) -> Self:
        local = self.money_env in {"development", "test"}
        if not local and self.money_research_mode == "demo":
            raise ValueError("Synthetic demo mode is forbidden in production")
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
