"""Subscription reconciliation, admission usage and customer workspace tools.

Revision ID: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical migrations must not import mutable application metadata: future
    # fields belong in a new migration, not silently in this release's revision.
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "subscriptions",
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("customer_id", sa.String(128), unique=True),
        sa.Column("subscription_id", sa.String(128), unique=True),
        sa.Column("checkout_key", sa.String(64)),
        sa.Column("checkout_plan", sa.String(20)),
        sa.Column("checkout_url", sa.String(8192)),
        sa.Column("checkout_until", sa.DateTime(timezone=True)),
        sa.Column("checkout_payload", document),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "usage_records",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), primary_key=True),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("max_seconds", sa.Integer(), nullable=False),
        sa.Column("data_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_workspace_period", "usage_records", ["workspace_id", "period_start"])
    op.create_index("ix_usage_user_time", "usage_records", ["user_id", "created_at"])
    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("subscription_id", sa.String(128)),
        sa.Column("customer_id", sa.String(128)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_billing_event_queue", "billing_events", ["state", "available_at"])
    op.create_table(
        "customer_watchlist",
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("ticker", sa.String(32), primary_key=True),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_reads",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("notification_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "product_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("reference_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_product_events_workspace_time", "product_events", ["workspace_id", "created_at"]
    )
    op.create_table(
        "workspace_preferences",
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("payload", document, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("usage_records", "product_events"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                f"CREATE TRIGGER immutable_{name} BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION money_reject_mutation()"
            )
        else:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER immutable_{name}_{operation.lower()} BEFORE {operation} ON {name} BEGIN SELECT RAISE(ABORT, 'Money audit records are immutable'); END"
                )


def downgrade() -> None:
    raise RuntimeError("Commercial audit/usage retention requires a reviewed forward migration")
