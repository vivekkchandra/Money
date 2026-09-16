"""Informational notifications with durable, idempotent delivery bookkeeping."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import select

from money.storage import ResearchStore
from money.storage.production_models import alert_outbox
from money.storage.store import StoreError, aware, digest, now_utc, serialize

Event = Literal["RESEARCH_COMPLETED", "STATE_CHANGED", "SIGNAL_EXPIRED", "MATERIAL_INVALIDATION"]
MESSAGES = {
    "RESEARCH_COMPLETED": "Research is complete and available for human review.",
    "STATE_CHANGED": "The research state has changed; review the recorded evidence.",
    "SIGNAL_EXPIRED": "The research setup has expired.",
    "MATERIAL_INVALIDATION": "New evidence has invalidated a research assumption.",
}


class AlertProvider(Protocol):
    supports_idempotency: bool

    def deliver(
        self, message: dict[str, Any], *, idempotency_key: str, timeout_seconds: int
    ) -> None: ...


class AlertOutbox:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def enqueue(
        self,
        job_id: str,
        event: Event,
        *,
        event_key: str,
        channel: Literal["web", "email", "telegram"] = "web",
    ) -> str:
        job = self.store.get_job(job_id)
        if job is None or event not in MESSAGES or not event_key or len(event_key) > 200:
            raise StoreError("Alert requires an accessible research event")
        if event == "RESEARCH_COMPLETED" and job["status"] not in {"COMPLETE", "REJECTED"}:
            raise StoreError("Completion alert requires completed research")
        alert_id = digest({"job": job_id, "event": event, "key": event_key, "channel": channel})
        with self.store.transaction() as connection:
            # Enqueue uses the existing serialization lock to prevent duplicate races.
            from money.storage.models import queue_control

            connection.execute(select(queue_control).with_for_update()).all()
            if connection.scalar(select(alert_outbox.c.id).where(alert_outbox.c.id == alert_id)):
                return alert_id
            connection.execute(
                alert_outbox.insert().values(
                    id=alert_id,
                    job_id=job_id,
                    workspace_id=job["workspace_id"],
                    channel=channel,
                    state="DELIVERED" if channel == "web" else "PENDING",
                    attempts=0,
                    created_at=now_utc(),
                    delivered_at=now_utc() if channel == "web" else None,
                    payload={
                        "event": event,
                        "message": MESSAGES[event],
                        "research_id": job_id,
                        "informational_only": True,
                    },
                )
            )
        return alert_id

    def deliver_one(self, providers: dict[str, AlertProvider] | None = None) -> bool:
        providers = providers or {}
        stamp = now_utc()
        with self.store.transaction() as connection:
            query = select(alert_outbox).where(
                alert_outbox.c.state.in_(["PENDING", "RETRY", "DELIVERING"])
            )
            if self.store.workspace_id:
                query = query.where(alert_outbox.c.workspace_id == self.store.workspace_id)
            rows = connection.execute(
                query.order_by(alert_outbox.c.created_at)
                .with_for_update(skip_locked=True)
                .limit(100)
            ).mappings()
            row = None
            for candidate in rows:
                lease = candidate["payload"].get("delivery_lease_until")
                if candidate["state"] != "DELIVERING" or (
                    lease and aware(datetime.fromisoformat(lease)) <= stamp
                ):
                    row = candidate
                    break
            if row is None:
                return False
            provider = providers.get(row["channel"])
            if row["channel"] != "web" and (provider is None or not provider.supports_idempotency):
                # Missing configuration remains visibly blocked, never pretends delivery.
                connection.execute(
                    alert_outbox.update()
                    .where(alert_outbox.c.id == row["id"])
                    .values(state="BLOCKED_CONFIGURATION")
                )
                return True
            if row["attempts"] >= 3:
                connection.execute(
                    alert_outbox.update()
                    .where(alert_outbox.c.id == row["id"])
                    .values(state="FAILED")
                )
                return True
            token = str(uuid4())
            data = {
                **row["payload"],
                "delivery_token": token,
                "delivery_lease_until": (stamp + timedelta(seconds=60)).isoformat(),
            }
            connection.execute(
                alert_outbox.update()
                .where(alert_outbox.c.id == row["id"])
                .values(
                    state="DELIVERING",
                    attempts=row["attempts"] + 1,
                    payload=data,
                )
            )
            alert_id = row["id"]
        try:
            if provider is not None:
                provider.deliver(
                    {key: value for key, value in data.items() if not key.startswith("delivery_")},
                    idempotency_key=alert_id,
                    timeout_seconds=20,
                )
            delivered = True
        except Exception:  # noqa: BLE001 - no provider response/credential persists
            delivered = False
        with self.store.transaction() as connection:
            current = (
                connection.execute(
                    select(alert_outbox).where(alert_outbox.c.id == alert_id).with_for_update()
                )
                .mappings()
                .one()
            )
            if current["payload"].get("delivery_token") != token:
                return True  # A newer delivery claim owns the result.
            connection.execute(
                alert_outbox.update()
                .where(alert_outbox.c.id == alert_id)
                .values(
                    state="DELIVERED" if delivered else "RETRY",
                    delivered_at=now_utc() if delivered else None,
                )
            )
        return True

    def list_web(self) -> list[dict[str, Any]]:
        workspace = self.store.workspace_id or "private"
        with self.store.engine.connect() as connection:
            result = []
            for row in connection.execute(
                select(alert_outbox)
                .where(
                    alert_outbox.c.workspace_id == workspace,
                    alert_outbox.c.channel == "web",
                    alert_outbox.c.state == "DELIVERED",
                )
                .order_by(alert_outbox.c.created_at.desc())
                .limit(100)
            ).mappings():
                item = serialize(row)
                item["payload"] = {
                    key: value
                    for key, value in item["payload"].items()
                    if not key.startswith("delivery_")
                }
                result.append(item)
            return result
