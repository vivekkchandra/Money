"""Isolate personal public-data work from the qualified commercial queue.

Existing immutable snapshots/results stay in place. No reference data moves.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_jobs",
        sa.Column("research_kind", sa.String(16), nullable=False, server_default="standard"),
    )
    op.create_index(
        "ix_jobs_kind_queue", "research_jobs", ["research_kind", "status", "created_at"]
    )


def downgrade() -> None:
    raise RuntimeError("R&D/commercial isolation requires a reviewed forward migration")
