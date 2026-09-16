"""Fenced, append-only call accounting, separate from research opinions.

Native invocations and gateway attempts are different measurements. Their
duration is not summed together and native aggregate usage is not counted again
when individual gateway receipts exist. Missing usage/cost stays unknown.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    and_,
    case,
    exists,
    func,
    select,
)

from money.product.models import usage_records
from money.research.call_telemetry import InferenceReceipt, capture_calls
from money.schemas.contracts import Usage, content_hash, utc_now
from money.storage import ResearchStore
from money.storage.models import metadata
from money.storage.production_models import budget_reservations
from money.storage.store import StoreError

call_metering = Table(
    "call_metering",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("invocation_id", String(64), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("job_id", ForeignKey("research_jobs.id"), nullable=False),
    Column("workspace_id", String(128), nullable=False),
    Column("user_id", String(36)),
    Column("period_start", DateTime(timezone=True)),
    Column("period_end", DateTime(timezone=True)),
    Column("reservation_id", ForeignKey("budget_reservations.id")),
    Column("lease_token", String(36)),
    Column("attempt", Integer, nullable=False),
    Column("kind", String(32), nullable=False),
    Column("component", String(128), nullable=False),
    Column("provider", String(128), nullable=False),
    Column("model", String(200)),
    Column("stage", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("provider_calls", Integer),
    Column("duration_ms", Integer),
    Column("actual_cost", Numeric(24, 12)),
    Column("estimated_cost", Numeric(24, 12)),
    Column("currency", String(3)),
    Column("cache_hit", Boolean),
    Column("retry", Boolean, nullable=False),
    Column("error_code", String(64)),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("invocation_id", "sequence", name="uq_call_invocation_sequence"),
)
Index("ix_call_metering_workspace_time", call_metering.c.workspace_id, call_metering.c.created_at)
Index("ix_call_metering_job", call_metering.c.job_id)
Index("ix_call_metering_reservation", call_metering.c.reservation_id, call_metering.c.kind)


def _ownership(connection: Connection, job: Any) -> dict[str, Any]:
    admission = (
        connection.execute(select(usage_records).where(usage_records.c.job_id == job["id"]))
        .mappings()
        .first()
    )
    if admission is not None and admission["workspace_id"] != job["workspace_id"]:
        raise StoreError("CALL_ADMISSION_OWNERSHIP_MISMATCH")
    return {
        "job_id": job["id"],
        "workspace_id": job["workspace_id"],
        "user_id": admission["user_id"] if admission else None,
        "period_start": admission["period_start"] if admission else None,
        "period_end": admission["period_end"] if admission else None,
        "lease_token": job["lease_token"],
        "attempt": job["attempt_count"],
        "stage": job["current_stage"],
    }


def _insert(connection: Connection, values: dict[str, Any]) -> None:
    # The owning job's row lock serializes duplicate receipts on PostgreSQL.
    identity = content_hash({"invocation": values["invocation_id"], "sequence": values["sequence"]})
    fingerprint = content_hash(
        {
            key: value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, Decimal)
            else value
            for key, value in values.items()
            if key != "created_at"
        }
    )
    previous = connection.scalar(
        select(call_metering.c.content_hash).where(call_metering.c.id == identity)
    )
    if previous is not None:
        if previous != fingerprint:
            raise ValueError("CALL_METERING_IDEMPOTENCY_CONFLICT")
        return
    connection.execute(
        call_metering.insert().values(
            id=identity,
            content_hash=fingerprint,
            created_at=utc_now(),
            **values,
        )
    )


class CallMeter:
    """Trusted orchestrator wrapper; never supplied to first-pass firm inputs."""

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def invoke_reserved[T](self, reservation_id: str, operation: Callable[[], T]) -> T:
        """Use the already-approved identity; callers cannot change provider/model."""
        with self.store.engine.connect() as connection:
            row = (
                connection.execute(
                    select(budget_reservations).where(
                        budget_reservations.c.id == reservation_id,
                    )
                )
                .mappings()
                .one()
            )
        return self.invoke(
            row["job_id"],
            component=row["agent"],
            provider=row["provider"],
            model=row["model"],
            operation=operation,
            reservation_id=reservation_id,
        )

    def invoke[T](
        self,
        job_id: str,
        *,
        component: str,
        provider: str,
        model: str | None,
        operation: Callable[[], T],
        reservation_id: str | None = None,
        kind: Literal["NATIVE_INVOCATION", "PROVIDER_OPERATION"] = "NATIVE_INVOCATION",
    ) -> T:
        """Persist a start before work; retain an unknown orphan after a hard crash."""
        invocation_id = (
            content_hash({"reservation": reservation_id}) if reservation_id else uuid4().hex
        )
        with self.store.transaction() as connection:
            job = self.store._owned_job(connection, job_id)
            if (
                self.store.workspace_id is not None
                and self.store.workspace_id != job["workspace_id"]
            ):
                raise StoreError("RESOURCE_NOT_FOUND")
            if reservation_id is not None:
                reservation = (
                    connection.execute(
                        select(budget_reservations).where(
                            budget_reservations.c.id == reservation_id,
                            budget_reservations.c.job_id == job_id,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    reservation is None
                    or reservation["provider"] != provider
                    or reservation["model"] != model
                    or reservation["agent"] != component
                ):
                    raise ValueError("CALL_BUDGET_IDENTITY_MISMATCH")
                if reservation["payload"]["usage"] is not None:
                    raise ValueError("CALL_BUDGET_ALREADY_SETTLED")
            if connection.scalar(
                select(call_metering.c.id)
                .where(
                    call_metering.c.invocation_id == invocation_id,
                )
                .limit(1)
            ):
                raise ValueError("CALL_INVOCATION_ALREADY_STARTED")
            identity = {
                **_ownership(connection, job),
                "invocation_id": invocation_id,
                "reservation_id": reservation_id,
                "component": component,
                "provider": provider,
                "model": model,
            }
            _insert(
                connection,
                {
                    **identity,
                    "sequence": 0,
                    "kind": kind,
                    "status": "STARTED",
                    "retry": job["attempt_count"] > 1,
                },
            )
        started = monotonic()
        success = False
        with capture_calls() as receipts:
            try:
                result = operation()
                success = True
                return result
            finally:
                # If fencing was lost, do not let an old worker rewrite new usage.
                # The durable start remains explicitly unresolved, not zero-cost.
                with self.store.transaction() as connection:
                    self.store._owned_job(connection, job_id)
                    for index, receipt in enumerate(receipts, start=1):
                        if receipt.provider != provider or receipt.model != model:
                            raise ValueError("CALL_PROVIDER_IDENTITY_MISMATCH")
                        _insert(
                            connection,
                            {
                                **identity,
                                "sequence": index,
                                "kind": "INFERENCE_CALL",
                                **self._receipt_values(receipt),
                                "retry": receipt.retry or job["attempt_count"] > 1,
                            },
                        )
                    _insert(
                        connection,
                        {
                            **identity,
                            "sequence": 257,
                            "kind": kind,
                            "status": "SUCCEEDED" if success else "FAILED",
                            "duration_ms": int((monotonic() - started) * 1000),
                            "retry": job["attempt_count"] > 1,
                            "error_code": None if success else "COMPONENT_FAILED",
                        },
                    )

    @staticmethod
    def _receipt_values(receipt: InferenceReceipt) -> dict[str, Any]:
        return {
            "status": receipt.status,
            "input_tokens": receipt.input_tokens,
            "output_tokens": receipt.output_tokens,
            "provider_calls": receipt.provider_calls,
            "duration_ms": int(receipt.duration_seconds * 1000),
            "actual_cost": receipt.actual_cost,
            "estimated_cost": receipt.estimated_cost,
            "currency": receipt.currency,
            "cache_hit": receipt.cache_hit,
            "error_code": receipt.error_code,
        }


def record_reported_usage(connection: Connection, reservation: Any, usage: Usage, job: Any) -> None:
    """Recover aggregate usage if no individual receipt survived; never invent calls."""
    if connection.scalar(
        select(call_metering.c.id)
        .where(
            call_metering.c.reservation_id == reservation["id"],
            call_metering.c.kind == "INFERENCE_CALL",
        )
        .limit(1)
    ):
        return
    identity = content_hash({"aggregate_reservation": reservation["id"]})
    _insert(
        connection,
        {
            **_ownership(connection, job),
            "invocation_id": identity,
            "sequence": 0,
            "reservation_id": reservation["id"],
            "kind": "REPORTED_USAGE",
            "component": reservation["agent"],
            "provider": reservation["provider"],
            "model": reservation["model"],
            "stage": reservation["stage"],
            "status": "SUCCEEDED",
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            # Existing Usage.cost_gbp can be computed from rate configuration. Without
            # a provider invoice contract it is only an estimate, never actual cost.
            "estimated_cost": usage.cost_gbp,
            "actual_cost": None,
            "currency": "GBP" if usage.cost_gbp is not None else None,
            "retry": job["attempt_count"] > 1,
        },
    )


def admin_call_summary(store: ResearchStore, *, days: int = 30, limit: int = 100) -> dict[str, Any]:
    """Bounded admin-only aggregates; authorization remains at the API boundary.

    Costs are grouped by currency, never converted or added across currencies.
    Partial-known subtotals include explicit unknown counts and are not invoices.
    """
    if not 1 <= days <= 366 or not 1 <= limit <= 200:
        raise ValueError("CALL_SUMMARY_WINDOW_INVALID")
    table = call_metering
    since = utc_now() - timedelta(days=days)
    predicates: list[Any] = [table.c.created_at >= since, table.c.status != "STARTED"]
    if store.workspace_id is not None:
        predicates.append(table.c.workspace_id == store.workspace_id)
    dimensions = [
        table.c.kind,
        table.c.component,
        table.c.provider,
        table.c.model,
        table.c.currency,
    ]
    query = (
        select(
            *dimensions,
            func.count().label("records"),
            func.sum(case((table.c.status == "FAILED", 1), else_=0)).label("failures"),
            func.sum(case((table.c.retry.is_(True), 1), else_=0)).label("retries"),
            func.sum(case((table.c.cache_hit.is_(True), 1), else_=0)).label("cache_hits"),
            func.sum(table.c.provider_calls).label("provider_calls"),
            func.sum(table.c.duration_ms).label("duration_ms"),
            func.sum(table.c.input_tokens).label("input_tokens"),
            func.sum(table.c.output_tokens).label("output_tokens"),
            func.sum(table.c.actual_cost).label("actual_cost_subtotal"),
            func.sum(table.c.estimated_cost).label("estimated_cost_subtotal"),
            func.sum(case((table.c.actual_cost.is_(None), 1), else_=0)).label(
                "unknown_actual_cost_records"
            ),
            func.sum(case((table.c.input_tokens.is_(None), 1), else_=0)).label(
                "unknown_input_token_records"
            ),
            func.sum(case((table.c.output_tokens.is_(None), 1), else_=0)).label(
                "unknown_output_token_records"
            ),
            func.sum(case((table.c.provider_calls.is_(None), 1), else_=0)).label(
                "unknown_provider_call_records"
            ),
        )
        .where(*predicates)
        .group_by(*dimensions)
        .order_by(*dimensions)
        .limit(limit + 1)
    )
    finished = table.alias("finished")
    unfinished = (
        select(func.count())
        .select_from(table)
        .where(
            table.c.created_at >= since,
            table.c.status == "STARTED",
            ~exists(
                select(finished.c.id).where(
                    and_(
                        finished.c.invocation_id == table.c.invocation_id,
                        finished.c.kind == table.c.kind,
                        finished.c.status != "STARTED",
                    )
                )
            ),
        )
    )
    if store.workspace_id is not None:
        unfinished = unfinished.where(table.c.workspace_id == store.workspace_id)
    with store.engine.connect() as connection:
        rows = list(connection.execute(query).mappings())
        pending = int(connection.scalar(unfinished) or 0)
    return {
        "days": days,
        "truncated": len(rows) > limit,
        "unfinished_invocations": pending,
        "groups": [
            {key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()}
            for row in rows[:limit]
        ],
        "cost_policy": "ACTUAL_UNKNOWN_UNLESS_REPORTED; ESTIMATES_ARE_NOT_INVOICES",
        "measurement_policy": "NATIVE_DURATION_AND_INFERENCE_DURATION_OVERLAP; DO_NOT_SUM_KINDS",
    }
