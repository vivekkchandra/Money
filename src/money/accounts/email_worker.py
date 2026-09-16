"""Bounded, independently deployed transactional SMTP worker.

At-least-once transport: a fixed Message-ID assists provider deduplication but
SMTP cannot guarantee exactly-once delivery following an ambiguous disconnect.
"""

import argparse
import json
import logging
import signal
import smtplib
import ssl
import threading
from datetime import timedelta
from email.message import EmailMessage
from typing import Any, Literal
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import or_, select

from money.accounts.models import email_outbox, users
from money.api.observability import configure_logging
from money.api.settings import OperatorSettings
from money.storage.store import ResearchStore, now_utc

logger = logging.getLogger(__name__)


class EmailSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    money_email_encryption_key: SecretStr
    money_smtp_host: str = Field(min_length=1, max_length=253)
    money_smtp_port: int = Field(default=465, ge=1, le=65535)
    money_smtp_tls: Literal["implicit", "starttls"] = "implicit"
    money_smtp_username: str = Field(min_length=1, max_length=254)
    money_smtp_password: SecretStr
    money_email_from: str = Field(pattern=r"^[^\r\n@ ]+@[^\r\n@ ]+\.[^\r\n@ ]+$")


class EmailWorker:
    def __init__(self, store: ResearchStore, settings: EmailSettings) -> None:
        self.store, self.settings = store, settings
        self.cipher = Fernet(settings.money_email_encryption_key.get_secret_value().encode())

    def claim(self) -> dict[str, Any] | None:
        with self.store.transaction() as connection:
            now = now_utc()
            # Closure suppresses retries and queued mail. An already in-flight
            # SMTP delivery cannot be unsent; erasure waits for its lease.
            connection.execute(
                email_outbox.update()
                .where(
                    email_outbox.c.user_id.in_(
                        select(users.c.id).where(users.c.deletion_requested_at.is_not(None))
                    ),
                    or_(
                        email_outbox.c.state == "QUEUED",
                        (email_outbox.c.state == "SENDING") & (email_outbox.c.lease_until <= now),
                    ),
                )
                .values(
                    state="CANCELLED",
                    encrypted_payload="",
                    lease_token=None,
                    lease_until=None,
                    failure_code="ACCOUNT_CLOSING",
                )
            )
            # A crash on the final attempt becomes final failure, not a stuck lease.
            connection.execute(
                email_outbox.update()
                .where(
                    email_outbox.c.state == "SENDING",
                    email_outbox.c.lease_until <= now,
                    email_outbox.c.attempts >= 5,
                )
                .values(state="FAILED", failure_code="EMAIL_RETRY_EXHAUSTED", lease_token=None)
            )
            row = (
                connection.execute(
                    select(email_outbox)
                    .where(
                        or_(
                            email_outbox.c.state == "QUEUED",
                            (email_outbox.c.state == "SENDING")
                            & (email_outbox.c.lease_until <= now),
                        ),
                        email_outbox.c.available_at <= now,
                        email_outbox.c.attempts < 5,
                    )
                    .order_by(email_outbox.c.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            lease = str(uuid4())
            connection.execute(
                email_outbox.update()
                .where(email_outbox.c.id == row["id"])
                .values(
                    state="SENDING",
                    attempts=row["attempts"] + 1,
                    lease_token=lease,
                    lease_until=now + timedelta(minutes=2),
                )
            )
            return {**row, "lease_token": lease, "attempts": row["attempts"] + 1}

    def deliver(self, row: dict[str, Any]) -> None:
        with self.store.engine.connect() as connection:
            if connection.scalar(
                select(users.c.deletion_requested_at).where(users.c.id == row["user_id"])
            ):
                raise ValueError("ACCOUNT_CLOSING")
        payload = json.loads(self.cipher.decrypt(row["encrypted_payload"].encode()))
        if set(payload) != {"to", "subject", "body"}:
            raise ValueError("EMAIL_PAYLOAD_INVALID")
        message = EmailMessage()
        message["From"] = self.settings.money_email_from
        message["To"] = payload["to"]
        message["Subject"] = payload["subject"]
        message["Message-ID"] = f"<{row['id']}@{self.settings.money_email_from.split('@')[1]}>"
        message.set_content(payload["body"])
        context = ssl.create_default_context()
        if self.settings.money_smtp_tls == "implicit":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.settings.money_smtp_host,
                self.settings.money_smtp_port,
                timeout=20,
                context=context,
            )
        else:
            client = smtplib.SMTP(
                self.settings.money_smtp_host, self.settings.money_smtp_port, timeout=20
            )
        with client:
            if self.settings.money_smtp_tls == "starttls":
                client.starttls(context=context)
            client.login(
                self.settings.money_smtp_username,
                self.settings.money_smtp_password.get_secret_value(),
            )
            client.send_message(message)

    def once(self) -> bool:
        row = self.claim()
        if row is None:
            return False
        failure: str | None = None
        permanent = False
        try:
            self.deliver(row)
        except smtplib.SMTPResponseException as error:
            failure, permanent = "EMAIL_PROVIDER_REJECTED", error.smtp_code >= 500
        except (smtplib.SMTPRecipientsRefused, ValueError, InvalidToken):
            failure, permanent = "EMAIL_PAYLOAD_REJECTED", True
        except Exception as error:  # noqa: BLE001 - never log decrypted email or provider response
            failure = "EMAIL_DELIVERY_FAILED"
            logger.warning("email_delivery_failed", extra={"failure_class": type(error).__name__})
        with self.store.transaction() as connection:
            values: dict[str, Any] = {
                "lease_token": None,
                "lease_until": None,
                "failure_code": failure,
            }
            if failure is None:
                values.update(state="DELIVERED", delivered_at=now_utc(), encrypted_payload="")
            else:
                values.update(
                    state="FAILED" if permanent or row["attempts"] >= 5 else "QUEUED",
                    available_at=now_utc()
                    + timedelta(seconds=min(3600, 30 * 2 ** row["attempts"])),
                )
            connection.execute(
                email_outbox.update()
                .where(
                    email_outbox.c.id == row["id"],
                    email_outbox.c.lease_token == row["lease_token"],
                    email_outbox.c.lease_until > now_utc(),
                )
                .values(**values)
            )
        return True


def healthcheck() -> int:
    """Read-only service readiness; no SMTP secrets, lease claims or delivery."""
    store: ResearchStore | None = None
    status = 1
    try:
        operator = OperatorSettings()  # type: ignore[call-arg]
        store = ResearchStore(
            operator.database_url.get_secret_value(),
            allow_sqlite=operator.money_env in {"test", "development"},
            pool_timeout=5,
            statement_timeout_ms=5000,
        )
        status = 0 if store.health("transactional-email")["status"] == "ok" else 1
    except Exception as error:  # noqa: BLE001 - no driver response or configuration values in logs
        logger.warning("email_healthcheck_failed", extra={"failure_class": type(error).__name__})
    finally:
        if store is not None:
            try:
                store.engine.dispose()
            except Exception as error:  # noqa: BLE001 - driver cleanup errors may contain secrets
                logger.warning(
                    "email_healthcheck_cleanup_failed",
                    extra={"failure_class": type(error).__name__},
                )
                status = 1
    return status


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Money transactional email worker")
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Check the durable email-worker heartbeat without sending email",
    )
    args = parser.parse_args()
    if args.healthcheck:
        raise SystemExit(healthcheck())
    operator = OperatorSettings()  # type: ignore[call-arg]
    settings = EmailSettings()  # type: ignore[call-arg]
    store = ResearchStore(
        operator.database_url.get_secret_value(),
        allow_sqlite=operator.money_env in {"test", "development"},
    )
    worker = EmailWorker(store, settings)
    worker_id = f"email-{uuid4().hex}"
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    try:
        while not stopping.is_set():
            try:
                worked = worker.once()
                store.heartbeat(worker_id, "transactional-email")
            except Exception as error:  # noqa: BLE001 - safe operational correlation only
                logger.error("email_worker_failed", extra={"failure_class": type(error).__name__})
                worked = False
            if not worked:
                stopping.wait(2)
    finally:
        try:
            store.heartbeat(worker_id, "transactional-email", healthy=False)
        except Exception as error:  # noqa: BLE001 - shutdown must dispose even during DB failure
            logger.warning("email_shutdown_failed", extra={"failure_class": type(error).__name__})
        finally:
            store.engine.dispose()


if __name__ == "__main__":
    main()
