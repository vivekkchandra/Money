"""Transactional jobs, report seals and worker fencing; no research computation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import Connection, Engine, create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    DecisionPacket,
    FirmReport,
    QlibQuantResearchReport,
    ResearchMandate,
    ResearchSignal,
    ResearchSnapshot,
    TradingAgentsResearchReport,
)
from money.storage import models as db

FIRST_PASS_FIRMS = frozenset({"tradingagents", "ai_hedge_fund", "qlib"})
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
        self.engine = engine or create_engine(database_url, **options)
        self.database_url = database_url
        self.allow_sqlite = allow_sqlite
        self.claim = claim
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
        )

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
        self, ticker: str, mandate: Any, *, capacity: int = 100, rate_per_minute: int = 20
    ) -> dict[str, Any]:
        mandate_data = ResearchMandate.model_validate(payload(mandate)).model_dump(mode="json")
        stamp = now_utc()
        job_id, mandate_id = str(uuid4()), str(uuid4())
        with self.transaction() as connection:
            # Singleton row serializes capacity and rate checks across API replicas.
            connection.execute(select(db.queue_control).with_for_update()).all()
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
                    mandate_id=mandate_id,
                    status="QUEUED",
                    current_stage="QUEUED",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
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
                connection.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().first()
            )
            if row is None:
                return None
            result = serialize(row)
            for private in ("lease_token", "worker_id", "lease_until"):
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
            return result

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            ids = connection.scalars(
                select(db.jobs.c.id)
                .order_by(db.jobs.c.created_at.desc())
                .limit(min(max(limit, 1), 100))
            ).all()
        return [job for job_id in ids if (job := self.get_job(job_id)) is not None]

    def get_mandate(self, job_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            result = connection.scalar(
                select(db.mandates.c.payload)
                .join(db.jobs, db.jobs.c.mandate_id == db.mandates.c.id)
                .where(db.jobs.c.id == job_id)
            )
            if result is None:
                raise StoreError("Research mandate does not exist")
            return dict(result)

    def claim_job(self, worker_id: str, lease_seconds: int = 120) -> Claim | None:
        if lease_seconds < 10:
            raise ValueError("Worker leases must last at least ten seconds")
        with self.transaction() as connection:
            stamp = now_utc()
            self._expire_leases(connection)
            job_id = connection.scalar(
                select(db.jobs.c.id)
                .where(db.jobs.c.status == "QUEUED")
                .order_by(db.jobs.c.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job_id is None:
                return None
            token = str(uuid4())
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(
                    status="ELIGIBILITY_CHECK",
                    current_stage="ELIGIBILITY_CHECK",
                    started_at=stamp,
                    updated_at=stamp,
                    worker_id=worker_id,
                    lease_token=token,
                    lease_until=stamp + timedelta(seconds=lease_seconds),
                )
            )
            self._audit(connection, job_id, "WORKER_CLAIMED")
            return Claim(job_id=job_id, token=token, worker_id=worker_id)

    def _expire_leases(self, connection: Connection, job_id: str | None = None) -> None:
        stamp = now_utc()
        query = select(db.jobs.c.id).where(
            db.jobs.c.status.not_in(TERMINAL),
            db.jobs.c.lease_until <= stamp,
        )
        if job_id is not None:
            query = query.where(db.jobs.c.id == job_id)
        expired = connection.scalars(query.with_for_update(skip_locked=True)).all()
        for expired_id in expired:
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == expired_id)
                .values(
                    status="FAILED",
                    current_stage="FAILED",
                    completed_at=stamp,
                    updated_at=stamp,
                    lease_token=None,
                    lease_until=None,
                    error_code="WORKER_LEASE_EXPIRED",
                    error_message="The research worker stopped responding; submit a new research job.",
                )
            )
            self._audit(connection, expired_id, "WORKER_LEASE_EXPIRED")

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
            if row["status"] != "SNAPSHOT_BUILD" or row["snapshot_id"] is not None:
                raise StoreError("A snapshot can only be frozen once during snapshot building")
            if data["ticker"] != row["ticker"]:
                raise StoreError("Snapshot ticker differs from the research job")
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
            firms = frozenset(
                connection.scalars(
                    select(db.firm_reports.c.firm).where(db.firm_reports.c.job_id == job_id)
                )
            )
            if row["status"] != "FIRST_PASS_RESEARCH" or firms != FIRST_PASS_FIRMS:
                raise BarrierNotLocked(
                    "All three independent reports must be persisted before locking"
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
        with self.engine.connect() as connection:
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
                for firm in sorted(FIRST_PASS_FIRMS)
            },
            "artifacts": extras,
        }

    def get_evidence(self, job_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            snapshot = connection.scalar(
                select(db.snapshots.c.payload).where(db.snapshots.c.job_id == job_id)
            )
            items = list(
                connection.scalars(
                    select(db.evidence.c.payload).where(db.evidence.c.job_id == job_id)
                )
            )
            return {"snapshot": snapshot, "evidence": items}

    def save_artifact(self, job_id: str, kind: str, value: Any) -> None:
        if kind not in {
            "eligibility",
            "discovery",
            "lean",
            "audit",
            "red_team",
            "independence",
            "cross_examination",
            "consensus",
            "token_usage",
        }:
            raise StoreError("Unknown research artifact")
        data = payload(value)
        with self.transaction() as connection:
            row = self._owned_job(connection, job_id)
            if kind not in {"eligibility", "discovery", "token_usage"} and row["locked_at"] is None:
                raise BarrierNotLocked("Downstream artifacts require locked first-pass reports")
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
                calculated_independence = evidence_independence(
                    authoritative_snapshot, validated_packet.reports
                )
                if calculated_independence != validated_packet.independence:
                    raise StoreError(
                        "Decision packet evidence independence was not derived from its sources"
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
                )
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

    def list_signals(self, *, expired: bool = False) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(db.signals).order_by(db.signals.c.created_at.desc())
            ).mappings()
            result = []
            for row in rows:
                state = (
                    ResearchSignal.model_validate(row["payload"]).effective_state(now_utc()).value
                )
                if (state == "EXPIRED") == expired:
                    result.append({**serialize(row), "final_state": state})
                if len(result) >= 100:
                    break
            return result

    def list_outcomes(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                serialize(row)
                for row in connection.execute(
                    select(db.outcomes).order_by(db.outcomes.c.created_at.desc()).limit(100)
                ).mappings()
            ]

    def list_discovery(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                serialize(row)
                for row in connection.execute(
                    select(db.discoveries).order_by(db.discoveries.c.created_at.desc()).limit(100)
                ).mappings()
            ]
