"""Database-coordinated provider circuit, with bounded half-open probes."""

from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from typing import TypeVar

from sqlalchemy import select

from money.data.security import ProviderFailure
from money.storage import ResearchStore
from money.storage.models import queue_control
from money.storage.production_models import provider_state
from money.storage.store import aware, now_utc

T = TypeVar("T")


class ProviderCircuit:
    def __init__(
        self, store: ResearchStore, *, threshold: int = 3, cooldown_seconds: int = 60
    ) -> None:
        if not 1 <= threshold <= 10 or not 10 <= cooldown_seconds <= 600:
            raise ValueError("invalid circuit limits")
        self.store, self.threshold, self.cooldown = store, threshold, cooldown_seconds

    def call(self, provider: str, operation: Callable[[], T]) -> T:
        now = now_utc()
        with self.store.transaction() as connection:
            connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
            row = (
                connection.execute(select(provider_state).where(provider_state.c.id == provider))
                .mappings()
                .first()
            )
            if row is None:
                generation = 0
                connection.execute(
                    provider_state.insert().values(
                        id=provider,
                        failures=0,
                        payload={"calls": 0, "failures": 0, "generation": 0},
                        updated_at=now,
                    )
                )
            elif row["open_until"] is not None:
                if aware(row["open_until"]) > now:
                    raise ProviderFailure("PROVIDER_CIRCUIT_OPEN", retryable=True)
                # A single probe owns this bounded interval across all workers.
                generation = row["payload"].get("generation", 0) + 1
                connection.execute(
                    provider_state.update()
                    .where(provider_state.c.id == provider)
                    .values(
                        open_until=now + timedelta(seconds=self.cooldown),
                        payload=dict(row["payload"], generation=generation),
                    )
                )
            else:
                generation = row["payload"].get("generation", 0)
        started = monotonic()
        try:
            result = operation()
        except Exception:
            self._record(provider, False, monotonic() - started, generation)
            raise
        self._record(provider, True, monotonic() - started, generation)
        return result

    def _record(self, provider: str, success: bool, duration: float, generation: int) -> None:
        now = now_utc()
        with self.store.transaction() as connection:
            connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
            row = (
                connection.execute(select(provider_state).where(provider_state.c.id == provider))
                .mappings()
                .one()
            )
            metrics = row["payload"]
            current_generation = metrics.get("generation", 0)
            failures, open_until = row["failures"], row["open_until"]
            if current_generation == generation:
                failures = 0 if success else failures + 1
                open_until = (
                    now + timedelta(seconds=self.cooldown) if failures >= self.threshold else None
                )
                if open_until is not None or row["open_until"] is not None:
                    current_generation += 1
            connection.execute(
                provider_state.update()
                .where(provider_state.c.id == provider)
                .values(
                    failures=failures,
                    open_until=open_until,
                    updated_at=now,
                    payload={
                        "calls": metrics.get("calls", 0) + 1,
                        "failures": metrics.get("failures", 0) + int(not success),
                        "last_duration_seconds": duration,
                        "last_success": success,
                        "generation": current_generation,
                    },
                )
            )
