"""Money-owned durable records. JSON payloads keep upstream objects out of storage."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
document = JSON().with_variant(JSONB(), "postgresql")


def record_table(name: str, *, job: bool = False) -> Table:
    columns: list[Column] = [Column("id", String(128), primary_key=True)]
    if job:
        columns.append(Column("job_id", ForeignKey("research_jobs.id"), nullable=False))
    columns.extend(
        [
            Column("payload", document, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        ]
    )
    return Table(name, metadata, *columns)


profiles = record_table("profiles")
mandates = record_table("research_mandates")
instruments = record_table("instruments")
jobs = Table(
    "research_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("ticker", String(32), nullable=False),
    Column("mandate_id", ForeignKey("research_mandates.id"), nullable=False),
    Column("candidate_id", String(128), nullable=True),
    Column("snapshot_id", String(128), nullable=True),
    Column("status", String(40), nullable=False),
    Column("current_stage", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("locked_at", DateTime(timezone=True), nullable=True),
    Column("worker_id", String(128), nullable=True),
    Column("lease_token", String(36), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("error_code", String(80), nullable=True),
    Column("error_message", String(512), nullable=True),
)
Index("ix_jobs_queue", jobs.c.status, jobs.c.created_at)
Index("ix_jobs_lease", jobs.c.lease_until)
eligibility = record_table("eligibility", job=True)
discoveries = record_table("candidate_discovery", job=True)
snapshots = record_table("research_snapshots", job=True)
evidence = record_table("evidence", job=True)
firm_reports = Table(
    "firm_reports",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", ForeignKey("research_jobs.id"), nullable=False),
    Column("firm", String(32), nullable=False),
    Column("payload", document, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("job_id", "firm", name="uq_first_pass_report"),
)
artifacts = Table(
    "research_artifacts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", ForeignKey("research_jobs.id"), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("job_id", "kind", name="uq_research_artifact"),
)
packets = Table(
    "decision_packets",
    metadata,
    Column("job_id", ForeignKey("research_jobs.id"), primary_key=True),
    Column("payload", document, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
signals = Table(
    "research_signals",
    metadata,
    Column("job_id", ForeignKey("research_jobs.id"), primary_key=True),
    Column("payload", document, nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
outcomes = record_table("research_outcomes", job=True)
manual_trades = record_table("manual_trade_records")
component_performance = record_table("component_performance")
token_usage = record_table("token_usage", job=True)
audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", ForeignKey("research_jobs.id"), nullable=True),
    Column("event", String(80), nullable=False),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
service_health = Table(
    "service_health",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("healthy", Boolean, nullable=False),
    Column("mode", String(32), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
queue_control = Table(
    "queue_control",
    metadata,
    Column("id", Integer, primary_key=True),
)

IMMUTABLE_TABLES = (
    "research_mandates",
    "research_snapshots",
    "evidence",
    "firm_reports",
    "research_artifacts",
    "decision_packets",
    "research_signals",
    "research_outcomes",
    "audit_events",
)
