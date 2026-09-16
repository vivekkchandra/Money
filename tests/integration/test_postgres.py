"""Run the actual deployment dialect in CI when TEST_DATABASE_URL is available."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError

from money.flows.research import build_runtime
from money.schemas.contracts import ResearchMandate
from money.storage import ResearchStore
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
    finally:
        store.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
