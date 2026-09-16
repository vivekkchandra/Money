"""TTL cache and request coalescing for public R&D responses, never Git state."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from money.data.resilience import ProviderCircuit
from money.data.security import ProviderFailure
from money.schemas.contracts import content_hash
from money.storage import ResearchStore
from money.storage.models import queue_control
from money.storage.production_models import provider_state
from money.storage.store import aware, now_utc


class RndProviderCache:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def get(
        self, provider: str, request: str, operation: Callable[[], dict[str, Any]],
        *, ttl_seconds: int, lease_seconds: int = 120,
    ) -> dict[str, Any]:
        if not 1 <= ttl_seconds <= 86400 or not 5 <= lease_seconds <= 300:
            raise ValueError("Invalid R&D cache bounds")
        key = "rnd-cache:" + content_hash({"provider": provider, "request": request})
        token, now = uuid4().hex, now_utc()
        with self.store.transaction() as connection:
            connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
            row = connection.execute(select(provider_state).where(
                provider_state.c.id == key,
            )).mappings().first()
            if row and row["open_until"] and aware(row["open_until"]) > now:
                if row["payload"].get("state") == "READY":
                    data = row["payload"]["data"]
                    if content_hash(data) != row["payload"].get("hash"):
                        raise ProviderFailure("RND_CACHE_CORRUPT")
                    # Do not replace retrieval time or make a cached quote appear fresh.
                    return dict(data)
                raise ProviderFailure("PROVIDER_BUSY", retryable=True)
            values = dict(
                failures=0, open_until=now + timedelta(seconds=lease_seconds),
                payload={"state": "FETCHING", "token": token}, updated_at=now,
            )
            if row:
                connection.execute(provider_state.update().where(
                    provider_state.c.id == key,
                ).values(**values))
            else:
                connection.execute(provider_state.insert().values(id=key, **values))
        try:
            limiter = self.store.for_workspace("rnd-public-providers")
            if not limiter.consume_rate_limit(provider, 20, 60)["allowed"]:
                raise ProviderFailure("PROVIDER_RATE_LIMITED", retryable=True)
            data = ProviderCircuit(self.store).call("rnd-" + provider, operation)
            if len(json.dumps(data, allow_nan=False)) > 5_000_000:
                raise ProviderFailure("PROVIDER_RESPONSE_TOO_LARGE")
        except BaseException:
            self._finish(key, token, None, 15)
            raise
        self._finish(key, token, data, ttl_seconds)
        return data

    def _finish(
        self, key: str, token: str, data: dict[str, Any] | None, ttl: int,
    ) -> None:
        now = now_utc()
        with self.store.transaction() as connection:
            row = connection.execute(select(provider_state).where(
                provider_state.c.id == key,
            ).with_for_update()).mappings().one()
            if row["payload"].get("token") != token:
                return
            connection.execute(provider_state.update().where(provider_state.c.id == key).values(
                payload={"state": "READY", "data": data, "hash": content_hash(data)}
                if data is not None else {"state": "COOLDOWN"},
                open_until=now + timedelta(seconds=ttl), updated_at=now,
            ))


def provider_diagnostics(store: ResearchStore) -> list[dict[str, Any]]:
    """Read past probes only. Health reads must never trigger provider work."""
    providers = {
        "yfinance": 300, "sec-edgar": 86400, "companies-house": 86400,
        "bank-of-england": 86400, "ons": 86400, "fred": 86400,
    }
    with store.engine.connect() as connection:
        rows = {row["id"]: row for row in connection.execute(select(provider_state).where(
            provider_state.c.id.in_(["rnd-" + name for name in providers]),
        )).mappings()}
    result = []
    for provider, maximum_age in providers.items():
        row = rows.get("rnd-" + provider)
        status, checked_at = "NOT_CONFIGURED", None
        if row is not None:
            checked_at = aware(row["updated_at"])
            fresh = checked_at >= now_utc() - timedelta(seconds=maximum_age)
            status = "READY" if row["payload"].get("last_success") and fresh else "UNAVAILABLE"
        result.append({
            "provider": provider, "status": status,
            "checked_at": checked_at.isoformat() if checked_at else None,
            "scope": "PERSONAL_RND_ONLY",
            "reason": "Last bounded data fetch" if checked_at else "No successful probe recorded",
        })
    return result
