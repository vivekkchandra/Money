"""Budget admission, provider circuits, model registry, alerts and immutable replay.

Revision ID: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    doc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("candidate", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_tokens", sa.Integer()),
        sa.Column("payload", doc, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_budget_workspace_time", "budget_reservations", ["workspace_id", "created_at"]
    )
    op.create_index("ix_budget_job", "budget_reservations", ["job_id"])
    op.create_table(
        "model_registry",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("artifact_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", doc, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_promotions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("model_id", sa.String(128), sa.ForeignKey("model_registry.id"), nullable=False),
        sa.Column("payload", doc, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_promotion_time", "model_promotions", ["model_id", "created_at"])
    op.create_table(
        "provider_state",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("open_until", sa.DateTime(timezone=True)),
        sa.Column("payload", doc, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "alert_outbox",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("payload", doc, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alert_delivery", "alert_outbox", ["state", "created_at"])
    op.create_table(
        "replay_runs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.Column("payload", doc, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("model_registry", "model_promotions", "replay_runs"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                sa.text(
                    f"CREATE TRIGGER immutable_{name} BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION money_reject_mutation()"
                )
            )
        else:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"CREATE TRIGGER immutable_{name}_{operation.lower()} BEFORE {operation} ON {name} BEGIN SELECT RAISE(ABORT, 'Money research records are immutable'); END"
                    )
                )


def downgrade() -> None:
    for name in (
        "replay_runs",
        "alert_outbox",
        "provider_state",
        "model_promotions",
        "model_registry",
        "budget_reservations",
    ):
        op.drop_table(name)
