import os
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from money.crews.cio import CIOResult
from money.flows.research import build_runtime, run_first_pass, run_research
from money.research.budgets import BudgetLimits, BudgetReservation, TokenBudgetManager
from money.schemas.contracts import ResearchMandate, Usage, utc_now
from money.storage import BarrierNotLocked, LeaseLost, ResearchStore
from money.storage import models as db
from money.storage.production_models import budget_reservations


@pytest.fixture(params=("sqlite", "postgres"))
def store(store, request):
    """Repeat the recovery tests on the actual production dialect in CI."""
    if request.param == "sqlite":
        yield store
        return
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL not configured")
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    schema = f"money_budget_{uuid4().hex}"
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    scoped_url = (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    repository = ResearchStore(scoped_url)
    try:
        configuration = Config("alembic.ini")
        configuration.attributes["database_url"] = scoped_url
        command.upgrade(configuration, "head")
        yield repository
    finally:
        repository.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def request(job_id, agent, maximum=10, attempt=1):
    return BudgetReservation(
        reservation_id=f"{job_id}:{attempt}:{agent}",
        job_id=job_id,
        stage="CREWAI_AUDIT" if agent == "crewai" else "FIRST_PASS_RESEARCH",
        agent=agent,
        provider="fixture",
        model="fixture",
        maximum_tokens=maximum,
        prompt_version="test-v1",
    )


def rows(store):
    with store.engine.connect() as connection:
        return list(connection.execute(select(budget_reservations)).mappings())


def prepare(store):
    runtime = build_runtime("demo")
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("first-worker")
    owned = store.for_claim(claim)
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    owned.save_artifact(job["id"], "eligibility", instrument)
    owned.update_stage(job["id"], "SNAPSHOT_BUILD")
    snapshot = runtime.snapshot_builder(instrument)
    owned.save_snapshot(job["id"], snapshot)
    owned.update_stage(job["id"], "FIRST_PASS_RESEARCH")
    return job["id"], owned, runtime, snapshot


def resume(store, owned, job_id):
    assert owned.retry_job(job_id, "PROVIDER_TIMEOUT")
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job_id)
            .values(available_at=utc_now() - timedelta(seconds=1))
        )
    claim = store.claim_job("recovery-worker")
    return store.for_claim(claim)


@pytest.mark.parametrize("duplicate", [False, True])
def test_failed_batch_leaves_no_partial_reservations(store, duplicate):
    job = store.create_job("DEMO.L", ResearchMandate())
    manager = TokenBudgetManager(store, BudgetLimits(per_job=15))
    first = request(job["id"], "tradingagents")
    second = first if duplicate else request(job["id"], "ai_hedge_fund")
    with pytest.raises(ValueError, match="ALREADY_RESERVED" if duplicate else "BUDGET_EXCEEDED"):
        manager.reserve_many((first, second))
    assert rows(store) == []


def test_usage_settlement_is_idempotent_but_conflicts_cannot_replace_usage(store):
    job = store.create_job("DEMO.L", ResearchMandate())
    manager = TokenBudgetManager(store, BudgetLimits())
    reservation = request(job["id"], "tradingagents")
    manager.reserve_many((reservation,))
    usage = Usage(input_tokens=2, output_tokens=1)
    manager.settle(reservation.reservation_id, usage)
    manager.settle(reservation.reservation_id, usage)
    with pytest.raises(ValueError, match="USAGE_CONFLICT"):
        manager.settle(reservation.reservation_id, Usage(input_tokens=0, output_tokens=0))
    assert rows(store)[0]["actual_tokens"] == 3
    assert rows(store)[0]["payload"]["cost_status"] == "unknown"


def test_unknown_usage_keeps_full_reservation_and_stale_workers_cannot_settle(store):
    job_id, owned, _, _ = prepare(store)
    manager = TokenBudgetManager(owned, BudgetLimits(per_job=10))
    reservation = request(job_id, "tradingagents")
    manager.reserve_many((reservation,))
    manager.settle(reservation.reservation_id, Usage())
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        manager.reserve_many((request(job_id, "ai_hedge_fund", 1),))
    assert rows(store)[0]["actual_tokens"] is None
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job_id)
            .values(
                lease_until=utc_now() - timedelta(seconds=1),
            )
        )
    with pytest.raises(LeaseLost):
        manager.settle(reservation.reservation_id, Usage())
    with pytest.raises(LeaseLost):
        manager.reconcile_job(job_id)


