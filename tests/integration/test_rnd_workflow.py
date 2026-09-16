"""Synthetic test inputs prove isolation/durability, never live-provider acceptance."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from test_commercial_product import store as store

from money.api.app import create_app
from money.api.settings import Settings
from money.data.rnd_cache import RndProviderCache
from money.data.rnd_market import YFinanceProvider
from money.data.security import ProviderFailure, SafeFetcher, SourceSecurityError
from money.flows.research import build_runtime
from money.research.rnd import RndRuntime, run_rnd, study_analysis
from money.research.rnd_contracts import RndSnapshot
from money.schemas.contracts import PriceBar, ResearchMandate, utc_now
from money.storage import ResearchStore
from money.storage import models as db
from money.storage.store import IdempotencyConflict, StoreError
from money.worker import run_once

TOKEN = "rnd-test-only-service-token-32-characters"


def config(store, **overrides):
    return Settings.model_validate({
        "money_env": "test", "money_research_mode": "live_rnd",
        "database_url": store.database_url, "research_api_token": TOKEN, **overrides,
    })


def market_fixture(ticker="AAPL", currency="USD"):
    now = utc_now() - timedelta(seconds=1)
    metadata = {
        "symbol": ticker, "currency": currency, "exchange": "LSE" if ticker.endswith(".L")
        else "NMS", "quoteType": "EQUITY", "longName": "Synthetic unit-test company",
        "regularMarketPrice": 120, "regularMarketTime": int(now.timestamp()),
        "exchangeTimezoneName": "Europe/London" if ticker.endswith(".L") else "America/New_York",
    }
    raw = {
        "metadata": metadata, "info": {}, "retrieved_at": now.isoformat(),
        "requested_at": (now - timedelta(seconds=1)).isoformat(),
        "history": [
            {"timestamp": (now - timedelta(days=2)).isoformat(), "open": 100, "high": 110,
             "low": 95, "close": 105, "volume": 1000, "dividends": 0, "stock_splits": 0},
            {"timestamp": (now - timedelta(days=1)).isoformat(), "open": 105, "high": 125,
             "low": 100, "close": 120, "volume": 1200, "dividends": 0, "stock_splits": 0},
        ], "news": [], "provider_version": "test-fixture",
    }
    provider = YFinanceProvider(transport=lambda *_: raw)
    return provider.snapshot(ticker).model_dump(mode="json")


def runtime(ticker="AAPL", currency="USD"):
    return RndRuntime(
        lambda _: market_fixture(ticker, currency),
        lambda *_: {"provider": "official-test", "status": "NOT_CONFIGURED", "records": []},
        lambda: {"provider": "macro-test", "status": "NOT_CONFIGURED", "records": []},
    )


@pytest.mark.parametrize("env", ["production", "preview"])
def test_commercial_environment_rejects_rnd(store, env):
    with pytest.raises(ValidationError, match="Personal R&D"):
        config(store, money_env=env)


def test_rnd_never_permits_demo_or_hosted_unauthenticated_sqlite(store):
    with pytest.raises(ValidationError, match="synthetic"):
        config(store, money_enable_synthetic_demo=True)
    with pytest.raises(ValidationError, match="PostgreSQL"):
        config(store, money_deployment_env="hosted", database_url="sqlite:///explicit-test.db")
    with pytest.raises(ValidationError, match="Unauthenticated"):
        config(store, money_deployment_env="hosted", money_allow_unauthenticated_dev=True)
    with pytest.raises(ValueError, match="unknown research runtime"):
        build_runtime("live_rnd")  # Cannot fall through to demo or unconfigured adapters.
    with pytest.raises(ValidationError):
        ResearchMandate(quote_currencies=("USD",))
    with pytest.raises(ValidationError):
        PriceBar(open=1, high=1, low=1, close=1, volume=1, currency="USD")


def test_worker_queue_kind_isolation_and_idempotency(store):
    standard = store.create_job("DEMO.L", ResearchMandate(), idempotency_key="standard")
    personal = store.create_job(
        "AAPL", ResearchMandate(), research_kind="live_rnd", idempotency_key="personal",
    )
    assert store.claim_job("commercial").job_id == standard["id"]
    assert store.claim_job("commercial") is None
    assert store.claim_job("rnd", research_kind="live_rnd").job_id == personal["id"]
    with pytest.raises(IdempotencyConflict):
        store.create_job("AAPL", ResearchMandate(), idempotency_key="personal")


@pytest.mark.parametrize("ticker,currency", [("AAPL", "USD"), ("BARC.L", "GBp")])
def test_rnd_queue_snapshot_result_restart_and_immutability(store, ticker, currency):
    job = store.for_workspace("personal-a").create_job(
        ticker, ResearchMandate(), research_kind="live_rnd",
    )
    assert run_once(store, runtime(ticker, currency), worker_id="rnd", mode="live_rnd")
    result = store.get_job(job["id"])
    assert result["status"] == "COMPLETE", result
    packet = result["packet"]
    assert packet["runtime"] == "live_rnd"
    assert packet["signal"] is None
    assert packet["final_state"] == "INSUFFICIENT_EVIDENCE"
    assert all(c["status"] == "NOT_CONFIGURED" for c in packet["components"] if c["component"]
               in {"tradingagents", "ai_hedge_fund", "qlib", "lean", "crewai"})
    restarted = ResearchStore(store.database_url, allow_sqlite=store.allow_sqlite)
    try:
        assert restarted.for_workspace("personal-a").get_job(job["id"])["packet"] == packet
        assert restarted.for_workspace("personal-b").get_job(job["id"]) is None
        evidence = restarted.for_workspace("personal-a").get_evidence(job["id"])
        snapshot = RndSnapshot.model_validate(evidence["snapshot"])
        assert snapshot.hash == packet["snapshot_hash"]
        assert len(evidence["evidence"]) == 3
        with pytest.raises(DatabaseError), restarted.transaction() as connection:
            connection.execute(db.snapshots.update().where(
                db.snapshots.c.job_id == job["id"],
            ).values(payload={}))
    finally:
        restarted.engine.dispose()


def test_provider_failure_cannot_make_demo_snapshot_or_packet(store):
    job = store.create_job("AAPL", ResearchMandate(), research_kind="live_rnd")
    def fail(_):
        raise ProviderFailure("MARKET_DATA_UNAVAILABLE")
    failing = RndRuntime(fail, lambda *_: {}, lambda: {})
    run_once(store, failing, worker_id="rnd-fail", mode="live_rnd")
    assert store.get_job(job["id"])["status"] == "FAILED"
    assert store.get_job(job["id"])["packet"] is None
    assert store.get_evidence(job["id"])["snapshot"] is None


def test_existing_commercial_snapshot_and_publication_cannot_be_used_for_rnd(store):
    job = store.create_job("AAPL", ResearchMandate(), research_kind="live_rnd")
    claim = store.claim_job("rnd", research_kind="live_rnd")
    owned = store.for_claim(claim)
    with pytest.raises(StoreError, match="commercial"):
        owned.complete_job(job["id"], {
            "research_id": job["id"], "final_state": "REJECT",
        }, state="REJECTED")
    with pytest.raises(ValueError, match="RND_JOB_BOUNDARY"):
        run_rnd("missing", owned, runtime())


def test_cache_reuses_original_provenance_across_instances_and_ttl(store):
    calls = []
    stamp = utc_now().isoformat()
    def fetch():
        calls.append(1)
        return {"retrieved_at": stamp, "price": 1}
    first = RndProviderCache(store).get("test", "AAPL", fetch, ttl_seconds=60)
    second = RndProviderCache(store).get("test", "AAPL", fetch, ttl_seconds=60)
    assert first == second and len(calls) == 1
    assert second["retrieved_at"] == stamp


def test_split_series_is_not_reported_as_total_return():
    market = market_fixture()
    market["history"][1]["stock_splits"] = 4
    analysis = study_analysis(market)
    assert analysis["price_change_fraction"] is None
    assert analysis["split_events_present"]


@pytest.mark.parametrize("user_agent", ["", "bad\r\nAuthorization: secret", "é", "x" * 201])
def test_sec_user_agent_cannot_inject_headers(user_agent):
    with pytest.raises(SourceSecurityError):
        SafeFetcher(frozenset({"data.sec.gov"}), user_agent=user_agent)


def test_rnd_api_persists_result_and_refuses_commercial_display(store):
    market = market_fixture()
    provider = SimpleNamespace(search=lambda *_args, **_kwargs: SimpleNamespace(
        model_dump=lambda **_: {"instruments": [market["instrument"]],
                                "retrieved_at": market["retrieved_at"]},
    ))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(config(store), store)) as client:
        client.app.state.rnd_provider = provider
        search = client.get("/research/instruments?query=Apple", headers=headers)
        assert search.status_code == 200, search.text
        assert search.json()["mode"] == "live_rnd"
        assert search.json()["instruments"][0]["eligibility"] == "UNKNOWN"
        created = client.post("/research/jobs", headers=headers, json={"ticker": "AAPL"})
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        assert client.post("/research/jobs", headers=headers, json={"ticker": "DEMO.L"}).status_code == 422
        run_once(store, runtime(), worker_id="api-rnd", mode="live_rnd")
        assert client.get(f"/research/jobs/{job_id}", headers=headers).json()["packet"]["purpose"] == "PERSONAL_RND"
        assert client.get(f"/research/jobs/{job_id}/evidence", headers=headers).status_code == 200
    with TestClient(create_app(config(store, money_research_mode="unconfigured"), store)) as client:
        assert client.get(f"/research/jobs/{job_id}", headers=headers).status_code == 503


def test_rnd_migration_has_current_schema(store):
    with store.engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "0008"
        assert connection.execute(select(db.jobs.c.research_kind)).all() == []


def test_diagnostic_claim_is_scoped_and_exact(store):
    first = store.for_workspace("other").create_job(
        "AAPL", ResearchMandate(), research_kind="live_rnd",
    )
    second = store.for_workspace("personal").create_job(
        "BARC.L", ResearchMandate(), research_kind="live_rnd",
    )
    assert store.for_workspace("personal").claim_job(
        "diagnostic", research_kind="live_rnd", job_id=first["id"],
    ) is None
    claim = store.for_workspace("personal").claim_job(
        "diagnostic", research_kind="live_rnd", job_id=second["id"],
    )
    assert claim.job_id == second["id"]
    assert store.get_job(first["id"])["status"] == "QUEUED"


def test_free_personal_usage_is_atomic_but_cannot_admit_commercial_work(store):
    from test_commercial_product import actor, ensure_actor

    from money.product.service import EntitlementDenied, ProductService

    who = actor("personal")
    ensure_actor(store, who)
    product = ProductService(store)
    scoped = store.for_workspace(who.workspace_id)

    def enqueue(key, kind="live_rnd"):
        return scoped.create_job(
            "AAPL", ResearchMandate(), research_kind=kind, idempotency_key=key,
            admission=lambda connection, job_id: product.reserve_research(
                connection, job_id, who, "AAPL", personal_rnd=True,
            ),
        )

    first = enqueue("first")
    assert enqueue("first")["id"] == first["id"]
    with pytest.raises(EntitlementDenied):
        enqueue("bad-mode", "standard")
    enqueue("second")
    enqueue("third")
    with pytest.raises(EntitlementDenied, match="EXHAUSTED"):
        enqueue("fourth")
    assert len(scoped.list_jobs()) == 3
    assert product.summary(who)["used"] == 3


@pytest.mark.parametrize("code,expected,retry", [
    ("RND_PROVIDER_RATE_LIMIT", "PROVIDER_RATE_LIMITED", True),
    ("RND_PROVIDER_TIMEOUT", "PROVIDER_TIMEOUT", True),
    ("RND_STALE_DATA", "STALE_DATA", False),
    ("RND_SYMBOL_INVALID", "INSTRUMENT_NOT_FOUND", False),
    ("RND_PROVIDER_CONFLICT", "CRITICAL_DATA_CONFLICT", False),
])
def test_rnd_failure_taxonomy(code, expected, retry):
    from money.api.errors import classify_failure

    failure = classify_failure(ProviderFailure(code, retryable=True))
    assert (failure.code, failure.retryable) == (expected, retry)


def test_cache_diagnostics_do_not_probe_and_expired_values_are_not_ready(store):
    from money.data.rnd_cache import provider_diagnostics
    from money.storage.production_models import provider_state

    assert not any(row["status"] == "READY" for row in provider_diagnostics(store))
    RndProviderCache(store).get("bank-of-england", "test-series", lambda: {"value": 1},
                                ttl_seconds=60)
    assert next(row for row in provider_diagnostics(store)
                if row["provider"] == "bank-of-england")["status"] == "READY"
    with store.transaction() as connection:
        connection.execute(provider_state.update().where(
            provider_state.c.id == "rnd-bank-of-england",
        ).values(updated_at=utc_now() - timedelta(days=2)))
    assert not any(row["status"] == "READY" for row in provider_diagnostics(store))


def test_cache_does_not_serve_corrupted_payload_or_duplicate_concurrent_fetch(store):
    from money.storage.production_models import provider_state

    cache = RndProviderCache(store)
    def outer():
        with pytest.raises(ProviderFailure, match="PROVIDER_BUSY"):
            cache.get("test", "same", lambda: pytest.fail("duplicate fetch"), ttl_seconds=60)
        return {"value": 1}
    cache.get("test", "same", outer, ttl_seconds=60)
    with store.transaction() as connection:
        row = connection.execute(select(provider_state).where(
            provider_state.c.id.startswith("rnd-cache:"),
        )).mappings().one()
        connection.execute(provider_state.update().where(provider_state.c.id == row["id"]).values(
            payload={**row["payload"], "data": {"value": 2}},
        ))
    with pytest.raises(ProviderFailure, match="RND_CACHE_CORRUPT"):
        cache.get("test", "same", lambda: pytest.fail("must not silently repair"), ttl_seconds=60)


def test_rnd_stale_worker_cannot_publish_after_replacement(store):
    from money.research.rnd_contracts import RndResult
    from money.research.rnd_store import complete_study
    from money.storage import LeaseLost

    job = store.create_job("AAPL", ResearchMandate(), research_kind="live_rnd")
    old = store.claim_job("old", research_kind="live_rnd")
    with store.transaction() as connection:
        connection.execute(db.jobs.update().where(db.jobs.c.id == job["id"]).values(
            lease_until=utc_now() - timedelta(seconds=1),
        ))
    assert store.get_job(job["id"])["status"] == "QUEUED"
    with store.transaction() as connection:
        connection.execute(db.jobs.update().where(db.jobs.c.id == job["id"]).values(
            available_at=utc_now() - timedelta(seconds=1),
        ))
    replacement = store.claim_job("new", research_kind="live_rnd")
    assert replacement.token != old.token
    run_once(store, runtime(), claimed=replacement, worker_id="new", mode="live_rnd")
    packet = store.get_job(job["id"])["packet"]
    with pytest.raises(LeaseLost):
        complete_study(store.for_claim(old), job["id"], RndResult.model_validate(packet))
    assert store.get_job(job["id"])["packet"] == packet


def test_rnd_http_cannot_cross_workspaces_or_forge_workspace_header(store):
    from test_commercial_accounts import configuration, customer, headers

    settings = configuration(store, money_research_mode="live_rnd", money_enable_synthetic_demo=False)
    with TestClient(create_app(settings, store)) as client:
        alice = customer(client.app.state.accounts, "rnd-alice@example.test")
        bob = customer(client.app.state.accounts, "rnd-bob@example.test")
        job = store.for_workspace(alice["workspace"]["id"]).create_job(
            "AAPL", ResearchMandate(), research_kind="live_rnd",
        )
        run_once(store, runtime(), worker_id="tenant-rnd", mode="live_rnd")
        for suffix in ("", "/evidence", "/reports"):
            path = f"/research/jobs/{job['id']}{suffix}"
            assert client.get(path, headers=headers(alice)).status_code == 200
            assert client.get(path, headers=headers(bob)).status_code == 404
            assert client.get(path, headers={
                **headers(bob), "X-Money-Workspace": alice["workspace"]["id"],
            }).status_code == 403
