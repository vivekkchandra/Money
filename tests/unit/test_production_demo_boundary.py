"""Production must never admit synthetic research, including the SaaS opt-in."""

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from money.api.settings import Settings


@pytest.mark.parametrize("environment", ["preview", "production"])
@pytest.mark.parametrize("mode,flag", [("demo", False), ("unconfigured", True)])
def test_deployed_settings_reject_all_demo_switches(environment, mode, flag):
    with pytest.raises(ValidationError, match="Synthetic demo mode is forbidden"):
        Settings(
            money_env=environment,
            money_auth_mode="saas",
            money_research_mode=mode,
            money_enable_synthetic_demo=flag,
            database_url="postgresql://test:fixture@localhost/money",
            research_api_token="test-service-token-not-a-production-secret",
            money_email_encryption_key=Fernet.generate_key().decode(),
            money_public_web_url="https://money.example.test",
        )


def test_unqualified_production_can_serve_accounts_without_demo_or_live_research():
    settings = Settings(
        money_env="production",
        money_auth_mode="saas",
        money_research_mode="unconfigured",
        database_url="postgresql://test:fixture@localhost/money",
        research_api_token="test-service-token-not-a-production-secret",
        money_email_encryption_key=Fernet.generate_key().decode(),
        money_public_web_url="https://money.example.test",
    )
    assert not settings.money_enable_synthetic_demo
    assert settings.money_research_mode == "unconfigured"
