"""Durable jobs, immutable evidence/reports/packets and process health.

Revision ID: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

IMMUTABLE = (
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
RECORDS = (
    "eligibility",
    "candidate_discovery",
    "research_snapshots",
    "evidence",
    "research_outcomes",
    "token_usage",
)


def document() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def record(name: str, *, job: bool = False) -> None:
    columns = [sa.Column("id", sa.String(128), primary_key=True)]
    if job:
        columns.append(
            sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), nullable=False)
        )
    columns.extend(
        [
            sa.Column("payload", document(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        ]
    )
    op.create_table(name, *columns)


def upgrade() -> None:
    for name in ("profiles", "research_mandates", "instruments"):
        record(name)
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column(
            "mandate_id", sa.String(128), sa.ForeignKey("research_mandates.id"), nullable=False
        ),
        sa.Column("candidate_id", sa.String(128)),
        sa.Column("snapshot_id", sa.String(128)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_stage", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.String(512)),
    )
    op.create_index("ix_jobs_queue", "research_jobs", ["status", "created_at"])
    op.create_index("ix_jobs_lease", "research_jobs", ["lease_until"])
    for name in RECORDS:
        record(name, job=True)
    op.create_table(
        "firm_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), nullable=False),
        sa.Column("firm", sa.String(32), nullable=False),
        sa.Column("payload", document(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "firm", name="uq_first_pass_report"),
    )
    op.create_table(
        "research_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", document(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "kind", name="uq_research_artifact"),
    )
    op.create_table(
        "decision_packets",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), primary_key=True),
        sa.Column("payload", document(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "research_signals",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id"), primary_key=True),
        sa.Column("payload", document(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("manual_trade_records", "component_performance"):
        record(name)
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.id")),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("payload", document(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "service_health",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("healthy", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("queue_control", sa.Column("id", sa.Integer(), primary_key=True))
    op.execute(sa.text("INSERT INTO queue_control (id) VALUES (1)"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("""
            CREATE FUNCTION money_reject_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Money research records are immutable';
            END; $$
        """)
        )
        for name in IMMUTABLE:
            op.execute(
                sa.text(
                    f"CREATE TRIGGER immutable_{name} BEFORE UPDATE OR DELETE ON {name} "
                    "FOR EACH ROW EXECUTE FUNCTION money_reject_mutation()"
                )
            )
    elif op.get_bind().dialect.name == "sqlite":
        for name in IMMUTABLE:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"CREATE TRIGGER immutable_{name}_{operation.lower()} BEFORE {operation} ON {name} "
                        "BEGIN SELECT RAISE(ABORT, 'Money research records are immutable'); END"
                    )
                )


def downgrade() -> None:
    for name in (
        "queue_control",
        "service_health",
        "audit_events",
        "component_performance",
        "manual_trade_records",
        "research_signals",
        "decision_packets",
        "research_artifacts",
        "firm_reports",
        *reversed(RECORDS),
        "research_jobs",
        "instruments",
        "research_mandates",
        "profiles",
    ):
        op.drop_table(name)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP FUNCTION money_reject_mutation()"))
