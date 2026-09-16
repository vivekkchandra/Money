from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DatabaseError

from money.api.app import create_app
from money.api.settings import Settings
from money.flows.research import build_runtime, run_research
from money.schemas.contracts import ResearchMandate, ResearchSignal, utc_now
from money.storage import BarrierNotLocked, LeaseLost, ResearchStore
from money.storage import models as db
from money.storage.store import EnqueueRateExceeded, QueueCapacityExceeded, StoreError
from money.worker import run_once

TOKEN = "integration-only-token-32-characters"


def settings(store: ResearchStore, **overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "money_env": "test",
            "money_research_mode": "demo",
            "database_url": store.database_url,
            "research_api_token": TOKEN,
            **overrides,
        }
    )


def prepared(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("integration-worker")
    assert claim is not None
    owned = store.for_claim(claim)
    runtime = build_runtime("demo")
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    assert instrument is not None
    snapshot = runtime.snapshot_builder(instrument)
    owned.update_stage(job["id"], "SNAPSHOT_BUILD")
    owned.save_snapshot(job["id"], snapshot)
    owned.update_stage(job["id"], "FIRST_PASS_RESEARCH")
    return job["id"], owned, runtime, snapshot


def test_api_job_survives_request_and_separate_worker_process(store: ResearchStore):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(settings(store), store)) as client:
        created = client.post("/research/jobs", headers=headers, json={"ticker": "DEMO.L"})
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        assert created.json()["status"] == "QUEUED"
        assert client.get(f"/research/jobs/{job_id}", headers=headers).json()["status"] == "QUEUED"

    # The request and app have ended. A distinct process consumes the persisted queue.
    process = subprocess.run(
        [sys.executable, "-m", "money.worker", "--once"],
        env={
            **os.environ,
            "MONEY_ENV": "test",
            "DATABASE_URL": store.database_url,
            "RESEARCH_API_TOKEN": TOKEN,
            "MONEY_RESEARCH_MODE": "demo",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    restarted = ResearchStore(store.database_url, allow_sqlite=True)
    try:
        with TestClient(create_app(settings(restarted), restarted)) as client:
            result = client.get(f"/research/jobs/{job_id}", headers=headers)
            assert result.status_code == 200
            assert result.json()["status"] == "COMPLETE", result.json()
            assert result.json()["final_state"] == "INSUFFICIENT_EVIDENCE"
            assert result.json()["packet"]["runtime"] == "demo"
            assert result.json()["packet"]["signal"] is None
            reports = client.get(f"/research/jobs/{job_id}/reports", headers=headers).json()
            assert reports["locked"] is True
            assert set(reports["reports"]) == {"tradingagents", "ai_hedge_fund", "qlib"}
            assert "prediction_score" in reports["reports"]["qlib"]
            evidence = client.get(f"/research/jobs/{job_id}/evidence", headers=headers).json()
            assert len(evidence["evidence"]) == 3
    finally:
        restarted.engine.dispose()


def test_authentication_input_validation_and_real_body_limit(store: ResearchStore):
    config = settings(store, money_request_max_bytes=1024)
    with TestClient(create_app(config, store)) as client:
        assert client.post("/research/jobs", json={"ticker": "DEMO.L"}).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        invalid = client.post("/research/jobs", headers=headers, json={"ticker": "../../secret"})
        assert invalid.status_code == 422
        assert "secret" not in invalid.text
        forbidden = client.post(
            "/research/jobs",
            headers=headers,
            json={
                "ticker": "DEMO.L",
                "mandate": {"maximum_capital_gbp": "201"},
            },
        )
        assert forbidden.status_code == 422
        assert (
            client.post("/research/jobs", headers=headers, content=b"x" * 1025).status_code == 413
        )
        assert client.get("/research/jobs/not-a-uuid", headers=headers).status_code == 422
        assert (
            client.get(
                "/research/jobs/00000000-0000-0000-0000-000000000000", headers=headers
            ).status_code
            == 404
        )


def test_prelock_content_barrier_and_atomic_lock(store: ResearchStore):
    job_id, owned, runtime, snapshot = prepared(store)
    for firm in runtime.firms[:2]:
        report = firm.research(ResearchMandate(), snapshot)
        owned.save_report(job_id, report.firm, report)
    with pytest.raises(BarrierNotLocked):
        owned.get_reports(job_id)
    with pytest.raises(BarrierNotLocked):
        owned.lock_first_pass(job_id)
    with pytest.raises(BarrierNotLocked):
        owned.update_stage(job_id, "CREWAI_AUDIT")
    public = owned.public_reports(job_id)
    assert public["locked"] is False
    assert all(set(report) == {"status"} for report in public["reports"].values())
    report = runtime.firms[2].research(ResearchMandate(), snapshot)
    owned.save_report(job_id, report.firm, report)
    owned.lock_first_pass(job_id)
    assert len(owned.get_reports(job_id)) == 3
    owned.update_stage(job_id, "CREWAI_AUDIT")


def test_sealed_report_snapshot_and_packet_integrity(store: ResearchStore):
    job_id, owned, runtime, snapshot = prepared(store)
    report = runtime.firms[0].research(ResearchMandate(), snapshot)
    with pytest.raises(ValidationError):
        owned.save_report(job_id, "qlib" if report.firm != "qlib" else "tradingagents", report)
    with pytest.raises(StoreError, match="hash"):
        owned.save_report(job_id, report.firm, report.model_copy(update={"snapshot_hash": "wrong"}))
    owned.save_report(job_id, report.firm, report)
    with pytest.raises(StoreError, match="replaced"):
        owned.save_report(job_id, report.firm, report)
    with pytest.raises(DatabaseError, match="immutable"):
        with store.engine.begin() as connection:
            connection.execute(db.firm_reports.update().values(payload={"changed": True}))
    with pytest.raises(DatabaseError, match="immutable"):
        with store.engine.begin() as connection:
            connection.execute(db.snapshots.delete())


def test_stale_worker_cannot_persist_after_lease_expiration(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("worker-one")
    assert claim is not None
    stale = store.for_claim(claim)
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job["id"])
            .values(
                lease_until=utc_now() - timedelta(seconds=1),
            )
        )
    # Status reads also reap expired work when every worker has stopped.
    assert store.get_job(job["id"])["error_code"] == "WORKER_LEASE_EXPIRED"
    assert store.claim_job("worker-two") is None
    with pytest.raises(LeaseLost):
        stale.update_stage(job["id"], "DISCOVERY")
    with pytest.raises(LeaseLost):
        stale.fail_job(job["id"], "OVERRIDE", "must not overwrite")


def test_parallel_claims_take_each_job_only_once(store: ResearchStore):
    for _ in range(4):
        store.create_job("DEMO.L", ResearchMandate())
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(
            executor.map(store.claim_job, ["worker-1", "worker-2", "worker-3", "worker-4"])
        )
    assert all(claim is not None for claim in claims)
    assert len({claim.job_id for claim in claims if claim}) == 4


def test_queue_bounds_are_durable(store: ResearchStore):
    store.create_job("DEMO.L", ResearchMandate(), capacity=1)
    with pytest.raises(QueueCapacityExceeded):
        store.create_job("DEMO.L", ResearchMandate(), capacity=1)
    with pytest.raises(EnqueueRateExceeded):
        store.create_job("DEMO.L", ResearchMandate(), rate_per_minute=1)


def test_worker_failure_is_safe_and_unknown_eligibility_rejects(store: ResearchStore):
    job = store.create_job("REAL.L", ResearchMandate())
    assert run_once(
        store, build_runtime("unconfigured"), worker_id="test-worker", mode="unconfigured"
    )
    assert store.get_job(job["id"])["status"] == "REJECTED"
    failed = store.create_job("DEMO.L", ResearchMandate())

    def crash(*args: object) -> None:
        raise RuntimeError("secret-provider-key-do-not-leak")

    run_once(store, None, worker_id="crash-worker", mode="demo", runner=crash)
    result = store.get_job(failed["id"])
    assert result["status"] == "FAILED"
    assert "secret-provider" not in str(result)


def test_completed_packet_is_database_immutable_and_has_stage_audit(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    run_once(store, build_runtime("demo"), worker_id="test-worker", mode="demo")
    assert store.get_job(job["id"])["status"] == "COMPLETE"
    with pytest.raises(DatabaseError, match="immutable"):
        with store.engine.begin() as connection:
            connection.execute(db.packets.update().values(payload={"replaced": True}))
    with store.engine.connect() as connection:
        events = list(
            connection.scalars(
                select(db.audit_events.c.event).where(db.audit_events.c.job_id == job["id"])
            )
        )
    assert "FIRST_PASS_LOCKED" in events
    assert "DECISION_PERSISTED" in events


def test_health_reports_worker_and_database(store: ResearchStore):
    with TestClient(create_app(settings(store), store)) as client:
        assert client.get("/health").status_code == 503
        store.heartbeat("healthy-worker", "demo")
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "database": "ready",
            "worker": "ready",
            "mode": "demo",
        }
        store.heartbeat("healthy-worker", "demo", healthy=False)
        assert client.get("/health").status_code == 503


def test_production_cannot_enable_demo_or_sqlite_or_disable_authentication():
    common = {
        "database_url": SecretStr("postgresql://user:password@db/money"),
        "research_api_token": SecretStr(TOKEN),
    }
    for bad in (
        {"money_research_mode": "demo"},
        {"money_allow_unauthenticated_dev": True},
        {"database_url": "sqlite:///:memory:"},
        {"research_api_token": None},
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate({"money_env": "production", **common, **bad})
    with pytest.raises(ValueError, match="SQLite"):
        ResearchStore("sqlite:///:memory:")


@pytest.mark.parametrize("event_invalidated", [False, True])
def test_signal_expiration_is_enforced_on_every_read(store: ResearchStore, event_invalidated: bool):
    job = store.create_job("DEMO.L", ResearchMandate())
    now = utc_now()
    signal = ResearchSignal(
        research_id=job["id"],
        ticker="DEMO.L",
        state="WATCH",
        issued_at=now - timedelta(hours=2),
        valid_until=now + timedelta(hours=2) if event_invalidated else now - timedelta(hours=1),
        invalidated_at=now - timedelta(minutes=1) if event_invalidated else None,
        entry_low="1",
        entry_high="2",
        quote_currency="GBP",
        invalidation_conditions=("Material adverse filing",),
        event_invalidators=("New dilution",),
        potential_targets=("3",),
        assumed_capital_gbp="200",
        illustrative_allocation_gbp="100",
        modelled_downside_gbp="10",
        horizon_days=1,
    )
    with store.engine.begin() as connection:
        connection.execute(
            db.signals.insert().values(
                job_id=job["id"],
                payload=signal.model_dump(mode="json"),
                valid_until=signal.valid_until,
                created_at=now,
            )
        )
    assert store.get_job(job["id"])["final_state"] == "EXPIRED"
    assert store.list_signals() == []
    assert store.list_signals(expired=True)[0]["final_state"] == "EXPIRED"


def test_typed_positive_packet_cannot_bypass_final_consensus(
    store: ResearchStore, monkeypatch: pytest.MonkeyPatch
):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("consensus-boundary-test")
    assert claim is not None
    owned = store.for_claim(claim)
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(owned, "complete_job", lambda job_id, packet: captured.append(packet))
        run_research(job["id"], owned, build_runtime("demo"))
    assert len(captured) == 1
    forged = captured[0].model_dump(mode="json")
    forged.update(final_state="WATCH", reasons=["Fabricated positive decision"], hash="")
    with pytest.raises(StoreError, match="consensus gates"):
        owned.complete_job(job["id"], forged)
    assert store.get_job(job["id"])["packet"] is None
    owned.complete_job(job["id"], captured[0])
    assert store.get_job(job["id"])["final_state"] == "INSUFFICIENT_EVIDENCE"
