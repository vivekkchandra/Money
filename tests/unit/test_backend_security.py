import httpx
import pytest
from pydantic import ValidationError

from money.api.errors import classify_failure
from money.api.settings import OperatorSettings, Settings


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), httpx.ReadTimeout("secret")])
def test_transient_failures_are_retryable_and_safe(error):
    failure = classify_failure(error)
    assert failure.retryable
    assert "secret" not in failure.message


@pytest.mark.parametrize(
    "status,retry", [(400, False), (401, False), (404, False), (429, True), (503, True)]
)
def test_http_retry_classification(status, retry):
    response = httpx.Response(status, request=httpx.Request("GET", "https://example.com"))
    assert (
        classify_failure(
            httpx.HTTPStatusError("secret", request=response.request, response=response)
        ).retryable
        is retry
    )


def test_permanent_failures_do_not_retry():
    assert not classify_failure(ValueError("PIT violation")).retryable


@pytest.mark.parametrize(
    "overrides",
    [
        {"money_env": "preview", "money_research_mode": "demo"},
        {"money_env": "production", "money_research_mode": "unconfigured"},
        {"money_env": "preview", "database_url": "sqlite:///:memory:"},
        {"money_auth_mode": "multi-user"},
        {"money_workspace_id": "../foreign"},
        {"money_job_max_attempts": 0},
        {"money_job_timeout_seconds": 0},
    ],
)
def test_unsafe_production_settings_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "money_env": "production",
                "database_url": "postgresql://u:p@db/money",
                "research_api_token": "test-only-service-token-32-characters",
                **overrides,
            }
        )


def test_operator_can_withdraw_during_provider_outage_without_enabling_api(monkeypatch):
    monkeypatch.setenv("MONEY_RESEARCH_MODE", "live")
    monkeypatch.setenv("MONEY_LIVE_MANIFEST", "/does/not/exist")
    config = OperatorSettings(money_env="production", database_url="postgresql://u:p@db/money")
    assert config.money_workspace_id == "private"
    with pytest.raises(ValidationError):
        OperatorSettings(money_env="production", database_url="sqlite:///:memory:")
