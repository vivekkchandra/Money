"""Email readiness must be observational, never an email-delivery side effect."""

import os
import subprocess
import sys
from datetime import timedelta

from sqlalchemy import select

from money.accounts import email_worker
from money.accounts.models import email_outbox, users
from money.storage import ResearchStore
from money.storage.models import service_health
from money.storage.store import now_utc


def test_email_healthcheck_fresh_stale_unhealthy_and_wrong_service(store, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", store.database_url)
    monkeypatch.setenv("MONEY_ENV", "test")
    assert email_worker.healthcheck() == 1
    store.heartbeat("research-worker", "demo")
    assert email_worker.healthcheck() == 1
    store.heartbeat("email-test", "transactional-email")
    assert email_worker.healthcheck() == 0
    with store.transaction() as connection:
        connection.execute(
            service_health.update()
            .where(service_health.c.id == "email-test")
            .values(updated_at=now_utc() - timedelta(seconds=91))
        )
    assert email_worker.healthcheck() == 1
    store.heartbeat("email-test", "transactional-email", healthy=False)
    assert email_worker.healthcheck() == 1


def test_email_cli_healthcheck_needs_no_smtp_secret_and_never_claims_or_delivers(store):
    store.heartbeat("email-cli", "transactional-email")
    with store.transaction() as connection:
        connection.execute(
            users.insert().values(
                id="health-test-user",
                email="health@example.test",
                display_name="Health fixture",
                password_hash="not-a-login-credential",
                email_notifications=False,
                created_at=now_utc(),
            )
        )
        connection.execute(
            email_outbox.insert().values(
                id="health-test-email",
                user_id="health-test-user",
                kind="verify",
                encrypted_payload="intentionally-not-decryptable-health-fixture",
                state="QUEUED",
                attempts=0,
                available_at=now_utc(),
                created_at=now_utc(),
            )
        )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MONEY_SMTP_", "MONEY_EMAIL_"))
    }
    environment.update(MONEY_ENV="test", DATABASE_URL=store.database_url)
    before = None
    with store.engine.connect() as connection:
        before = connection.execute(select(email_outbox)).all()
    result = subprocess.run(
        [sys.executable, "-m", "money.accounts.email_worker", "--healthcheck"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    with store.engine.connect() as connection:
        assert connection.execute(select(email_outbox)).all() == before
    store.heartbeat("email-cli", "transactional-email", healthy=False)
    result = subprocess.run(
        [sys.executable, "-m", "money.accounts.email_worker", "--healthcheck"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 1


def test_email_healthcheck_redacts_database_failure(store, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", store.database_url)
    monkeypatch.setenv("MONEY_ENV", "test")

    def unavailable(self, mode):
        raise RuntimeError("database://private:secret@example.test/customer-pii")

    monkeypatch.setattr(ResearchStore, "health", unavailable)
    assert email_worker.healthcheck() == 1
    assert "email_healthcheck_failed" in caplog.text
    assert "secret" not in caplog.text and "customer-pii" not in caplog.text
