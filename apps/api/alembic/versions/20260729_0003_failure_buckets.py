"""add failure_buckets table

Revision ID: 20260729_0003
Revises: 20260718_0002
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0003"
down_revision: Union[str, None] = "20260718_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "failure_buckets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column("bucket_name", sa.String(length=256), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=True),
        sa.Column("symptom", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "discriminating_signals",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "counter_signals",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("root_cause", sa.Text(), server_default="", nullable=False),
        sa.Column("recommended_action", sa.Text(), server_default="", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("support_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("counter_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("evidence_grade", sa.String(length=8), server_default="machine", nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_failure_buckets_protocol", "failure_buckets", ["protocol"])
    op.create_index("ix_failure_buckets_bucket_name", "failure_buckets", ["bucket_name"])


def downgrade() -> None:
    op.drop_index("ix_failure_buckets_bucket_name", table_name="failure_buckets")
    op.drop_index("ix_failure_buckets_protocol", table_name="failure_buckets")
    op.drop_table("failure_buckets")
