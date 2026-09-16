"""Tenant-owned subscriptions, immutable usage and customer product records."""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Table

from money.storage.models import document, metadata

subscriptions = Table(
    "subscriptions",
    metadata,
    Column("workspace_id", String(80), primary_key=True),
    Column("plan", String(20), nullable=False),
    Column("status", String(32), nullable=False),
    Column("customer_id", String(128), unique=True),
    Column("subscription_id", String(128), unique=True),
    Column("checkout_key", String(64)),
    Column("checkout_plan", String(20)),
    Column("checkout_url", String(8192)),
    Column("checkout_until", DateTime(timezone=True)),
    Column("checkout_payload", document),
    Column("period_start", DateTime(timezone=True), nullable=False),
    Column("period_end", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
usage_records = Table(
    "usage_records",
    metadata,
    Column("job_id", ForeignKey("research_jobs.id"), primary_key=True),
    Column("workspace_id", String(80), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("period_start", DateTime(timezone=True), nullable=False),
    Column("period_end", DateTime(timezone=True), nullable=False),
    Column("plan", String(20), nullable=False),
    Column("max_tokens", Integer, nullable=False),
    Column("max_seconds", Integer, nullable=False),
    Column("data_version", String(80), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_usage_workspace_period", usage_records.c.workspace_id, usage_records.c.period_start)
Index("ix_usage_user_time", usage_records.c.user_id, usage_records.c.created_at)
billing_events = Table(
    "billing_events",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("kind", String(100), nullable=False),
    Column("subscription_id", String(128)),
    Column("customer_id", String(128)),
    Column("content_hash", String(64), nullable=False),
    Column("state", String(24), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("processed_at", DateTime(timezone=True)),
)
Index("ix_billing_event_queue", billing_events.c.state, billing_events.c.available_at)
watchlist = Table(
    "customer_watchlist",
    metadata,
    Column("workspace_id", String(80), primary_key=True),
    Column("ticker", String(32), primary_key=True),
    Column("created_by", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
notification_reads = Table(
    "notification_reads",
    metadata,
    Column("user_id", String(36), primary_key=True),
    Column("notification_id", String(128), primary_key=True),
    Column("workspace_id", String(80), nullable=False),
    Column("read_at", DateTime(timezone=True), nullable=False),
)
product_events = Table(
    "product_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("workspace_id", String(80), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("event", String(64), nullable=False),
    Column("reference_id", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index(
    "ix_product_events_workspace_time", product_events.c.workspace_id, product_events.c.created_at
)
preferences = Table(
    "workspace_preferences",
    metadata,
    Column("workspace_id", String(80), primary_key=True),
    Column("payload", document, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
