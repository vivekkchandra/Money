"""Add commercial identity without changing existing research records.

Revision ID: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fixed release definitions: future changes require additive migrations.
    doc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True)),
        sa.Column("email_notifications", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "organisations",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "organisation_members",
        sa.Column(
            "workspace_id", sa.String(80), sa.ForeignKey("organisations.id"), primary_key=True
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('OWNER', 'ADMIN', 'MEMBER', 'VIEWER')", name="ck_member_role"),
    )
    op.create_index("ix_members_user", "organisation_members", ["user_id", "workspace_id"])
    op.create_table(
        "account_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_account_sessions_user", "account_sessions", ["user_id", "expires_at"])
    op.create_table(
        "account_action_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_action_tokens_user", "account_action_tokens", ["user_id", "purpose"])
    op.create_table(
        "account_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("workspace_id", sa.String(80), sa.ForeignKey("organisations.id")),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("payload", doc, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_account_audit_workspace_time", "account_audit_events", ["workspace_id", "created_at"]
    )
    op.create_table(
        "transactional_email_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
    )
    op.create_index(
        "ix_transactional_email_delivery", "transactional_email_outbox", ["state", "available_at"]
    )
    op.create_table(
        "organisation_invitations",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(80), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("invited_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('ADMIN', 'MEMBER', 'VIEWER')", name="ck_invitation_role"),
    )
    op.create_index(
        "ix_invitations_workspace", "organisation_invitations", ["workspace_id", "email"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "CREATE TRIGGER immutable_account_audit BEFORE UPDATE OR DELETE ON account_audit_events FOR EACH ROW EXECUTE FUNCTION money_reject_mutation()"
            )
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER immutable_account_audit_{operation.lower()} BEFORE {operation} ON account_audit_events BEGIN SELECT RAISE(ABORT, 'Account audit records are immutable'); END"
                )
            )


def downgrade() -> None:
    raise RuntimeError("Commercial identity rollback requires an explicit retention-safe migration")
