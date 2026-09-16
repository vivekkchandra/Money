"""Operational schema extensions, separated from queue ownership implementation."""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Table

from money.storage.models import document, metadata

budget_reservations = Table(
    "budget_reservations",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("job_id", ForeignKey("research_jobs.id"), nullable=False),
    Column("workspace_id", String(128), nullable=False),
    Column("candidate", String(32), nullable=False),
    Column("stage", String(64), nullable=False),
    Column("agent", String(128), nullable=False),
    Column("provider", String(128), nullable=False),
    Column("model", String(200), nullable=False),
    Column("reserved_tokens", Integer, nullable=False),
    Column("actual_tokens", Integer),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index(
    "ix_budget_workspace_time", budget_reservations.c.workspace_id, budget_reservations.c.created_at
)
Index("ix_budget_job", budget_reservations.c.job_id)

model_registry = Table(
    "model_registry",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("artifact_hash", String(64), nullable=False, unique=True),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
model_promotions = Table(
    "model_promotions",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("model_id", ForeignKey("model_registry.id"), nullable=False),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_model_promotion_time", model_promotions.c.model_id, model_promotions.c.created_at)

provider_state = Table(
    "provider_state",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("failures", Integer, nullable=False),
    Column("open_until", DateTime(timezone=True)),
    Column("payload", document, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

alert_outbox = Table(
    "alert_outbox",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("job_id", ForeignKey("research_jobs.id"), nullable=False),
    Column("workspace_id", String(128), nullable=False),
    Column("channel", String(32), nullable=False),
    Column("state", String(32), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
)
Index("ix_alert_delivery", alert_outbox.c.state, alert_outbox.c.created_at)

replay_runs = Table(
    "replay_runs",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("job_id", ForeignKey("research_jobs.id"), nullable=False),
    Column("original_hash", String(64), nullable=False),
    Column("payload", document, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
