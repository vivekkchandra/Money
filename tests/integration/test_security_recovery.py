import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from money.api.app import create_app
from money.api.settings import Settings
from money.flows.research import build_runtime
from money.schemas.contracts import ResearchMandate, utc_now
from money.storage import LeaseLost, ResearchStore
from money.storage import models as db
from money.storage.store import IdempotencyConflict, StoreError
from money.worker import run_once, supervise_job

TOKEN = "test-service-token-with-32-characters"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def config(store: ResearchStore, **kwargs: object) -> Settings:
    return Settings.model_validate(
        {
            "money_env": "test",
            "money_research_mode": "demo",
            "database_url": store.database_url,
            "research_api_token": TOKEN,
            **kwargs,
        }
    )


def test_durable_session_expiry_rotation_revocation_and_scope(store: ResearchStore):
    primary = store.for_workspace("private")
    expires = utc_now() + timedelta(hours=1)
    primary.create_session("a" * 64, "b" * 64, expires)
    restarted = ResearchStore(store.database_url, allow_sqlite=True).for_workspace("private")
    try:
        assert restarted.session_valid("a" * 64, "b" * 64)
        assert not restarted.session_valid("a" * 64, "c" * 64)
        assert not store.for_workspace("other").session_valid("a" * 64, "b" * 64)
        restarted.revoke_session("a" * 64, "b" * 64)
        assert not primary.session_valid("a" * 64, "b" * 64)
        primary.create_session("c" * 64, "b" * 64, expires)
        with store.engine.begin() as connection:
            connection.execute(
                db.sessions.update()
                .where(db.sessions.c.token_hash == "c" * 64)
                .values(expires_at=utc_now() - timedelta(seconds=1))
            )
        assert not restarted.session_valid("c" * 64, "b" * 64)
        with pytest.raises(StoreError):
            primary.create_session("d" * 64, "b" * 64, utc_now() + timedelta(days=1))
    finally:
        restarted.engine.dispose()


def test_internal_auth_api_requires_service_auth_even_in_development(store: ResearchStore):
    with TestClient(
        create_app(config(store, money_allow_unauthenticated_dev=True), store)
    ) as client:
        data = {
            "token_hash": "a" * 64,
            "credential_version": "b" * 64,
            "expires_at": (utc_now() + timedelta(hours=1)).isoformat(),
        }
        assert client.post("/internal/auth/sessions", json=data).status_code == 401
        assert (
            client.post(
                "/internal/auth/sessions", json={**data, "password": "secret"}, headers=HEADERS
            ).status_code
            == 422
        )
        assert client.post("/internal/auth/sessions", json=data, headers=HEADERS).status_code == 200
        check = {k: v for k, v in data.items() if k != "expires_at"}
        assert client.post(
            "/internal/auth/sessions/validate", json=check, headers=HEADERS
        ).json() == {"valid": True}
        assert client.post(
            "/internal/auth/sessions/revoke", json=check, headers=HEADERS
        ).json() == {"revoked": True}
        assert client.post(
            "/internal/auth/sessions/validate", json=check, headers=HEADERS
        ).json() == {"valid": False}


def test_unexpected_api_errors_do_not_expose_provider_text(store: ResearchStore, monkeypatch):
    def unsafe_failure(*args):
        raise RuntimeError("sensitive-provider-response-secret")

    with TestClient(create_app(config(store), store)) as client:
        monkeypatch.setattr(store, "for_workspace", unsafe_failure)
        response = client.get("/research/jobs", headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["code"] == "SERVICE_UNAVAILABLE"
        assert "sensitive-provider" not in response.text


def test_rate_limits_are_atomic_and_shared_across_replicas(store: ResearchStore):
    def consume(_: int) -> bool:
        return store.for_workspace("private").consume_rate_limit("login-test", 3, 60)["allowed"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(consume, range(8))) == 3
    assert not store.consume_rate_limit("login-test", 3, 60)["allowed"]
    assert store.for_workspace("other").consume_rate_limit("login-test", 3, 60)["allowed"]
    with TestClient(create_app(config(store, money_login_global_per_minute=2), store)) as client:
        for key in ("a", "b"):
            assert client.post(
                "/internal/auth/rate-limit", headers=HEADERS, json={"key": key * 64}
            ).json()["allowed"]
        assert not client.post(
            "/internal/auth/rate-limit", headers=HEADERS, json={"key": "c" * 64}
        ).json()["allowed"]


def test_idempotency_prevents_duplicates_and_rejects_conflicting_input(store: ResearchStore):
    def create(_: int) -> str:
        return store.create_job("DEMO.L", ResearchMandate(), idempotency_key="request-1")["id"]

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert len(set(executor.map(create, range(6)))) == 1
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.jobs)) == 1
    with pytest.raises(IdempotencyConflict):
        store.create_job("OTHER.L", ResearchMandate(), idempotency_key="request-1")
    other = store.for_workspace("other").create_job(
        "OTHER.L", ResearchMandate(), idempotency_key="request-1"
    )
    assert other["workspace_id"] == "other"


