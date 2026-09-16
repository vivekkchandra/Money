"""Append-only, tenant-attributed provider/native usage receipts.

Revision ID: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_metering",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("invocation_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(36)),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("reservation_id", sa.String(128), sa.ForeignKey("budget_reservations.id")),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("component", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(200)),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("provider_calls", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("actual_cost", sa.Numeric(24, 12)),
        sa.Column("estimated_cost", sa.Numeric(24, 12)),
        sa.Column("currency", sa.String(3)),
        sa.Column("cache_hit", sa.Boolean()),
        sa.Column("retry", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("invocation_id", "sequence", name="uq_call_invocation_sequence"),
    )
    op.create_index(
        "ix_call_metering_workspace_time", "call_metering", ["workspace_id", "created_at"]
    )
    op.create_index("ix_call_metering_job", "call_metering", ["job_id"])
    op.create_index("ix_call_metering_reservation", "call_metering", ["reservation_id", "kind"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER immutable_call_metering BEFORE UPDATE OR DELETE ON call_metering FOR EACH ROW EXECUTE FUNCTION money_reject_mutation()"
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER immutable_call_metering_{operation.lower()} BEFORE {operation} ON call_metering BEGIN SELECT RAISE(ABORT, 'Money call metering records are immutable'); END"
            )


def downgrade() -> None:
    raise RuntimeError("Call-accounting retention requires a reviewed forward migration")
