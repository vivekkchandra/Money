"""Durable account offboarding and explicit reviewed retention fulfilment."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactional_email_outbox", sa.Column("recipient_hash", sa.String(64)))
    op.create_index(
        "ix_transactional_email_recipient", "transactional_email_outbox", ["recipient_hash"]
    )
    op.create_table(
        "account_deletion_requests",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(80)),
        sa.Column("policy_hash", sa.String(64)),
        sa.Column("reviewed_by", sa.String(128)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("erasure_after", sa.DateTime(timezone=True)),
        sa.Column("erased_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('REQUESTED', 'REVIEWED', 'ERASED')", name="ck_deletion_state"
        ),
    )
    op.create_index(
        "ix_deletion_fulfilment", "account_deletion_requests", ["state", "erasure_after"]
    )
    op.create_index("ix_deletion_recipient", "account_deletion_requests", ["recipient_hash"])
    op.create_table(
        "workspace_closures",
        sa.Column(
            "workspace_id", sa.String(80), sa.ForeignKey("organisations.id"), primary_key=True
        ),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("customer_id", sa.String(128)),
        sa.Column("subscription_id", sa.String(128)),
        sa.Column("cancellation_key", sa.String(64), nullable=False, unique=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("billing_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("billing_reviewed_by", sa.String(128)),
        sa.CheckConstraint(
            "state IN ('PENDING', 'SCHEDULED', 'CLOSED', 'FAILED')",
            name="ck_workspace_closure_state",
        ),
    )
    op.create_index("ix_workspace_closure_queue", "workspace_closures", ["state", "available_at"])


def downgrade() -> None:
    raise RuntimeError("Offboarding and retention evidence requires a reviewed forward migration")