def test_resume_reconciles_sealed_report_without_reading_peer_conclusions(store):
    job_id, owned, runtime, snapshot = prepare(store)
    limits = BudgetLimits(per_job=25)
    manager = TokenBudgetManager(owned, limits)
    # A genuinely unknown failed attempt remains charged; only the later sealed run settles.
    manager.reserve_many((request(job_id, "tradingagents", attempt=0),))
    latest = request(job_id, "tradingagents")
    manager.reserve_many((latest,))
    trading = next(firm for firm in runtime.firms if firm.firm == "tradingagents")
    report = trading.research(ResearchMandate(), snapshot)
    report = report.model_copy(update={"usage": Usage(input_tokens=2, output_tokens=1)})
    owned.save_report(job_id, report.firm, report)
    with pytest.raises(BarrierNotLocked):
        owned.get_reports(job_id)
    recovered = resume(store, owned, job_id)
    calls = []

    class CountFirm:
        def __init__(self, delegate):
            self.firm, self.delegate = delegate.firm, delegate

        def research(self, mandate, snapshot):
            calls.append(self.firm)
            return self.delegate.research(mandate, snapshot).model_copy(
                update={"usage": Usage(input_tokens=2, output_tokens=1)}
            )

    runtime = replace(
        runtime,
        firms=tuple(CountFirm(firm) for firm in runtime.firms),
        budget_limits=limits,
        invocation_budgets=(
            ("tradingagents", "fixture", "fixture", 10),
            ("ai_hedge_fund", "fixture", "fixture", 10),
        ),
    )
    run_research(job_id, recovered, runtime)
    assert store.get_job(job_id)["status"] == "COMPLETE"
    assert set(calls) == {"ai_hedge_fund", "qlib"}
    charges = {row["id"]: row["actual_tokens"] for row in rows(store)}
    assert charges[latest.reservation_id] == 3
    assert charges[request(job_id, "tradingagents", attempt=0).reservation_id] is None


def test_resume_reconciles_coupled_cio_usage_without_rerunning_audit(store):
    job_id, owned, runtime, snapshot = prepare(store)
    run_first_pass(job_id, owned, ResearchMandate(), snapshot, runtime.firms)
    reports = tuple(
        runtime.firms[index].research(ResearchMandate(), snapshot) for index in range(3)
    )
    owned.update_stage(job_id, "LEAN_VALIDATION")
    lean = runtime.validate(snapshot, reports)
    owned.save_artifact(job_id, "lean", lean)
    owned.update_stage(job_id, "CREWAI_AUDIT")
    manager = TokenBudgetManager(owned, BudgetLimits())
    reservation = request(job_id, "crewai")
    manager.reserve_many((reservation,))
    audit = runtime.audit(snapshot, reports, lean)
    red_team = runtime.red_team(snapshot, reports)
    cio = CIOResult(
        audit=audit,
        red_team=red_team,
        provider="fixture",
        model="fixture",
        runtime="demo",
        usage=Usage(input_tokens=4, output_tokens=2),
    )
    owned.save_cio_artifacts(job_id, audit, red_team, runtime=cio)
    recovered = resume(store, owned, job_id)

    def forbidden(*args):
        raise AssertionError("sealed CIO must not run twice")

    runtime = replace(
        runtime,
        audit=forbidden,
        red_team=forbidden,
        budget_limits=BudgetLimits(),
        invocation_budgets=(("crewai", "fixture", "fixture", 10),),
    )
    run_research(job_id, recovered, runtime)
    assert store.get_job(job_id)["status"] == "COMPLETE"
    assert len(rows(store)) == 1
    assert rows(store)[0]["actual_tokens"] == 6


def test_cio_is_not_reserved_when_first_pass_fails(store):
    job_id, owned, runtime, _ = prepare(store)

    class FailedFirm:
        firm = "tradingagents"

        def research(self, mandate, snapshot):
            raise ValueError("permanent fixture failure")

    runtime = replace(
        runtime,
        firms=tuple(
            FailedFirm() if firm.firm == "tradingagents" else firm for firm in runtime.firms
        ),
        budget_limits=BudgetLimits(),
        invocation_budgets=(
            ("tradingagents", "fixture", "fixture", 10),
            ("ai_hedge_fund", "fixture", "fixture", 10),
            ("crewai", "fixture", "fixture", 10),
        ),
    )
    run_research(job_id, owned, runtime)
    assert store.get_job(job_id)["status"] == "FAILED"
    assert {row["agent"] for row in rows(store)} == {"tradingagents", "ai_hedge_fund"}


def test_foreign_workspace_cannot_reconcile_usage(store):
    job = store.create_job("DEMO.L", ResearchMandate())
    manager = TokenBudgetManager(store.for_workspace("another-workspace"), BudgetLimits())
    with pytest.raises(ValueError, match="RESOURCE_NOT_FOUND"):
        manager.reconcile_job(job["id"])
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(budget_reservations)) == 0
