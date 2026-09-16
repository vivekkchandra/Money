"""Workspace ownership, idempotency, durable authentication and retry checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("workspace_id", sa.String(80), nullable=False, server_default="private"),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("resume_stage", sa.String(40)),
    ):
        op.add_column("research_jobs", column)
    op.create_index("ix_jobs_workspace_created", "research_jobs", ["workspace_id", "created_at"])
    op.create_index("ix_jobs_available", "research_jobs", ["status", "available_at"])
    op.create_index(
        "uq_jobs_idempotency", "research_jobs", ["workspace_id", "idempotency_key"], unique=True
    )
    op.create_table(
        "web_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("credential_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_sessions_expiry", "web_sessions", ["expires_at"])
    op.create_table(
        "rate_limits",
        sa.Column("bucket", sa.String(160), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_audit_job_created", "audit_events", ["job_id", "created_at"])
    op.create_index("ix_audit_event_job_time", "audit_events", ["event", "job_id", "created_at"])
    op.create_index("ix_evidence_job", "evidence", ["job_id"])
    op.create_index("ix_discovery_job", "candidate_discovery", ["job_id"])


def downgrade() -> None:
    # Production rollback retains durable security/audit state. Restore a backup
    # into a separate database for a deliberate schema rollback.
    raise RuntimeError("Security migration is forward-only; restore a reviewed backup")
