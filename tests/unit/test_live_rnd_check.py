"""Offline CLI safety tests; fixtures do not qualify real providers or PostgreSQL."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from test_rnd_market import provider, snapshot_payload

from money.api.settings import Settings
from money.data.security import ProviderFailure
from money.research.rnd import RndRuntime
from money.schemas.contracts import ResearchMandate
from money.storage import ResearchStore
from money.storage import models as db
from money.worker import run_once

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_live_rnd.py"))
NAMESPACE = MODULE["main"].__globals__


@pytest.fixture(autouse=True)
def personal_environment(monkeypatch):
    monkeypatch.setenv("MONEY_ENV", "test")
    monkeypatch.setenv("MONEY_RESEARCH_MODE", "live_rnd")
    monkeypatch.setenv("MONEY_ENABLE_SYNTHETIC_DEMO", "false")
    for key in (
        "DATABASE_URL",
        "RESEARCH_API_TOKEN",
        "COMPANIES_HOUSE_API_KEY",
        "MONEY_SEC_USER_AGENT",
        "MONEY_RND_COMPANY_NUMBERS",
    ):
        monkeypatch.delenv(key, raising=False)


def fail(code="RND_PROVIDER_UNAVAILABLE"):
    raise ProviderFailure(code)


def fixture_runtime(*, market_failure=False):
    return RndRuntime(
        fetch_market=(lambda _: fail())
        if market_failure
        else (
            lambda ticker: (
                provider(snapshot_payload(ticker, "USD" if ticker == "AAPL" else "GBp"))
                .snapshot(ticker)
                .model_dump(mode="json")
            )
        ),
        fetch_official=lambda *_: {
            "provider": "official-fixture",
            "status": "NOT_CONFIGURED",
            "reason": "SEC_CONTACT_USER_AGENT_REQUIRED",
            "records": [],
        },
        fetch_macro=lambda: {
            "provider": "macro-fixture",
            "status": "READY",
            "reason": None,
            "records": [{"fixture_only": True}],
        },
    )


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--providers-only", "--run-jobs"],
        ["--run-jobs"],
        ["--run-jobs", "--workspace", "customer-workspace"],
        ["--providers-only", "--workspace", "rnd-acceptance-test"],
        ["--providers-only", "--ticker", "DEMO.L"],
        ["--providers-only", "--ticker", "../etc"],
        ["--providers-only", "--job-timeout-seconds", "301"],
        ["--providers-only", "--job-timeout-seconds", "0"],
        ["--providers-only", *sum((["--ticker", ticker] for ticker in "ABCDEF"), [])],
    ],
)
def test_opt_in_and_bounded_inputs_are_required(argv, monkeypatch):
    monkeypatch.setitem(NAMESPACE, "provider_runtime", lambda *_: pytest.fail("Provider started"))
    with pytest.raises(SystemExit) as caught:
        MODULE["main"](argv)
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"money_env": "production"},
        {"money_env": "preview"},
        {"money_research_mode": "live"},
        {"money_research_mode": "demo"},
        {"money_enable_synthetic_demo": True},
        {"money_rnd_provider_timeout_seconds": 61},
        {"money_rnd_company_numbers": {"BARC.L": "unreviewed"}},
    ],
)
def test_provider_configuration_refuses_commercial_or_synthetic_modes(overrides):
    with pytest.raises(ValidationError):
        MODULE["ProviderProbeSettings"](**overrides)


def test_provider_only_needs_no_database_and_never_claims_native_acceptance(monkeypatch, capsys):
    monkeypatch.setitem(NAMESPACE, "provider_runtime", lambda _: fixture_runtime())
    monkeypatch.setitem(NAMESPACE, "Settings", lambda **_: pytest.fail("DB settings requested"))
    monkeypatch.setitem(NAMESPACE, "ResearchStore", lambda *_, **__: pytest.fail("DB opened"))
    assert MODULE["main"](["--providers-only", "--ticker", "aapl", "--ticker", "AAPL"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INCOMPLETE_RESEARCH"
    assert result["production_qualified"] is False
    assert result["job_execution"] == "NOT_REQUESTED"
    assert set(result["native_research"].values()) == {"NOT_CONFIGURED"}
    assert len(result["instruments"]) == 1
    instrument = result["instruments"][0]
    assert instrument["market"]["status"] == "PASSED"
    assert instrument["market"]["provider_version"] == "fixture-only"
    assert instrument["official"]["status"] == "NOT_CONFIGURED"
    assert instrument["snapshot"]["status"] == "NOT_RUN"
    assert instrument["worker"]["status"] == "NOT_RUN"
    assert instrument["result"]["status"] == "NOT_RUN"
    assert result["macro"]["status"] == "PASSED"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("postgres://private-user:private-password@private-host/db"),
        SQLAlchemyError("postgres://private-user:private-password@private-host/db"),
        ProviderFailure("private-api-key"),
    ],
)
def test_initialization_errors_do_not_expose_exception_text(error, monkeypatch, capsys):
    def unavailable(_):
        raise error

    monkeypatch.setitem(NAMESPACE, "provider_runtime", unavailable)
    assert MODULE["main"](["--providers-only"]) == 2
    output = capsys.readouterr().out
    assert "private" not in output
    result = json.loads(output)
    assert result["status"] == "BLOCKED"
    assert result["instruments"] == []
    assert result["reason"] in {"PROVIDER_UNAVAILABLE", "DATABASE_UNAVAILABLE"}


def test_market_failure_does_not_create_snapshot_or_job_and_macro_is_still_tested(monkeypatch):
    runtime = RndRuntime(
        lambda _: fail(),
        lambda *_: pytest.fail("Official called with no market identity"),
        lambda: {"status": "UNAVAILABLE", "reason": "PROVIDER_DNS_UNAVAILABLE", "records": []},
    )
    monkeypatch.setitem(NAMESPACE, "run_diagnostic_job", lambda *_: pytest.fail("Job written"))
    result = MODULE["check"](runtime, ("BARC.L", "AAPL"), store=object(), settings=object())
    for instrument in result["instruments"]:
        assert instrument["market"] == {"status": "FAILED", "reason": "RND_PROVIDER_UNAVAILABLE"}
        for name in ("official", "snapshot", "worker", "result"):
            assert instrument[name] == {"status": "NOT_RUN", "reason": "MARKET_UNAVAILABLE"}
    assert result["macro"] == {
        "status": "FAILED",
        "reason": "PROVIDER_DNS_UNAVAILABLE",
        "record_count": 0,
    }


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"fake": "market"}, "RND_PROVIDER_DATA_INVALID"),
        (None, "RND_SYMBOL_MISMATCH"),
    ],
)
def test_invalid_or_wrong_instrument_snapshot_fails_closed(payload, code):
    market = payload or fixture_runtime().fetch_market("AAPL")
    runtime = RndRuntime(lambda _: market, lambda *_: pytest.fail("Official started"), lambda: {})
    result = MODULE["probe_instrument"](runtime, "BARC.L")
    assert result["market"] == {"status": "FAILED", "reason": code}
    assert result["snapshot"]["status"] == "NOT_RUN"


def test_empty_ready_context_and_untrusted_error_codes_cannot_pass():
    assert MODULE["context_summary"]({"status": "READY", "records": []}) == {
        "status": "FAILED",
        "reason": "OFFICIAL_NO_RECORDS",
        "record_count": 0,
    }
    assert MODULE["context_summary"]({"status": "UNAVAILABLE", "reason": "secret-key"}) == {
        "status": "FAILED",
        "reason": "PROVIDER_UNAVAILABLE",
        "record_count": 0,
    }


@pytest.fixture
def diagnostic_settings(tmp_path):
    settings = Settings(
        money_env="test",
        money_research_mode="live_rnd",
        database_url=f"sqlite:///{tmp_path / 'fixture-acceptance.db'}",
        research_api_token="offline-fixture-token-at-least-32-characters",
        money_job_timeout_seconds=30,
    )
    config = Config("alembic.ini")
    config.attributes["database_url"] = settings.database_url.get_secret_value()
    command.upgrade(config, "head")
    return settings


def test_existing_diagnostic_workspace_is_not_reused(diagnostic_settings):
    store = MODULE["diagnostic_store"](diagnostic_settings, "rnd-acceptance-existing")
    try:
        store.create_job("AAPL", ResearchMandate(), research_kind="live_rnd")
        with pytest.raises(MODULE["AcceptanceFailure"], match="DIAGNOSTIC_WORKSPACE_NOT_EMPTY"):
            MODULE["diagnostic_store"](diagnostic_settings, "rnd-acceptance-existing")
    finally:
        store.engine.dispose()


def test_schema_revision_must_match_without_running_migrations(diagnostic_settings):
    store = ResearchStore(diagnostic_settings.database_url.get_secret_value(), allow_sqlite=True)
    try:
        with store.engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = 'fixture-stale'"))
        with pytest.raises(MODULE["AcceptanceFailure"], match="MIGRATIONS_NOT_CURRENT"):
            MODULE["diagnostic_store"](diagnostic_settings, "rnd-acceptance-stale")
        with store.engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "fixture-stale"
            )
    finally:
        store.engine.dispose()


def test_hosted_jobs_cannot_use_sqlite(diagnostic_settings):
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings.model_validate(
            {
                **diagnostic_settings.model_dump(),
                "money_deployment_env": "hosted",
            }
        )


def test_diagnostic_worker_claims_only_exact_owned_job_and_verifies_saved_study(
    diagnostic_settings,
    monkeypatch,
):
    store = MODULE["diagnostic_store"](diagnostic_settings, "rnd-acceptance-owned")
    customer = store.for_workspace("customer-not-diagnostic")
    other = customer.create_job("AAPL", ResearchMandate(), research_kind="live_rnd")
    same_workspace = store.create_job("BARC.L", ResearchMandate(), research_kind="live_rnd")
    claims = []

    def fixture_supervisor(repository, settings, claim, stop):
        claims.append(claim.job_id)
        assert not stop.is_set()
        assert run_once(
            repository,
            fixture_runtime(),
            worker_id=claim.worker_id,
            mode="live_rnd",
            claimed=claim,
        )

    monkeypatch.setitem(NAMESPACE, "supervise_job", fixture_supervisor)
    try:
        result = MODULE["run_diagnostic_job"](store, diagnostic_settings, "AAPL")
        assert result["worker"]["status"] == "PASSED"
        assert result["worker"]["scope"] == "WORKER_EXECUTION_ONLY"
        assert claims == [result["worker"]["job_id"]]
        assert customer.get_job(other["id"])["status"] == "QUEUED"
        assert store.get_job(same_workspace["id"])["status"] == "QUEUED"
        assert result["snapshot"]["status"] == "PASSED"
        assert result["result"] == {
            "status": "PASSED",
            "scope": "PERSISTED_RND_EVIDENCE_ONLY",
            "final_state": "INSUFFICIENT_EVIDENCE",
            "native_research": "NOT_CONFIGURED",
            "signal_issued": False,
        }
        with store.engine.connect() as connection:
            persisted = (
                connection.execute(
                    select(db.jobs).where(db.jobs.c.id == claims[0]),
                )
                .mappings()
                .one()
            )
        assert persisted["max_attempts"] == 1
        assert persisted["workspace_id"] == "rnd-acceptance-owned"
    finally:
        store.engine.dispose()


def test_diagnostic_failed_provider_cannot_produce_saved_evidence(diagnostic_settings, monkeypatch):
    store = MODULE["diagnostic_store"](diagnostic_settings, "rnd-acceptance-failed")

    def fixture_supervisor(repository, settings, claim, stop):
        run_once(
            repository,
            fixture_runtime(market_failure=True),
            worker_id=claim.worker_id,
            mode="live_rnd",
            claimed=claim,
        )

    monkeypatch.setitem(NAMESPACE, "supervise_job", fixture_supervisor)
    try:
        result = MODULE["run_diagnostic_job"](store, diagnostic_settings, "AAPL")
        assert result["worker"]["status"] == "FAILED"
        assert result["snapshot"]["status"] == "NOT_RUN"
        assert result["result"]["status"] == "FAILED"
    finally:
        store.engine.dispose()


@pytest.mark.parametrize("claim", [None, SimpleNamespace(job_id="other-tenant-job")])
def test_claim_mismatch_never_runs_compute(claim, monkeypatch):
    store = SimpleNamespace(
        create_job=lambda *_, **__: {"id": "own-job"},
        claim_job=lambda *_, **__: claim,
    )
    settings = SimpleNamespace(
        money_queue_capacity=10,
        money_worker_lease_seconds=30,
        money_job_timeout_seconds=30,
    )
    monkeypatch.setitem(NAMESPACE, "supervise_job", lambda *_: pytest.fail("Compute started"))
    with pytest.raises(MODULE["AcceptanceFailure"], match="DIAGNOSTIC_JOB_NOT_CLAIMED"):
        MODULE["run_diagnostic_job"](store, settings, "AAPL")


def test_job_error_is_sanitized_and_database_is_closed(monkeypatch, capsys):
    disposed = []
    store = SimpleNamespace(engine=SimpleNamespace(dispose=lambda: disposed.append(True)))
    monkeypatch.setitem(NAMESPACE, "Settings", lambda **_: object())
    monkeypatch.setitem(NAMESPACE, "diagnostic_store", lambda *_: store)
    monkeypatch.setitem(NAMESPACE, "build_rnd_runtime", lambda *_: fixture_runtime())

    def private_failure(*_):
        raise RuntimeError("password=never-print-this")

    monkeypatch.setitem(NAMESPACE, "run_diagnostic_job", private_failure)
    assert (
        MODULE["main"](
            [
                "--run-jobs",
                "--workspace",
                "rnd-acceptance-test",
                "--ticker",
                "AAPL",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "password" not in output
    result = json.loads(output)
    assert result["instruments"][0]["worker"]["status"] == "FAILED"
    assert result["instruments"][0]["result"]["status"] == "NOT_RUN"
    assert disposed == [True]
