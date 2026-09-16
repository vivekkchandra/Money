"""Server-only billing configuration; prices and return URLs never come from customers."""

from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProductSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    money_billing_enabled: bool = False
    money_public_web_url: str = "https://neon-griffin-08e616.netlify.app"
    money_stripe_secret_key: SecretStr | None = None
    money_stripe_webhook_secret: SecretStr | None = None
    money_stripe_prices: dict[str, str] = {}
    money_stripe_api_version: str = "2025-03-31.basil"
    money_stripe_live: bool = False
    money_internal_admin_user_ids: tuple[str, ...] = ()
    money_user_jobs_per_month: int = Field(default=300, ge=1, le=10000)

    @model_validator(mode="after")
    def secure_billing(self) -> Self:
        parsed = urlsplit(self.money_public_web_url)
        if self.money_billing_enabled and (
            not self.money_stripe_secret_key
            or not self.money_stripe_webhook_secret
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Billing requires secrets and a trusted HTTPS web origin")
        if len(set(self.money_stripe_prices.values())) != len(self.money_stripe_prices):
            raise ValueError("Stripe prices must map to exactly one plan")
        if any(
            plan not in {"PRO", "TEAM", "ENTERPRISE"} or not price.startswith("price_")
            for plan, price in self.money_stripe_prices.items()
        ):
            raise ValueError("Invalid server billing price configuration")
        if self.money_billing_enabled and self.money_stripe_secret_key:
            prefix = "sk_live_" if self.money_stripe_live else "sk_test_"
            if not self.money_stripe_secret_key.get_secret_value().startswith(prefix):
                raise ValueError("Stripe key does not match configured billing mode")
        return self
