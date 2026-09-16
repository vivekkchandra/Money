"""Durable pessimistic token admission; unknown usage/cost never becomes zero."""

from datetime import UTC, datetime
from typing import Any

from pydantic import Field
from sqlalchemy import Connection, func, select

from money.schemas.contracts import Contract, Usage, utc_now
from money.storage import ResearchStore
from money.storage.models import artifacts, firm_reports, jobs, queue_control
from money.storage.production_models import budget_reservations as reservations


class BudgetLimits(Contract):
    per_job: int = Field(default=100000, gt=0)
    per_candidate: int = Field(default=100000, gt=0)
    per_stage: int = Field(default=60000, gt=0)
    per_agent: int = Field(default=30000, gt=0)
    daily: int = Field(default=200000, gt=0)
    per_model: tuple[tuple[str, int], ...] = ()


class BudgetReservation(Contract):
    reservation_id: str = Field(min_length=1, max_length=128)
    job_id: str
    stage: str
    agent: str
    provider: str
    model: str
    maximum_tokens: int = Field(gt=0, strict=True)
    prompt_version: str


class TokenBudgetManager:
    def __init__(self, store: ResearchStore, limits: BudgetLimits) -> None:
        self.store, self.limits = store, limits

    def reserve(
        self,
        *,
        reservation_id: str,
        job_id: str,
        stage: str,
        agent: str,
        provider: str,
        model: str,
        maximum_tokens: int,
        prompt_version: str,
    ) -> None:
        self.reserve_many(
            (
                BudgetReservation(
                    reservation_id=reservation_id,
                    job_id=job_id,
                    stage=stage,
                    agent=agent,
                    provider=provider,
                    model=model,
                    maximum_tokens=maximum_tokens,
                    prompt_version=prompt_version,
                ),
            )
        )

    def _job(self, connection: Connection, job_id: str) -> Any:
        job = (
            self.store._owned_job(connection, job_id)
            if self.store.claim is not None
            else connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
        )
        if self.store.workspace_id is not None and job["workspace_id"] != self.store.workspace_id:
            raise ValueError("RESOURCE_NOT_FOUND")
        return job

    def reserve_many(self, requests: tuple[BudgetReservation, ...]) -> None:
        """Admit one job's whole stage or commit nothing, before starting paid calls."""
        if not requests:
            return
        if len({request.job_id for request in requests}) != 1:
            raise ValueError("BUDGET_BATCH_JOB_MISMATCH")
        now = utc_now()
        with self.store.transaction() as connection:
            # Same serialization row as enqueue; admission remains atomic across workers.
            connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
            job = self._job(connection, requests[0].job_id)
            for request in requests:
                self._reserve(connection, request, job, now)

    def _reserve(
        self, connection: Connection, request: BudgetReservation, job: Any, now: datetime
    ) -> None:
        reservation_id, job_id = request.reservation_id, request.job_id
        stage, agent = request.stage, request.agent
        provider, model = request.provider, request.model
        maximum_tokens, prompt_version = request.maximum_tokens, request.prompt_version
        if maximum_tokens > self.limits.per_job:
            raise ValueError("TOKEN_BUDGET_EXCEEDED")
        existing = (
            connection.execute(select(reservations).where(reservations.c.id == reservation_id))
            .mappings()
            .first()
        )
        identity = dict(
            job_id=job_id,
            workspace_id=job["workspace_id"],
            candidate=job["ticker"],
            stage=stage,
            agent=agent,
            provider=provider,
            model=model,
            reserved_tokens=maximum_tokens,
        )
        if existing:
            if any(existing[key] != value for key, value in identity.items()) or (
                existing["payload"]["prompt_version"] != prompt_version
            ):
                raise ValueError("BUDGET_IDEMPOTENCY_CONFLICT")
            # Reusing an invocation identity must never initiate a second paid call.
            raise ValueError("BUDGET_INVOCATION_ALREADY_RESERVED")
        charged = func.coalesce(reservations.c.actual_tokens, reservations.c.reserved_tokens)
        scopes: list[tuple[Any, int]] = [
            (reservations.c.job_id == job_id, self.limits.per_job),
            (
                (reservations.c.job_id == job_id) & (reservations.c.candidate == job["ticker"]),
                self.limits.per_candidate,
            ),
            (
                (reservations.c.job_id == job_id) & (reservations.c.stage == stage),
                self.limits.per_stage,
            ),
            (
                (reservations.c.job_id == job_id) & (reservations.c.agent == agent),
                self.limits.per_agent,
            ),
            (
                (reservations.c.workspace_id == job["workspace_id"])
                & (
                    reservations.c.created_at
                    >= datetime.combine(now.date(), datetime.min.time(), UTC)
                ),
                self.limits.daily,
            ),
        ]
        model_limit = dict(self.limits.per_model).get(f"{provider}/{model}")
        if model_limit is not None:
            scopes.append(
                (
                    (reservations.c.job_id == job_id)
                    & (reservations.c.provider == provider)
                    & (reservations.c.model == model),
                    model_limit,
                )
            )
        for predicate, limit in scopes:
            used = connection.scalar(select(func.coalesce(func.sum(charged), 0)).where(predicate))
            if int(used or 0) + maximum_tokens > limit:
                raise ValueError("TOKEN_BUDGET_EXCEEDED")
        connection.execute(
            reservations.insert().values(
                id=reservation_id,
                **identity,
                payload={
                    "prompt_version": prompt_version,
                    "usage": None,
                    "cost_status": "unknown",
                },
                created_at=now,
            )
        )

    def settle(self, reservation_id: str, usage: Usage) -> None:
        with self.store.transaction() as connection:
            connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
            row = (
                connection.execute(select(reservations).where(reservations.c.id == reservation_id))
                .mappings()
                .one()
            )
            self._job(connection, row["job_id"])
            self._settle(connection, row, usage)

    def reconcile_job(self, job_id: str) -> None:
        """Recover usage after a crash between sealing a result and settling its charge.

        Only usage fields are read: this never exposes unsealed peer report contents.
        Earlier failed invocations remain charged at their pessimistic reservation.
        """
        with self.store.transaction() as connection:
            connection.execute(queue_control.update().where(queue_control.c.id == 1).values(id=1))
            self._job(connection, job_id)
            sealed_usage = {
                row.firm: Usage.model_validate(row.usage or {})
                for row in connection.execute(
                    select(
                        firm_reports.c.firm, firm_reports.c.payload["usage"].label("usage")
                    ).where(firm_reports.c.job_id == job_id)
                )
            }
            cio_usage = connection.execute(
                select(artifacts.c.payload["usage"]).where(
                    (artifacts.c.job_id == job_id) & (artifacts.c.kind == "cio_runtime")
                )
            ).first()
            if cio_usage is not None:
                sealed_usage["crewai"] = Usage.model_validate(cio_usage[0] or {})
            for agent, usage in sealed_usage.items():
                row = (
                    connection.execute(
                        select(reservations)
                        .where((reservations.c.job_id == job_id) & (reservations.c.agent == agent))
                        .order_by(reservations.c.created_at.desc(), reservations.c.id.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if row is not None:
                    self._settle(connection, row, usage)

    @staticmethod
    def _settle(connection: Connection, row: Any, usage: Usage) -> None:
        serialized_usage = usage.model_dump(mode="json")
        if row["payload"]["usage"] is not None:
            if row["payload"]["usage"] == serialized_usage:
                return
            raise ValueError("TOKEN_USAGE_CONFLICT")
        actual = (
            usage.input_tokens + usage.output_tokens
            if usage.input_tokens is not None and usage.output_tokens is not None
            else None
        )
        # Record an overrun honestly. Subsequent admissions count the full usage.
        connection.execute(
            reservations.update()
            .where(reservations.c.id == row["id"])
            .values(
                actual_tokens=actual,
                payload=dict(
                    row["payload"],
                    usage=serialized_usage,
                    cost_status="known" if usage.cost_gbp is not None else "unknown",
                    overrun=actual is not None and actual > row["reserved_tokens"],
                ),
            )
        )


def research_depth(
    *, deterministic_passed: bool, discovery_channels: int, qualified_quant: bool
) -> str:
    if not deterministic_passed:
        return "REJECT"
    if discovery_channels == 0:
        return "DETERMINISTIC_ONLY"
    if not qualified_quant:
        return "QUANT_QUALIFICATION_REQUIRED"
    return "FULL_REVIEW" if discovery_channels >= 2 else "QUANT_ONLY"
