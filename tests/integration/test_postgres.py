"""Run the actual deployment dialect in CI when TEST_DATABASE_URL is available."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError

from money.flows.research import build_runtime
from money.research.budgets import BudgetLimits, TokenBudgetManager
from money.schemas.contracts import ResearchMandate, Usage, utc_now
from money.storage import LeaseLost, ResearchStore
from money.storage import models as db
from money.worker import run_once


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured"
)
def test_postgres_migration_worker_claims_and_immutability(monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ["TEST_DATABASE_URL"].replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    schema = f"money_test_{uuid4().hex}"
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    scoped_url = (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    store = ResearchStore(scoped_url)
    try:
        monkeypatch.setenv("MONEY_ENV", "test")
        configuration = Config("alembic.ini")
        configuration.attributes["database_url"] = scoped_url
        command.upgrade(configuration, "head")
        command.check(configuration)
        for _ in range(4):
            store.create_job("DEMO.L", ResearchMandate())
        with ThreadPoolExecutor(max_workers=4) as executor:
            claims = list(executor.map(store.claim_job, [f"pg-{i}" for i in range(4)]))
        assert len({claim.job_id for claim in claims if claim}) == 4
        job = store.create_job("DEMO.L", ResearchMandate())
        run_once(store, build_runtime("demo"), worker_id="pg-demo", mode="demo")
        assert store.get_job(job["id"])["status"] == "COMPLETE"
        with pytest.raises(DatabaseError, match="immutable"):
            with store.engine.begin() as connection:
                connection.execute(db.packets.delete())
        # Real PostgreSQL row locks protect idempotency and login counters across workers.
        with ThreadPoolExecutor(max_workers=8) as executor:
            duplicate_jobs = list(
                executor.map(
                    lambda _: store.create_job(
                        "DEMO.L", ResearchMandate(), idempotency_key="pg-key"
                    ),
                    range(8),
                )
            )
        assert len({job["id"] for job in duplicate_jobs}) == 1
        with ThreadPoolExecutor(max_workers=8) as executor:
            allowed = list(
                executor.map(
                    lambda _: store.consume_rate_limit("postgres-login", 3, 60)["allowed"],
                    range(8),
                )
            )
        assert sum(allowed) == 3
        store.create_session("a" * 64, "b" * 64, utc_now() + timedelta(hours=1))
        assert store.session_valid("a" * 64, "b" * 64)
        store.revoke_session("a" * 64, "b" * 64)
        assert not store.session_valid("a" * 64, "b" * 64)
        old = store.claim_job("postgres-old")
        assert old is not None
        with store.engine.begin() as connection:
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == old.job_id)
                .values(
                    lease_until=utc_now() - timedelta(seconds=1),
                )
            )
        assert store.get_job(old.job_id)["status"] == "QUEUED"
        with store.engine.begin() as connection:
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == old.job_id)
                .values(
                    available_at=utc_now() - timedelta(seconds=1),
                )
            )
        new = store.claim_job("postgres-new")
        assert new is not None and new.job_id == old.job_id and new.token != old.token
        with pytest.raises(LeaseLost):
            store.for_claim(old).fail_job(old.job_id, "STALE", "must not publish")
        budget = TokenBudgetManager(store, BudgetLimits())
        budget.reserve(
            reservation_id="metrics",
            job_id=job["id"],
            stage="FIRST_PASS_RESEARCH",
            agent="metrics",
            provider="test-only",
            model="fixture",
            maximum_tokens=100,
            prompt_version="test-v1",
        )
        budget.settle("metrics", Usage(input_tokens=20, output_tokens=30, cost_gbp=Decimal("0.25")))
        measured = store.operational_metrics(include_details=True)
        assert measured["tokens"]["known_tokens"] == 50
        assert Decimal(measured["costs"]["known_total"]) == Decimal("0.25")
        assert measured["costs"]["basis"] == "ESTIMATED_OR_UNVERIFIED_REPORT"
        assert measured["costs"]["actual_total"] is None
        assert measured["costs"]["actual_count"] == 0
        assert measured["jobs"]["duration"]["samples"] == 1
    finally:
        store.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