def test_private_workspace_authorizes_every_public_resource(store: ResearchStore):
    foreign = store.for_workspace("other").create_job("DEMO.L", ResearchMandate())
    run_once(store, build_runtime("demo"), worker_id="foreign-worker", mode="demo")
    with TestClient(create_app(config(store), store)) as client:
        for suffix in ("", "/reports", "/evidence"):
            assert (
                client.get(f"/research/jobs/{foreign['id']}{suffix}", headers=HEADERS).status_code
                == 404
            )
        assert client.get("/research/jobs", headers=HEADERS).json()["jobs"] == []
        assert client.get("/research/discovery", headers=HEADERS).json()["candidates"] == []
        assert client.get("/research/universe", headers=HEADERS).json()["instruments"] == []
    for reader in (
        store.for_workspace("private").get_mandate,
        store.for_workspace("private").get_evidence,
        store.for_workspace("private").get_reports,
    ):
        with pytest.raises(StoreError):
            reader(foreign["id"])


def make_available(store: ResearchStore, job_id: str) -> None:
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job_id)
            .values(available_at=utc_now() - timedelta(seconds=1))
        )


def test_transient_failure_retries_with_backoff_then_exhausts(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate(), max_attempts=2)

    def timeout(*_: object) -> None:
        raise TimeoutError("provider-secret-never-leak")

    run_once(store, None, worker_id="one", mode="demo", runner=timeout)
    result = store.get_job(job["id"])
    assert result["status"] == "QUEUED"
    assert result["attempt_count"] == 1
    assert store.claim_job("too-early") is None
    assert "provider-secret" not in str(result)
    make_available(store, job["id"])
    run_once(store, None, worker_id="two", mode="demo", runner=timeout)
    assert store.get_job(job["id"])["error_code"] == "JOB_RETRY_EXHAUSTED"
    assert store.claim_job("exhausted") is None


def test_lease_recovery_preserves_checkpoint_and_fences_stale_writer(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    old = store.claim_job("old")
    assert old is not None
    owned = store.for_claim(old)
    owned.save_artifact(job["id"], "eligibility", {"proof": "immutable"})
    owned.update_stage(job["id"], "DISCOVERY")
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job["id"])
            .values(lease_until=utc_now() - timedelta(seconds=1))
        )
    assert store.get_job(job["id"])["status"] == "QUEUED"
    make_available(store, job["id"])
    new = store.claim_job("new")
    assert new is not None and new.token != old.token
    checkpoint = store.for_claim(new).get_checkpoint(job["id"])
    assert checkpoint["stage"] == "DISCOVERY"
    assert checkpoint["artifacts"]["eligibility"] == {"proof": "immutable"}
    with pytest.raises(LeaseLost):
        owned.fail_job(job["id"], "OVERRIDE", "old owner")


def test_whole_job_deadline_cannot_be_extended_by_heartbeat(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("hung")
    assert claim is not None
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job["id"])
            .values(deadline_at=utc_now() - timedelta(seconds=1))
        )
    with pytest.raises(LeaseLost):
        store.for_claim(claim).renew_lease(job["id"])
    assert store.get_job(job["id"])["error_code"] == "JOB_TIMEOUT"
    assert store.claim_job("next") is None


def test_supervisor_terminates_job_process_at_deadline(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("supervised", job_timeout_seconds=1)
    assert claim is not None
    # Test a deadline already elapsed before child startup: no research can publish.
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job["id"])
            .values(deadline_at=utc_now() - timedelta(seconds=1))
        )
    settings = config(store).model_copy(update={"money_job_timeout_seconds": 0.01})
    supervise_job(store, settings, claim, threading.Event())
    result = store.get_job(job["id"])
    assert result["status"] == "FAILED"
    assert result["error_code"] == "JOB_TIMEOUT"
    assert result["packet"] is None


def test_liveness_readiness_and_enqueue_idempotency_api(store: ResearchStore):
    with TestClient(create_app(config(store), store)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        store.heartbeat("ready-worker", "demo")
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["schema_revision"] in {"0002", "0003"}
        headers = {**HEADERS, "Idempotency-Key": "browser-retry"}
        first = client.post("/research/jobs", headers=headers, json={"ticker": "DEMO.L"})
        second = client.post("/research/jobs", headers=headers, json={"ticker": "DEMO.L"})
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        assert (
            client.post("/research/jobs", headers=headers, json={"ticker": "OTHER.L"}).status_code
            == 409
        )
        assert ready.headers["x-request-id"]
