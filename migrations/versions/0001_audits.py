"""audits and batches

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(128), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), unique=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id")),
        sa.Column("target", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("score", sa.Integer),
        sa.Column("report", sa.JSON),
        sa.Column("blocked", sa.JSON),
        sa.Column("scorecard_html", sa.Text),
        sa.Column("scorecard_pdf", sa.LargeBinary),
        sa.Column("error", sa.Text),
        sa.Column("seconds", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_audits_batch_id", "audits", ["batch_id"])
    op.create_index("ix_audits_status", "audits", ["status"])
    op.create_index("ix_audits_created_at", "audits", ["created_at"])


def downgrade() -> None:
    op.drop_table("audits")
    op.drop_table("batches")
