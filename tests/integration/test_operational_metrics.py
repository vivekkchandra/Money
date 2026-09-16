from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from money.api.app import create_app
from money.api.settings import Settings
from money.flows.research import build_runtime
from money.research.budgets import BudgetLimits, TokenBudgetManager
from money.schemas.contracts import ResearchMandate, Usage, utc_now
from money.storage import ResearchStore
from money.storage import models as db
from money.storage.production_models import provider_state
from money.worker import run_once

TOKEN = "metrics-service-token-with-at-least-32-characters"


def test_metrics_are_scoped_and_unknown_usage_is_not_free(store: ResearchStore):
    private = store.for_workspace("private")
    finished = private.create_job("DEMO.L", ResearchMandate())
    run_once(private, build_runtime("demo"), worker_id="metrics", mode="demo")
    queued = private.create_job("DEMO.L", ResearchMandate())
    other = store.for_workspace("other")
    foreign = other.create_job("DEMO.L", ResearchMandate())
    manager = TokenBudgetManager(private, BudgetLimits())
    for key, reserved in (("known", 50), ("unknown", 60), ("pending", 70)):
        manager.reserve(
            reservation_id=key,
            job_id=queued["id"],
            stage="FIRST_PASS_RESEARCH",
            agent=key,
            provider="test-only",
            model="fixture",
            maximum_tokens=reserved,
            prompt_version="test-v1",
        )
    manager.settle("known", Usage(input_tokens=10, output_tokens=20, cost_gbp=Decimal("0.25")))
    manager.settle("unknown", Usage())
    TokenBudgetManager(other, BudgetLimits()).reserve(
        reservation_id="foreign",
        job_id=foreign["id"],
        stage="FIRST_PASS_RESEARCH",
        agent="private-secret",
        provider="secret",
        model="secret",
        maximum_tokens=900,
        prompt_version="private-secret",
    )
    failed = private.create_job("DEMO.L", ResearchMandate())
    stamp = utc_now()
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == failed["id"])
            .values(
                status="FAILED",
                attempt_count=3,
                created_at=stamp - timedelta(seconds=10),
                completed_at=stamp,
            )
        )
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == foreign["id"])
            .values(
                created_at=stamp - timedelta(days=10),
            )
        )
        connection.execute(
            provider_state.insert().values(
                id="eodhd:ohlcv",
                failures=3,
                open_until=stamp + timedelta(minutes=1),
                updated_at=stamp,
                payload={
                    "calls": 5,
                    "failures": 3,
                    "last_duration_seconds": 0.5,
                    "last_success": False,
                    "secret": "do-not-expose",
                },
            )
        )
        connection.execute(
            provider_state.insert().values(
                id="https://internal-secret.example/?key=secret",
                failures=0,
                updated_at=stamp,
                payload={"calls": "do-not-expose", "last_success": "secret"},
            )
        )
    result = private.operational_metrics(include_details=True)
    assert result["queue_depth"] == 1
    assert result["queue_age_seconds"] < 60
    assert result["jobs"]["total"] == 3
    assert result["jobs"]["failed"] == 1
    assert result["jobs"]["terminal"] == 2
    assert result["jobs"]["failure_rate"] == 0.5
    assert result["jobs"]["retry_attempts"] == 2
    assert result["jobs"]["duration"]["samples"] == 2
    assert result["jobs"]["duration"]["max_seconds"] >= 9.9
    assert result["tokens"] == {
        "source": "budget_reservations",
        "reservations": 3,
        "known_tokens": 30,
        "unknown_usage_count": 2,
        "reserved_tokens": 180,
        "unsettled_reserved_tokens": 130,
        "budget_charged_tokens": 160,
    }
    assert Decimal(result["costs"]["known_total"]) == Decimal("0.25")
    assert result["costs"]["known_count"] == 1
    assert result["costs"]["unknown_count"] == 2
    assert result["signals"]["insufficient_evidence"] == 1
    assert result["signals"]["produced"] == 0
    provider = result["providers"]["records"][0]
    assert provider["provider"] == "eodhd:ohlcv"
    assert provider["circuit"] == "OPEN"
    assert provider["qualification"] == "UNKNOWN"
    assert provider["last_latency_seconds"] == 0.5
    assert "secret" not in str(result)
    assert "do-not-expose" not in str(result)
    assert result["providers"]["scope"] == "shared_compute_plane"
    assert store.operational_metrics()["queue_depth"] == 1
    assert other.operational_metrics()["queue_depth"] == 1
    assert private.get_job(finished["id"])["status"] == "COMPLETE"


def test_detailed_metrics_require_auth_and_public_health_omits_them(store: ResearchStore):
    config = Settings.model_validate(
        {
            "money_env": "test",
            "money_research_mode": "demo",
            "money_allow_unauthenticated_dev": True,
            "database_url": store.database_url,
            "research_api_token": TOKEN,
        }
    )
    with TestClient(create_app(config, store)) as client:
        assert client.get("/research/system").status_code == 401
        response = client.get("/research/system", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        metrics = response.json()
        assert metrics["jobs"]["failure_rate"] is None
        assert metrics["jobs"]["duration"]["mean_seconds"] is None
        assert metrics["providers"]["records"] == []
        assert metrics["costs"]["known_count"] == 0
        public = client.get("/health/ready").json()
        assert not set(public) & {"tokens", "costs", "providers", "jobs", "signals"}
