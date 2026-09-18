"""Transactional jobs, report seals and worker fencing; no research computation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import Connection, Engine, case, create_engine, event, func, or_, select, text, true
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    CIOAuditReport,
    DecisionPacket,
    EvidenceRecord,
    FirmReport,
    QlibQuantResearchReport,
    RedTeamReport,
    ResearchMandate,
    ResearchSignal,
    ResearchSnapshot,
    ResearchState,
    TradingAgentsResearchReport,
    content_hash,
)
from money.storage import models as db

FIRST_PASS_FIRMS = frozenset({"tradingagents", "ai_hedge_fund", "qlib"})


def _verify_universe_context(snapshot: ResearchSnapshot, raw: Any) -> None:
    """Recheck complete frozen membership at durable admission/publication boundaries."""
    if snapshot.universe_hash is None:
        return
    from money.scanner.universe import UniverseResearchContext, bind_universe_snapshot

    context = UniverseResearchContext.model_validate(raw)
    source = next((item for item in context.snapshots if item.ticker == snapshot.ticker), None)
    if (
        source is None
        or snapshot.ticker not in context.screen.selected_tickers
        or snapshot != bind_universe_snapshot(source, context.universe)
        or not context.universe.observed_at <= now_utc() < context.universe.valid_until
    ):
        raise ValueError("RESEARCH_UNIVERSE_CONTEXT_MISMATCH")


STAGES = (
    "QUEUED",
    "ELIGIBILITY_CHECK",
    "DISCOVERY",
    "SNAPSHOT_BUILD",
    "FIRST_PASS_RESEARCH",
    "FIRST_PASS_LOCKED",
    "LEAN_VALIDATION",
    "CREWAI_AUDIT",
    "CROSS_EXAMINATION",
    "CONSENSUS",
    "COMPLETE",
)
TERMINAL = frozenset({"COMPLETE", "REJECTED", "FAILED"})


class StoreError(RuntimeError):
    """A safe, application-level persistence failure."""


class BarrierNotLocked(StoreError):
    """First-pass opinions are not available to downstream consumers yet."""


class LeaseLost(StoreError):
    """This worker no longer owns permission to change the job."""


class QueueCapacityExceeded(StoreError):
    """The durable queue is full."""


class EnqueueRateExceeded(StoreError):
    """The deployment-wide enqueue rate was exceeded."""


class IdempotencyConflict(StoreError):
    """An enqueue key was already used with different input."""


@dataclass(frozen=True)
class Claim:
    job_id: str
    token: str
    worker_id: str


def now_utc() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def payload(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    # Round-trip prevents subsequent mutation of caller-owned dictionaries.
    return dict(json.loads(json.dumps(value)))


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def serialize(value: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        key: aware(item).isoformat() if isinstance(item, datetime) else item
        for key, item in value.items()
    }


class ResearchStore:
    def __init__(
        self,
        database_url: str,
        *,
        allow_sqlite: bool = False,
        engine: Engine | None = None,
        claim: Claim | None = None,
        workspace_id: str | None = None,
        pool_size: int = 5,
        pool_timeout: int = 10,
        statement_timeout_ms: int = 15000,
    ) -> None:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        sqlite = database_url.startswith("sqlite:")
        if sqlite and not allow_sqlite:
            raise ValueError("SQLite requires explicit development/test configuration")
        if not sqlite and not database_url.startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL is required for production")
        options: dict[str, Any] = {"pool_pre_ping": True}
        if sqlite:
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in database_url:
                options["poolclass"] = StaticPool
        else:
            options.update(pool_size=pool_size, max_overflow=5, pool_timeout=pool_timeout)
            options["connect_args"] = {
                "connect_timeout": 10,
                "options": f"{make_url(database_url).query.get('options', '')} -c statement_timeout={statement_timeout_ms}",
            }
        self.engine = engine or create_engine(database_url, **options)
        self.database_url = database_url
        self.allow_sqlite = allow_sqlite
        self.claim = claim
        self.workspace_id = workspace_id
        if sqlite and engine is None:

            @event.listens_for(self.engine, "connect")
            def sqlite_foreign_keys(connection: Any, _: Any) -> None:
                connection.execute("PRAGMA foreign_keys=ON")

    def for_claim(self, claim: Claim) -> ResearchStore:
        return ResearchStore(
            self.database_url,
            allow_sqlite=self.allow_sqlite,
            engine=self.engine,
            claim=claim,
            workspace_id=self.workspace_id,
        )

    def for_workspace(self, workspace_id: str) -> ResearchStore:
        """Scope every public resource read to the authenticated deployment workspace."""
        return ResearchStore(
            self.database_url,
            allow_sqlite=self.allow_sqlite,
            engine=self.engine,
            workspace_id=workspace_id,
        )

    def _workspace_filter(self) -> Any:
        return db.jobs.c.workspace_id == self.workspace_id if self.workspace_id else True

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _owned_job(self, connection: Connection, job_id: str) -> Mapping[Any, Any]:
        row = (
            connection.execute(select(db.jobs).where(db.jobs.c.id == job_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise StoreError("Research job does not exist")
        claim = self.claim
        if (
            claim is None
            or claim.job_id != job_id
            or row["lease_token"] != claim.token
            or row["worker_id"] != claim.worker_id
            or row["lease_until"] is None
            or aware(row["lease_until"]) <= now_utc()
            or (row["deadline_at"] is not None and aware(row["deadline_at"]) <= now_utc())
            or row["status"] in TERMINAL
        ):
            raise LeaseLost("Worker lease is no longer valid")
        return row

    @staticmethod
    def _audit(
        connection: Connection,
        job_id: str | None,
        event_name: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            db.audit_events.insert().values(
                id=str(uuid4()),
                job_id=job_id,
                event=event_name,
                payload=data or {},
                created_at=now_utc(),
            )
        )

    def create_job(
        self,
        ticker: str,
        mandate: Any,
        *,
        capacity: int = 100,
        rate_per_minute: int = 20,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        admission: Callable[[Connection, str], None] | None = None,
        research_kind: str = "standard",
    ) -> dict[str, Any]:
        if research_kind not in {"standard", "live_rnd"}:
            raise ValueError("Unknown research kind")
        mandate_data = ResearchMandate.model_validate(payload(mandate)).model_dump(mode="json")
        stamp = now_utc()
        job_id, mandate_id = str(uuid4()), str(uuid4())
        workspace_id = self.workspace_id or "private"
        request_data = {"ticker": ticker, "mandate": mandate_data}
        if research_kind != "standard":
            request_data["research_kind"] = research_kind
        request_hash = digest(request_data)
        with self.transaction() as connection:
            # Singleton row serializes capacity and rate checks across API replicas.
            connection.execute(select(db.queue_control).with_for_update()).all()
            if idempotency_key is not None:
                existing = (
                    connection.execute(
                        select(db.jobs).where(
                            db.jobs.c.workspace_id == workspace_id,
                            db.jobs.c.idempotency_key == idempotency_key,
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict("Idempotency key was used with different input")
                    replay = serialize(existing)
                    for private in ("lease_token", "worker_id", "lease_until", "request_hash"):
                        replay.pop(private, None)
                    return replay
            active = (
                connection.scalar(
                    select(func.count())
                    .select_from(db.jobs)
                    .where(db.jobs.c.status.not_in(TERMINAL))
                )
                or 0
            )
            if active >= capacity:
                raise QueueCapacityExceeded("Research queue is at capacity")
            recent = (
                connection.scalar(
                    select(func.count())
                    .select_from(db.jobs)
                    .where(db.jobs.c.created_at >= stamp - timedelta(minutes=1))
                    .where(db.jobs.c.workspace_id == workspace_id)
                )
                or 0
            )
            if recent >= rate_per_minute:
                raise EnqueueRateExceeded("Research submission rate exceeded")
            connection.execute(
                db.mandates.insert().values(
                    id=mandate_id,
                    payload=mandate_data,
                    created_at=stamp,
                )
            )
            connection.execute(
                db.jobs.insert().values(
                    id=job_id,
                    ticker=ticker,
                    research_kind=research_kind,
                    mandate_id=mandate_id,
                    workspace_id=workspace_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    max_attempts=max_attempts,
                    status="QUEUED",
                    current_stage="QUEUED",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            if admission is not None:
                admission(connection, job_id)
            self._audit(connection, job_id, "JOB_ENQUEUED")
        result = self.get_job(job_id)
        assert result is not None
        return result

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        # A lost compute process must not leave the UI showing RUNNING forever.
        # This small control-plane update does not depend on a replacement worker.
        with self.transaction() as connection:
            self._expire_leases(connection, job_id)
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(db.jobs).where(db.jobs.c.id == job_id, self._workspace_filter())
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            result = serialize(row)
            for private in ("lease_token", "worker_id", "lease_until", "request_hash"):
                result.pop(private, None)
            packet = connection.scalar(
                select(db.packets.c.payload).where(db.packets.c.job_id == job_id)
            )
            result["packet"] = packet
            result["final_state"] = packet.get("final_state") if packet else None
            stored_signal = connection.scalar(
                select(db.signals.c.payload).where(db.signals.c.job_id == job_id)
            )
            if stored_signal:
                result["final_state"] = (
                    ResearchSignal.model_validate(stored_signal).effective_state(now_utc()).value
                )
                invalidated_at = connection.scalar(
                    select(func.min(db.audit_events.c.created_at)).where(
                        db.audit_events.c.job_id == job_id,
                        db.audit_events.c.event == "SIGNAL_INVALIDATED",
                        db.audit_events.c.created_at <= now_utc(),
                    )
                )
                if invalidated_at is not None:
                    result["final_state"] = "EXPIRED"
                    result["invalidated_at"] = aware(invalidated_at).isoformat()
            return result

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.transaction() as connection:
            self._expire_leases(connection)
        with self.engine.connect() as connection:
            invalidations = self._signal_invalidations()
            rows = connection.execute(
                select(
                    db.jobs,
                    db.packets.c.payload["final_state"].as_string().label("final_state"),
                    db.signals.c.payload.label("signal_payload"),
                    invalidations.c.invalidated_at,
                )
                .outerjoin(db.packets, db.packets.c.job_id == db.jobs.c.id)
                .outerjoin(db.signals, db.signals.c.job_id == db.jobs.c.id)
                .outerjoin(invalidations, invalidations.c.job_id == db.jobs.c.id)
                .where(self._workspace_filter())
                .order_by(db.jobs.c.created_at.desc())
                .limit(min(max(limit, 1), 100))
            ).mappings()
            results = []
            for row in rows:
                result = serialize(row)
                signal = result.pop("signal_payload")
                if signal:
                    result["final_state"] = (
                        ResearchSignal.model_validate(signal).effective_state(now_utc()).value
                    )
                    if result["invalidated_at"] is not None:
                        result["final_state"] = "EXPIRED"
                for private in ("lease_token", "worker_id", "lease_until", "request_hash"):
                    result.pop(private, None)
                results.append(result)
            return results

    def get_mandate(self, job_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            result = connection.scalar(
                select(db.mandates.c.payload)
                .join(db.jobs, db.jobs.c.mandate_id == db.mandates.c.id)
                .where(db.jobs.c.id == job_id)
                .where(self._workspace_filter())
            )
            if result is None:
                raise StoreError("Research mandate does not exist")
            return dict(result)

    def claim_job(
        self,
        worker_id: str,
        lease_seconds: int = 120,
        job_timeout_seconds: int = 1800,
        *,
        research_kind: str = "standard",
        job_id: str | None = None,
    ) -> Claim | None:
        if research_kind not in {"standard", "live_rnd"}:
            raise ValueError("Unknown research kind")
        if lease_seconds < 10:
            raise ValueError("Worker leases must last at least ten seconds")
        with self.transaction() as connection:
            stamp = now_utc()
            self._expire_leases(connection)
            row = (
                connection.execute(
                    select(db.jobs)
                    .where(db.jobs.c.status == "QUEUED")
                    .where(db.jobs.c.research_kind == research_kind)
                    .where(self._workspace_filter())
                    .where(db.jobs.c.id == job_id if job_id is not None else true())
                    .where(or_(db.jobs.c.available_at.is_(None), db.jobs.c.available_at <= stamp))
                    .order_by(db.jobs.c.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            job_id = row["id"]
            from money.product.models import usage_records

            admitted_seconds = connection.scalar(
                select(usage_records.c.max_seconds).where(usage_records.c.job_id == job_id)
            )
            if admitted_seconds is not None:
                job_timeout_seconds = min(job_timeout_seconds, admitted_seconds)
            token = str(uuid4())
            stage = row["resume_stage"] or (
                "SNAPSHOT_BUILD" if research_kind == "live_rnd" else "ELIGIBILITY_CHECK"
            )
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    status=stage,
                    current_stage=stage,
                    started_at=row["started_at"] or stamp,
                    updated_at=stamp,
                    attempt_count=row["attempt_count"] + 1,
                    error_code=None,
                    error_message=None,
                    deadline_at=stamp + timedelta(seconds=job_timeout_seconds),
                    worker_id=worker_id,
                    lease_token=token,
                    lease_until=stamp + timedelta(seconds=lease_seconds),
                )
            )
            self._audit(
                connection,
                job_id,
                "WORKER_CLAIMED",
                {
                    "attempt": row["attempt_count"] + 1,
                    "stage": stage,
                },
            )
            return Claim(job_id=job_id, token=token, worker_id=worker_id)

    def _expire_leases(self, connection: Connection, job_id: str | None = None) -> None:
        stamp = now_utc()
        query = select(db.jobs).where(
            db.jobs.c.status.not_in(TERMINAL),
            or_(db.jobs.c.lease_until <= stamp, db.jobs.c.deadline_at <= stamp),
            self._workspace_filter(),
        )
        if job_id is not None:
            query = query.where(db.jobs.c.id == job_id)
        expired = connection.execute(query.with_for_update(skip_locked=True)).mappings().all()
        for row in expired:
            timed_out = row["deadline_at"] and aware(row["deadline_at"]) <= stamp
            code = "JOB_TIMEOUT" if timed_out else "WORKER_LEASE_EXPIRED"
            retry = row["attempt_count"] < row["max_attempts"] and not timed_out
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == row["id"])
                .values(
                    status="QUEUED" if retry else "FAILED",
                    current_stage="QUEUED" if retry else "FAILED",
                    resume_stage=row["status"],
                    completed_at=None if retry else stamp,
                    updated_at=stamp,
                    lease_token=None,
                    lease_until=None,
                    deadline_at=None,
                    available_at=stamp + timedelta(seconds=self._retry_delay(row["attempt_count"])),
                    error_code=code if retry or timed_out else "JOB_RETRY_EXHAUSTED",
                    error_message="Research compute was interrupted; retry is pending."
                    if retry
                    else "Research compute failed; no research signal was issued.",
                )
            )
            self._audit(connection, row["id"], code, {"retry": retry})

    def renew_lease(self, job_id: str, lease_seconds: int = 120) -> None:
        with self.transaction() as connection:
            self._owned_job(connection, job_id)
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    lease_until=now_utc() + timedelta(seconds=lease_seconds),
                )
            )

    def get_checkpoint(self, job_id: str) -> dict[str, Any]:
        """Orchestrator-only recovery metadata; never supplied to a first-pass firm."""
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            return {
                "stage": row["status"],
                "snapshot": connection.scalar(
                    select(db.snapshots.c.payload).where(db.snapshots.c.job_id == job_id)
                ),
                "artifacts": {
                    item.kind: item.payload
                    for item in connection.execute(
                        select(db.artifacts.c.kind, db.artifacts.c.payload).where(
                            db.artifacts.c.job_id == job_id
                        )
                    )
                },
                "sealed_firms": list(
                    connection.scalars(
                        select(db.firm_reports.c.firm).where(db.firm_reports.c.job_id == job_id)
                    )
                ),
            }

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return float(min(300, 2 ** min(attempt, 8)) + secrets.randbelow(1000) / 1000)

    def retry_job(self, job_id: str, code: str) -> bool:
        """Retry an explicitly classified transient failure, preserving immutable checkpoints."""
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            retry = row["attempt_count"] < row["max_attempts"]
            stamp = now_utc()
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    status="QUEUED" if retry else "FAILED",
                    current_stage="QUEUED" if retry else "FAILED",
                    resume_stage=row["status"],
                    updated_at=stamp,
                    available_at=stamp + timedelta(seconds=self._retry_delay(row["attempt_count"])),
                    completed_at=None if retry else stamp,
                    deadline_at=None,
                    lease_token=None,
                    lease_until=None,
                    error_code=code if retry else "JOB_RETRY_EXHAUSTED",
                    error_message="A transient service failure delayed research."
                    if retry
                    else "Research retry limit reached; no signal was issued.",
                )
            )
            self._audit(
                connection,
                job_id,
                "JOB_RETRY_SCHEDULED" if retry else "JOB_RETRY_EXHAUSTED",
                {"failure_code": code, "attempt": row["attempt_count"]},
            )
            return retry

    def update_stage(self, job_id: str, status: str | Enum, **fields: Any) -> None:
        stage = str(status.value) if isinstance(status, Enum) else status
        if stage not in STAGES or stage in {"QUEUED", "FIRST_PASS_LOCKED", "COMPLETE"}:
            raise StoreError("Use the dedicated operation for this stage")
        if set(fields) - {"candidate_id"}:
            raise StoreError("Unsupported job field")
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            if STAGES.index(stage) < STAGES.index(row["status"]):
                raise StoreError("Research stages cannot move backwards")
            if STAGES.index(stage) > STAGES.index("FIRST_PASS_LOCKED") and row["locked_at"] is None:
                raise BarrierNotLocked("Final validation and audit require locked reports")
            if stage == "FIRST_PASS_RESEARCH" and row["snapshot_id"] is None:
                raise StoreError("First-pass research requires a frozen snapshot")
            self._audit(
                connection,
                job_id,
                "STAGE_CHANGED",
                {
                    "from": row["status"],
                    "to": stage,
                    "previous_stage_seconds": max(
                        0, (now_utc() - aware(row["updated_at"])).total_seconds()
                    ),
                },
            )
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    status=stage,
                    current_stage=stage,
                    updated_at=now_utc(),
                    **fields,
                )
            )

    def save_snapshot(self, job_id: str, snapshot: Any) -> None:
        data = ResearchSnapshot.model_validate(payload(snapshot)).model_dump(mode="json")
        snapshot_id = str(data["snapshot_id"])
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            if row["research_kind"] != "standard":
                raise StoreError("Personal R&D requires its own snapshot contract")
            if row["status"] != "SNAPSHOT_BUILD" or row["snapshot_id"] is not None:
                raise StoreError("A snapshot can only be frozen once during snapshot building")
            if data["ticker"] != row["ticker"]:
                raise StoreError("Snapshot ticker differs from the research job")
            if data.get("universe_hash") is not None:
                context = connection.scalar(
                    select(db.artifacts.c.payload).where(
                        db.artifacts.c.job_id == job_id, db.artifacts.c.kind == "universe_context"
                    )
                )
                _verify_universe_context(ResearchSnapshot.model_validate(data), context)
            connection.execute(
                db.snapshots.insert().values(
                    id=snapshot_id,
                    job_id=job_id,
                    payload=data,
                    created_at=now_utc(),
                )
            )
            for item in data.get("evidence", []):
                connection.execute(
                    db.evidence.insert().values(
                        id=f"{job_id}:{item.get('evidence_id', uuid4())}",
                        job_id=job_id,
                        payload=item,
                        created_at=now_utc(),
                    )
                )
            connection.execute(
                db.jobs.update().where(db.jobs.c.id == job_id).values(snapshot_id=snapshot_id)
            )
            self._audit(connection, job_id, "SNAPSHOT_FROZEN", {"snapshot_id": snapshot_id})

    def save_report(self, job_id: str, firm: str, report: Any) -> None:
        if firm not in FIRST_PASS_FIRMS:
            raise StoreError("Unknown first-pass research firm")
        report_types: dict[str, type[FirmReport]] = {
            "tradingagents": TradingAgentsResearchReport,
            "ai_hedge_fund": AIHedgeFundResearchReport,
            "qlib": QlibQuantResearchReport,
        }
        data = report_types[firm].model_validate(payload(report)).model_dump(mode="json")
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            if row["status"] != "FIRST_PASS_RESEARCH" or row["locked_at"] is not None:
                raise StoreError("Reports can only be sealed during first-pass research")
            if data.get("snapshot_id") != row["snapshot_id"]:
                raise StoreError("A firm report must reference the job's frozen snapshot")
            frozen = connection.scalar(
                select(db.snapshots.c.payload).where(db.snapshots.c.id == row["snapshot_id"])
            )
            if frozen is None or data["snapshot_hash"] != frozen["hash"]:
                raise StoreError("A firm report must match the frozen snapshot hash")
            if firm not in ResearchSnapshot.model_validate(frozen).required_first_pass_firms:
                raise StoreError("Firm is disabled by the frozen research policy")
            evidence_ids = {item["evidence_id"] for item in frozen["evidence"]}
            if any(not set(claim["evidence_ids"]) <= evidence_ids for claim in data["claims"]):
                raise StoreError("A firm report references evidence outside the frozen snapshot")
            try:
                connection.execute(
                    db.firm_reports.insert().values(
                        id=str(uuid4()),
                        job_id=job_id,
                        firm=firm,
                        payload=data,
                        content_hash=digest(data),
                        created_at=now_utc(),
                    )
                )
            except IntegrityError as error:
                raise StoreError("A sealed report cannot be replaced") from error
            self._audit(connection, job_id, "FIRM_REPORT_SEALED", {"firm": firm})

    def lock_first_pass(self, job_id: str) -> None:
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            frozen = connection.scalar(
                select(db.snapshots.c.payload).where(db.snapshots.c.id == row["snapshot_id"])
            )
            required = (
                ResearchSnapshot.model_validate(frozen).required_first_pass_firms
                if frozen is not None else FIRST_PASS_FIRMS
            )
            firms = frozenset(
                connection.scalars(
                    select(db.firm_reports.c.firm).where(db.firm_reports.c.job_id == job_id)
                )
            )
            if row["status"] != "FIRST_PASS_RESEARCH" or frozen is None or firms != required:
                raise BarrierNotLocked(
                    "All configured independent reports must be persisted before locking"
                )
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    status="FIRST_PASS_LOCKED",
                    current_stage="FIRST_PASS_LOCKED",
                    locked_at=now_utc(),
                    updated_at=now_utc(),
                )
            )
            self._audit(connection, job_id, "FIRST_PASS_LOCKED")

    def get_reports(self, job_id: str) -> dict[str, Any]:
        if self.get_job(job_id) is None:
            raise StoreError("Research job does not exist")
        with self.engine.connect() as connection:
            locked = connection.scalar(select(db.jobs.c.locked_at).where(db.jobs.c.id == job_id))
            if locked is None:
                raise BarrierNotLocked("First-pass reports remain sealed")
            return {
                row.firm: row.payload
                for row in connection.execute(
                    select(
                        db.firm_reports.c.firm,
                        db.firm_reports.c.payload,
                    ).where(db.firm_reports.c.job_id == job_id)
                )
            }

    def public_reports(self, job_id: str) -> dict[str, Any]:
        if self.get_job(job_id) is None:
            raise StoreError("Research job does not exist")
        with self.engine.connect() as connection:
            frozen = connection.scalar(
                select(db.snapshots.c.payload).where(db.snapshots.c.job_id == job_id)
            )
            if frozen is not None and frozen.get("usage_mode") == "PERSONAL_RESEARCH":
                raise StoreError("Personal research cannot export reports through the public API")
            required = (
                ResearchSnapshot.model_validate(frozen).required_first_pass_firms
                if frozen is not None else FIRST_PASS_FIRMS
            )
            locked = (
                connection.scalar(select(db.jobs.c.locked_at).where(db.jobs.c.id == job_id))
                is not None
            )
            firms = connection.scalars(
                select(db.firm_reports.c.firm).where(db.firm_reports.c.job_id == job_id)
            ).all()
            extras = {
                row.kind: row.payload
                for row in connection.execute(
                    select(
                        db.artifacts.c.kind,
                        db.artifacts.c.payload,
                    ).where(db.artifacts.c.job_id == job_id)
                )
            }
        return {
            "locked": locked,
            "reports": self.get_reports(job_id)
            if locked
            else {
                firm: {"status": "SEALED" if firm in firms else "PENDING"}
                for firm in sorted(required)
            },
            "artifacts": extras,
        }

    def get_evidence(self, job_id: str) -> dict[str, Any]:
        if self.get_job(job_id) is None:
            raise StoreError("Research job does not exist")
        with self.engine.connect() as connection:
            snapshot = connection.scalar(
                select(db.snapshots.c.payload).where(db.snapshots.c.job_id == job_id)
            )
            if snapshot is not None and snapshot.get("usage_mode") == "PERSONAL_RESEARCH":
                raise StoreError("Personal research cannot redistribute raw evidence through the API")
            items = list(
                connection.scalars(
                    select(db.evidence.c.payload).where(db.evidence.c.job_id == job_id)
                )
            )
            return {"snapshot": snapshot, "evidence": items}

    def save_artifact(self, job_id: str, kind: str, value: Any) -> None:
        correspondence_call = re.fullmatch(r"cross_call_[a-f0-9]{24}", kind) is not None
        if not correspondence_call and kind not in {
            "eligibility",
            "discovery",
            "lean",
            "audit",
            "red_team",
            "independence",
            "cross_examination",
            "consensus",
            "token_usage",
            "market_quality",
            "compute_budget",
            "source_manifest",
            "universe_context",
            "cio_runtime",
            "signal_design",
        }:
            raise StoreError("Unknown research artifact")
        data = payload(value)
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            if kind == "universe_context" and (
                row["locked_at"] is not None or row["status"] != "SNAPSHOT_BUILD"
            ):
                raise StoreError("Universe context must be frozen before first-pass research")
            if (
                kind
                not in {
                    "eligibility",
                    "discovery",
                    "token_usage",
                    "market_quality",
                    "compute_budget",
                    "source_manifest",
                    "universe_context",
                }
                and row["locked_at"] is None
            ):
                raise BarrierNotLocked("Downstream artifacts require locked first-pass reports")
            if kind == "cross_examination" or correspondence_call:
                from money.crews.cross_examination import CrossExaminationPacket

                checked_cross = (
                    CrossExaminationPacket.model_validate(data) if not correspondence_call else None
                )
                prior = {
                    item.kind: item.payload
                    for item in connection.execute(
                        select(
                            db.artifacts.c.kind,
                            db.artifacts.c.payload,
                        ).where(
                            db.artifacts.c.job_id == job_id,
                            db.artifacts.c.kind.in_(["audit", "lean"]),
                        )
                    )
                }
                if (
                    row["status"] != "CROSS_EXAMINATION"
                    or "lean" not in prior
                    or not prior.get("audit", {}).get("completed")
                    or (
                        checked_cross is not None
                        and checked_cross.snapshot_id != row["snapshot_id"]
                    )
                ):
                    raise StoreError(
                        "Cross-examination requires completed validation and initial CIO audit"
                    )
            connection.execute(
                db.artifacts.insert().values(
                    id=str(uuid4()),
                    job_id=job_id,
                    kind=kind,
                    payload=data,
                    created_at=now_utc(),
                )
            )
            table = {
                "eligibility": db.eligibility,
                "discovery": db.discoveries,
                "token_usage": db.token_usage,
            }.get(kind)
            if table is not None:
                connection.execute(
                    table.insert().values(
                        id=str(uuid4()),
                        job_id=job_id,
                        payload=data,
                        created_at=now_utc(),
                    )
                )

    def save_cio_artifacts(
        self, job_id: str, audit: Any, red_team: Any, runtime: Any = None
    ) -> None:
        """Seal one coupled CIO run atomically so recovery never loses its Red Team."""
        checked_audit = CIOAuditReport.model_validate(payload(audit))
        checked_red_team = RedTeamReport.model_validate(payload(red_team))
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            if row["locked_at"] is None or row["status"] != "CREWAI_AUDIT":
                raise BarrierNotLocked(
                    "CIO artifacts require the audit stage after first-pass lock"
                )
            if checked_audit.snapshot_id != row["snapshot_id"]:
                raise StoreError("CIO audit differs from the frozen snapshot")
            values = [("audit", payload(checked_audit)), ("red_team", payload(checked_red_team))]
            if runtime is not None:
                values.append(("cio_runtime", payload(runtime)))
            for kind, value in values:
                connection.execute(
                    db.artifacts.insert().values(
                        id=str(uuid4()),
                        job_id=job_id,
                        kind=kind,
                        payload=value,
                        created_at=now_utc(),
                    )
                )
            self._audit(connection, job_id, "CIO_ARTIFACTS_SEALED")

    def complete_job(self, job_id: str, packet: Any, state: str | Enum = "COMPLETE") -> None:
        status = str(state.value) if isinstance(state, Enum) else state
        if status not in {"COMPLETE", "REJECTED"}:
            raise StoreError("Invalid completion status")
        data = payload(packet)
        validated_packet: DecisionPacket | None = None
        if status == "COMPLETE":
            validated_packet = DecisionPacket.model_validate(data)
            data = validated_packet.model_dump(mode="json")
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            if row["research_kind"] != "standard":
                raise StoreError("Personal R&D cannot publish commercial decision packets")
            if status == "COMPLETE" and row["locked_at"] is None:
                raise BarrierNotLocked("Completed research requires locked reports")
            if status == "COMPLETE":
                if row["status"] != "CONSENSUS" or data["research_id"] != job_id:
                    raise StoreError(
                        "Completed research requires consensus and matching job identity"
                    )
                stored_reports = {
                    item.firm: item.payload
                    for item in connection.execute(
                        select(
                            db.firm_reports.c.firm,
                            db.firm_reports.c.payload,
                        ).where(db.firm_reports.c.job_id == job_id)
                    )
                }
                if {item["firm"]: item for item in data["reports"]} != stored_reports:
                    raise StoreError("Decision packet reports differ from the sealed reports")
                mandate = connection.scalar(
                    select(db.mandates.c.payload).where(db.mandates.c.id == row["mandate_id"])
                )
                if data["mandate"] != mandate:
                    raise StoreError(
                        "Decision packet mandate differs from the immutable job mandate"
                    )
                frozen = connection.scalar(
                    select(db.snapshots.c.payload).where(db.snapshots.c.id == row["snapshot_id"])
                )
                if frozen is None or any(
                    (
                        data["snapshot_id"] != row["snapshot_id"],
                        data["snapshot_hash"] != frozen["hash"],
                        data["sources"] != frozen["evidence"],
                        data["eligibility"] != frozen["instrument"],
                        data["candidate"]["ticker"] != row["ticker"],
                    )
                ):
                    raise StoreError("Decision packet evidence differs from the frozen snapshot")
                stored_artifacts = {
                    item.kind: item.payload
                    for item in connection.execute(
                        select(db.artifacts.c.kind, db.artifacts.c.payload).where(
                            db.artifacts.c.job_id == job_id
                        )
                    )
                }
                if any(
                    data[key] != stored_artifacts.get(key)
                    for key in ("lean", "audit", "red_team", "independence")
                ):
                    raise StoreError(
                        "Decision packet validation differs from persisted audit evidence"
                    )
                # Recompute gates at the final persistence boundary so callers cannot
                # publish a positive typed packet by bypassing the research flow.
                from money.policy.governance import consensus, evidence_independence

                assert validated_packet is not None
                authoritative_snapshot = ResearchSnapshot.model_validate(frozen)
                if authoritative_snapshot.usage_mode == "PERSONAL_RESEARCH":
                    raise StoreError(
                        "Personal research cannot publish commercial decision packets or raw sources"
                    )
                _verify_universe_context(
                    authoritative_snapshot, stored_artifacts.get("universe_context")
                )
                calculated_independence = evidence_independence(
                    authoritative_snapshot, validated_packet.reports
                )
                if calculated_independence != validated_packet.independence:
                    raise StoreError(
                        "Decision packet evidence independence was not derived from its sources"
                    )
                cross_disagreement = False
                cross_data = stored_artifacts.get("cross_examination")
                if cross_data is not None:
                    from money.crews.cross_examination import CrossExaminationPacket

                    cross = CrossExaminationPacket.model_validate(cross_data)
                    if (
                        cross.snapshot_id != authoritative_snapshot.snapshot_id
                        or cross.snapshot_hash != authoritative_snapshot.hash
                        or cross.qlib_enabled != authoritative_snapshot.qlib_enabled
                        or len(cross.report_hashes)
                        != len(authoritative_snapshot.required_first_pass_firms)
                        or dict(cross.report_hashes)
                        != {
                            report.firm: content_hash(report) for report in validated_packet.reports
                        }
                        or cross.lean_hash != content_hash(validated_packet.lean)
                        or cross.initial_audit_hash != content_hash(validated_packet.audit)
                        or len(cross.rounds) != validated_packet.cross_examination_rounds
                        or cross.hash != data.get("cross_examination_hash")
                        or not authoritative_snapshot.created_at
                        <= cross.issued_at
                        <= validated_packet.issued_at
                    ):
                        raise StoreError("Cross-examination differs from sealed research evidence")
                    cross_disagreement = cross.material_disagreement
                elif validated_packet.cross_examination_rounds or data.get(
                    "cross_examination_hash"
                ):
                    raise StoreError(
                        "Cross-examination rounds require a persisted immutable artifact"
                    )
                calculated_state, calculated_reasons = consensus(
                    validated_packet.mandate,
                    authoritative_snapshot,
                    validated_packet.reports,
                    validated_packet.lean,
                    validated_packet.audit,
                    validated_packet.red_team,
                    calculated_independence,
                    now_utc(),
                    rounds=validated_packet.cross_examination_rounds,
                    cross_examination_disagreement=cross_disagreement,
                )
                from money.signals.generation import (
                    DOWNGRADE_REASONS,
                    SignalDesign,
                    generate_signal,
                )

                design_data = stored_artifacts.get("signal_design")
                if design_data is not None:
                    design = SignalDesign.model_validate(design_data)
                    if design.signal is not None:
                        if (
                            design.policy is None
                            or design.market_quality is None
                            or design.cost_applicability is None
                            or design.designed_at is None
                            or design.designed_at > validated_packet.issued_at
                            or design.signal.valid_until <= now_utc()
                        ):
                            raise StoreError("Signal design lacks reproducible current inputs")
                        recalculated_design = generate_signal(
                            job_id,
                            validated_packet.mandate,
                            authoritative_snapshot,
                            validated_packet.reports,
                            validated_packet.lean,
                            validated_packet.audit,
                            validated_packet.red_team,
                            state=calculated_state,
                            issued_at=design.designed_at,
                            market_quality=design.market_quality,
                            cost_applicability=design.cost_applicability,
                            policy=design.policy,
                            rounds=validated_packet.cross_examination_rounds,
                        )
                        if (
                            recalculated_design != design
                            or validated_packet.signal != design.signal
                        ):
                            raise StoreError(
                                "Published signal differs from its deterministic sealed design"
                            )
                    else:
                        if validated_packet.signal is not None:
                            raise StoreError("Signal cannot bypass the sealed unavailable design")
                        if calculated_state in {
                            ResearchState.RESEARCH_CANDIDATE,
                            ResearchState.WATCH,
                        }:
                            if not design.reasons or not set(design.reasons) <= DOWNGRADE_REASONS:
                                raise StoreError("Signal design downgrade reasons are invalid")
                            calculated_state = ResearchState.INSUFFICIENT_EVIDENCE
                            calculated_reasons = design.reasons
                elif validated_packet.signal is not None:
                    raise StoreError("Published signal requires a sealed deterministic design")
                if (validated_packet.final_state, validated_packet.reasons) != (
                    calculated_state,
                    calculated_reasons,
                ):
                    raise StoreError("Decision packet bypasses deterministic consensus gates")
            elif data.get("research_id") != job_id or data.get("final_state") != "REJECT":
                raise StoreError("Rejected packets require the job identity and REJECT state")
            signal = data.get("signal")
            if signal and (status != "COMPLETE" or row["status"] != "CONSENSUS"):
                raise StoreError("Signals can only be published after consensus")
            connection.execute(
                db.packets.insert().values(
                    job_id=job_id,
                    payload=data,
                    content_hash=digest(data),
                    created_at=now_utc(),
                )
            )
            if signal:
                expiry = datetime.fromisoformat(signal["valid_until"])
                connection.execute(
                    db.signals.insert().values(
                        job_id=job_id,
                        payload=signal,
                        valid_until=expiry,
                        created_at=now_utc(),
                    )
                )
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    status=status,
                    current_stage=status,
                    completed_at=now_utc(),
                    updated_at=now_utc(),
                    lease_token=None,
                    lease_until=None,
                )
            )
            self._audit(
                connection, job_id, "DECISION_PERSISTED", {"final_state": data.get("final_state")}
            )
            # The web channel is the database itself: publish informational completion
            # atomically with the immutable packet, never send anything externally here.
            from money.storage.production_models import alert_outbox

            connection.execute(
                alert_outbox.insert().values(
                    id=digest(
                        {
                            "job": job_id,
                            "event": "RESEARCH_COMPLETED",
                            "key": "decision",
                            "channel": "web",
                        }
                    ),
                    job_id=job_id,
                    workspace_id=row["workspace_id"],
                    channel="web",
                    state="DELIVERED",
                    attempts=0,
                    created_at=now_utc(),
                    delivered_at=now_utc(),
                    payload={
                        "event": "RESEARCH_COMPLETED",
                        "research_id": job_id,
                        "message": "Research is complete and available for human review.",
                        "informational_only": True,
                    },
                )
            )

    def fail_job(self, job_id: str, code: str, message: str) -> None:
        with self.transaction() as connection:
            self._owned_job(connection, job_id)
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    status="FAILED",
                    current_stage="FAILED",
                    error_code=code[:80],
                    error_message=message[:512],
                    completed_at=now_utc(),
                    updated_at=now_utc(),
                    lease_token=None,
                    lease_until=None,
                )
            )
            self._audit(connection, job_id, "JOB_FAILED", {"error_code": code[:80]})

    def heartbeat(self, worker_id: str, mode: str, healthy: bool = True) -> None:
        with self.transaction() as connection:
            # Worker identifiers are process-unique, so only this process updates its row.
            exists = connection.scalar(
                select(db.service_health.c.id).where(db.service_health.c.id == worker_id)
            )
            values = {"healthy": healthy, "mode": mode, "updated_at": now_utc()}
            if exists:
                connection.execute(
                    db.service_health.update()
                    .where(db.service_health.c.id == worker_id)
                    .values(**values)
                )
            else:
                connection.execute(db.service_health.insert().values(id=worker_id, **values))

    def health(self, mode: str) -> dict[str, str]:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            active = connection.scalar(
                select(func.count())
                .select_from(db.service_health)
                .where(
                    db.service_health.c.updated_at >= now_utc() - timedelta(seconds=90),
                    db.service_health.c.healthy.is_(True),
                    db.service_health.c.mode == mode,
                )
            )
            # Checking a migrated application table distinguishes connection from readiness.
            connection.scalar(select(func.count()).select_from(db.queue_control))
            worker = "ready" if active else "unavailable"
            return {
                "status": "ok" if active else "degraded",
                "database": "ready",
                "worker": worker,
                "mode": mode,
            }

    def objective_publications(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Bounded, tenant-scoped immutable inputs; no reports or providers are executed."""
        if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise ValueError("Invalid objective page")
        with self.engine.connect() as connection:
            invalidations = self._signal_invalidations()
            rows = connection.execute(
                select(
                    db.jobs.c.id.label("job_id"),
                    db.packets.c.payload.label("packet"),
                    db.artifacts.c.payload.label("design"),
                    invalidations.c.invalidated_at,
                )
                .select_from(db.jobs)
                .join(db.packets, db.packets.c.job_id == db.jobs.c.id)
                .join(db.signals, db.signals.c.job_id == db.jobs.c.id)
                .join(
                    db.artifacts,
                    (db.artifacts.c.job_id == db.jobs.c.id)
                    & (db.artifacts.c.kind == "signal_design"),
                )
                .outerjoin(invalidations, invalidations.c.job_id == db.jobs.c.id)
                .where(
                    self._workspace_filter(),
                    db.jobs.c.research_kind == "standard",
                    db.jobs.c.status == "COMPLETE",
                )
                .order_by(db.jobs.c.created_at.desc(), db.jobs.c.id)
                .offset(offset)
                .limit(limit + 1)
            ).mappings()
            return [serialize(row) for row in rows]

    def list_signals(self, *, expired: bool = False) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            invalidations = self._signal_invalidations()
            rows = connection.execute(
                select(db.signals, invalidations.c.invalidated_at)
                .join(db.jobs, db.jobs.c.id == db.signals.c.job_id)
                .outerjoin(invalidations, invalidations.c.job_id == db.jobs.c.id)
                .where(self._workspace_filter())
                .order_by(db.signals.c.created_at.desc())
            ).mappings()
            result = []
            for row in rows:
                state = (
                    ResearchSignal.model_validate(row["payload"]).effective_state(now_utc()).value
                )
                if row["invalidated_at"] is not None:
                    state = "EXPIRED"
                if (state == "EXPIRED") == expired:
                    result.append({**serialize(row), "final_state": state})
                if len(result) >= 100:
                    break
            return result

    @staticmethod
    def _signal_invalidations() -> Any:
        return (
            select(
                db.audit_events.c.job_id,
                func.min(db.audit_events.c.created_at).label("invalidated_at"),
            )
            .where(
                db.audit_events.c.event == "SIGNAL_INVALIDATED",
                db.audit_events.c.created_at <= now_utc(),
            )
            .group_by(db.audit_events.c.job_id)
            .subquery()
        )

    def invalidate_signal(self, job_id: str, *, reason: str, evidence: EvidenceRecord) -> None:
        """Append verified event evidence; never update the immutable issued signal."""
        if reason not in {
            "PRICE",
            "FUNDAMENTAL",
            "EVENT",
            "ELIGIBILITY",
            "ETHICAL",
            "DATA_QUALITY",
        }:
            raise StoreError("Unknown signal invalidation reason")
        proof = EvidenceRecord.model_validate_json(evidence.model_dump_json())
        stamp = now_utc()
        if not proof.available_at(stamp) or proof.fresh_until <= stamp or proof.conflicting:
            raise StoreError("Signal invalidation requires fresh verified evidence")
        with self.transaction() as connection:
            job = (
                connection.execute(
                    select(db.jobs)
                    .where(db.jobs.c.id == job_id, self._workspace_filter())
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            signal = connection.scalar(
                select(db.signals.c.payload).where(db.signals.c.job_id == job_id)
            )
            if job is None or signal is None:
                raise StoreError("An accessible published signal is required")
            existing = connection.scalar(
                select(db.audit_events.c.id).where(
                    db.audit_events.c.job_id == job_id,
                    db.audit_events.c.event == "SIGNAL_INVALIDATED",
                    db.audit_events.c.payload["evidence_hash"].as_string() == proof.hash,
                )
            )
            if existing is not None:
                return
            self._audit(
                connection,
                job_id,
                "SIGNAL_INVALIDATED",
                {
                    "reason": reason,
                    "evidence_hash": proof.hash,
                    "evidence": proof.model_dump(mode="json"),
                    "invalidated_at": stamp.isoformat(),
                },
            )
            from money.storage.production_models import alert_outbox

            connection.execute(
                alert_outbox.insert().values(
                    id=digest(
                        {
                            "job": job_id,
                            "event": "MATERIAL_INVALIDATION",
                            "key": proof.hash,
                            "channel": "web",
                        }
                    ),
                    job_id=job_id,
                    workspace_id=job["workspace_id"],
                    channel="web",
                    state="DELIVERED",
                    attempts=0,
                    created_at=stamp,
                    delivered_at=stamp,
                    payload={
                        "event": "MATERIAL_INVALIDATION",
                        "research_id": job_id,
                        "message": "New evidence has invalidated a research assumption.",
                        "informational_only": True,
                    },
                )
            )

    def list_outcomes(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                serialize(row)
                for row in connection.execute(
                    select(db.outcomes)
                    .join(db.jobs, db.jobs.c.id == db.outcomes.c.job_id)
                    .where(self._workspace_filter())
                    .order_by(db.outcomes.c.created_at.desc())
                    .limit(100)
                ).mappings()
            ]

    def list_discovery(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                serialize(row)
                for row in connection.execute(
                    select(db.discoveries)
                    .join(db.jobs, db.jobs.c.id == db.discoveries.c.job_id)
                    .where(self._workspace_filter())
                    .order_by(db.discoveries.c.created_at.desc())
                    .limit(100)
                ).mappings()
            ]

    def list_universe(self) -> list[dict[str, Any]]:
        """Previously researched eligibility records, not a claimed complete broker universe."""
        with self.engine.connect() as connection:
            return [
                serialize(row)
                for row in connection.execute(
                    select(db.eligibility)
                    .join(db.jobs, db.jobs.c.id == db.eligibility.c.job_id)
                    .where(self._workspace_filter())
                    .order_by(db.eligibility.c.created_at.desc())
                    .limit(100)
                ).mappings()
            ]

    def consume_rate_limit(self, key: str, limit: int, window_seconds: int) -> dict[str, Any]:
        """Database-serialized fixed windows shared by all web/API replicas."""
        bucket = f"{self.workspace_id or 'private'}:{key}"
        stamp = now_utc()
        with self.transaction() as connection:
            connection.execute(select(db.queue_control).with_for_update()).all()
            row = (
                connection.execute(select(db.rate_limits).where(db.rate_limits.c.bucket == bucket))
                .mappings()
                .first()
            )
            if row is None:
                connection.execute(
                    db.rate_limits.insert().values(
                        bucket=bucket,
                        window_started_at=stamp,
                        count=1,
                    )
                )
                return {"allowed": True, "retry_after": 0}
            elapsed = (stamp - aware(row["window_started_at"])).total_seconds()
            if elapsed >= window_seconds:
                connection.execute(
                    db.rate_limits.update()
                    .where(db.rate_limits.c.bucket == bucket)
                    .values(window_started_at=stamp, count=1)
                )
                return {"allowed": True, "retry_after": 0}
            allowed = row["count"] < limit
            if allowed:
                connection.execute(
                    db.rate_limits.update()
                    .where(db.rate_limits.c.bucket == bucket)
                    .values(count=row["count"] + 1)
                )
            return {
                "allowed": allowed,
                "retry_after": 0 if allowed else max(1, int(window_seconds - elapsed) + 1),
            }

    def create_session(
        self, token_hash: str, credential_version: str, expires_at: datetime
    ) -> None:
        stamp = now_utc()
        expiry = aware(expires_at)
        if not stamp < expiry <= stamp + timedelta(hours=8, seconds=5):
            raise StoreError("Session expiry exceeds policy")
        with self.transaction() as connection:
            connection.execute(
                db.sessions.insert().values(
                    token_hash=token_hash,
                    workspace_id=self.workspace_id or "private",
                    credential_version=credential_version,
                    expires_at=expiry,
                    created_at=stamp,
                )
            )
            self._audit(
                connection, None, "SESSION_CREATED", {"workspace": self.workspace_id or "private"}
            )

    def session_valid(self, token_hash: str, credential_version: str) -> bool:
        with self.engine.connect() as connection:
            return (
                connection.scalar(
                    select(db.sessions.c.token_hash).where(
                        db.sessions.c.token_hash == token_hash,
                        db.sessions.c.workspace_id == (self.workspace_id or "private"),
                        db.sessions.c.credential_version == credential_version,
                        db.sessions.c.revoked_at.is_(None),
                        db.sessions.c.expires_at > now_utc(),
                    )
                )
                is not None
            )

    def revoke_session(self, token_hash: str, credential_version: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                db.sessions.update()
                .where(
                    db.sessions.c.token_hash == token_hash,
                    db.sessions.c.workspace_id == (self.workspace_id or "private"),
                    db.sessions.c.credential_version == credential_version,
                    db.sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=now_utc())
            )
            self._audit(
                connection, None, "SESSION_REVOKED", {"workspace": self.workspace_id or "private"}
            )

    def operational_metrics(self, *, include_details: bool = False) -> dict[str, Any]:
        """Workspace aggregates; detailed provider telemetry is shared and explicit.

        Public readiness uses the small default projection. Token accounting uses
        reservations only, avoiding double-counting copies in reports/artifacts.
        """
        workspace = db.jobs.c.workspace_id == (self.workspace_id or "private")
        stamp = now_utc()
        with self.engine.connect() as connection:
            counts = {
                status: count
                for status, count in connection.execute(
                    select(db.jobs.c.status, func.count())
                    .where(workspace)
                    .group_by(db.jobs.c.status)
                ).all()
            }
            oldest = connection.scalar(
                select(func.min(db.jobs.c.created_at)).where(
                    workspace, db.jobs.c.status == "QUEUED"
                )
            )
            schema = connection.scalar(text("SELECT version_num FROM alembic_version"))
            result = {
                "schema_revision": schema,
                "queue_depth": counts.get("QUEUED", 0),
                "queue_age_seconds": max(0, (stamp - aware(oldest)).total_seconds())
                if oldest
                else 0,
                "job_counts": counts,
            }
            if not include_details:
                return result
            from money.storage.production_models import budget_reservations, provider_state

            reservations = budget_reservations
            scoped_reservations = reservations.join(db.jobs, reservations.c.job_id == db.jobs.c.id)
            cost = reservations.c.payload["usage"]["cost_gbp"].as_numeric(28, 10)
            totals = (
                connection.execute(
                    select(
                        func.count().label("reservations"),
                        func.coalesce(func.sum(reservations.c.actual_tokens), 0).label(
                            "known_tokens"
                        ),
                        (func.count() - func.count(reservations.c.actual_tokens)).label(
                            "unknown_usage_count"
                        ),
                        func.coalesce(func.sum(reservations.c.reserved_tokens), 0).label(
                            "reserved_tokens"
                        ),
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        reservations.c.actual_tokens.is_(None),
                                        reservations.c.reserved_tokens,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ).label("unsettled_reserved_tokens"),
                        func.coalesce(
                            func.sum(
                                func.coalesce(
                                    reservations.c.actual_tokens, reservations.c.reserved_tokens
                                )
                            ),
                            0,
                        ).label("budget_charged_tokens"),
                        func.coalesce(func.sum(cost), 0).label("cost_known_total"),
                        func.count(cost).label("cost_known_count"),
                    )
                    .select_from(scoped_reservations)
                    .where(workspace)
                )
                .mappings()
                .one()
            )
            retries = connection.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (db.jobs.c.attempt_count > 1, db.jobs.c.attempt_count - 1), else_=0
                            )
                        ),
                        0,
                    )
                ).where(workspace)
            )
            duration = (
                (func.julianday(db.jobs.c.completed_at) - func.julianday(db.jobs.c.created_at))
                * 86400
                if self.engine.dialect.name == "sqlite"
                else func.extract("epoch", db.jobs.c.completed_at - db.jobs.c.created_at)
            )
            durations = connection.execute(
                select(func.count(), func.avg(duration), func.max(duration)).where(
                    workspace, db.jobs.c.completed_at.is_not(None)
                )
            ).one()
            terminal = sum(counts.get(status, 0) for status in TERMINAL)
            produced = connection.scalar(
                select(func.count()).select_from(db.signals.join(db.jobs)).where(workspace)
            )
            # Reuse the expression: independent JSON-key binds become distinct
            # PostgreSQL parameters, so SELECT would not match GROUP BY.
            final_state = db.packets.c.payload["final_state"].as_string()
            states = {
                state: count
                for state, count in connection.execute(
                    select(final_state, func.count())
                    .select_from(db.packets.join(db.jobs))
                    .where(workspace)
                    .group_by(final_state)
                ).all()
            }
            provider_rows = (
                connection.execute(select(provider_state).order_by(provider_state.c.id).limit(101))
                .mappings()
                .all()
            )
            providers = []
            known_labels = {
                "eodhd:ohlcv",
                "eodhd:corporate_action",
                "eodhd:news",
                "companies-house:filing",
            }
            for row in provider_rows[:100]:
                metadata = row["payload"]
                latency = metadata.get("last_duration_seconds")
                last_latency = (
                    float(latency)
                    if isinstance(latency, (float, int))
                    and not isinstance(latency, bool)
                    and math.isfinite(latency)
                    and latency >= 0
                    else None
                )
                providers.append(
                    {
                        "provider": row["id"]
                        if row["id"] in known_labels
                        else "provider-" + hashlib.sha256(row["id"].encode()).hexdigest()[:12],
                        "circuit": "CLOSED"
                        if row["open_until"] is None
                        else "OPEN"
                        if aware(row["open_until"]) > stamp
                        else "PROBE_DUE",
                        "calls": max(0, metadata.get("calls", 0))
                        if type(metadata.get("calls", 0)) is int
                        else 0,
                        "failures": max(0, metadata.get("failures", 0))
                        if type(metadata.get("failures", 0)) is int
                        else 0,
                        "consecutive_failures": max(0, row["failures"]),
                        "last_latency_seconds": last_latency,
                        "last_success": metadata.get("last_success")
                        if type(metadata.get("last_success")) is bool
                        else None,
                        "updated_at": aware(row["updated_at"]).isoformat(),
                        "qualification": "UNKNOWN",
                    }
                )
            result.update(
                {
                    "scope": "workspace",
                    "observed_at": stamp.isoformat(),
                    "jobs": {
                        "total": sum(counts.values()),
                        "failed": counts.get("FAILED", 0),
                        "terminal": terminal,
                        "failure_rate": counts.get("FAILED", 0) / terminal if terminal else None,
                        "retry_attempts": int(retries or 0),
                        "duration": {
                            "samples": durations[0],
                            "mean_seconds": max(0, float(durations[1]))
                            if durations[1] is not None
                            else None,
                            "max_seconds": max(0, float(durations[2]))
                            if durations[2] is not None
                            else None,
                        },
                    },
                    "tokens": {
                        "source": "budget_reservations",
                        **{
                            key: int(totals[key])
                            for key in (
                                "reservations",
                                "known_tokens",
                                "unknown_usage_count",
                                "reserved_tokens",
                                "unsettled_reserved_tokens",
                                "budget_charged_tokens",
                            )
                        },
                    },
                    "costs": {
                        "currency": "GBP",
                        # Legacy keys remain for compatible clients. Usage.cost_gbp
                        # has no invoice provenance and may be calculated from rates.
                        "basis": "ESTIMATED_OR_UNVERIFIED_REPORT",
                        "known_total": str(Decimal(totals["cost_known_total"])),
                        "known_count": totals["cost_known_count"],
                        "unknown_count": totals["reservations"] - totals["cost_known_count"],
                        "actual_total": None,
                        "actual_count": 0,
                        "actual_unknown_count": totals["reservations"],
                    },
                    "signals": {
                        "produced": int(produced or 0),
                        "rejected": states.get("REJECT", 0),
                        "insufficient_evidence": states.get("INSUFFICIENT_EVIDENCE", 0),
                    },
                    "providers": {
                        "scope": "shared_compute_plane",
                        "records": providers,
                        "truncated": len(provider_rows) > 100,
                    },
                }
            )
            return result
