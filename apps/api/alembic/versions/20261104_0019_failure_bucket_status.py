"""add failure_buckets.status / retired_* (2026-11-04 soft retire + test registrations)

Revision ID: 20261104_0019
Revises: 20261103_0018

Existing rows become status='active' via the server default. Rows are never
deleted by this feature; retire only hides, restore brings back.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_0019"
down_revision: Union[str, None] = "20261103_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "failure_buckets",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column("failure_buckets", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("failure_buckets", sa.Column("retired_by", sa.String(128), nullable=True))
    op.add_column("failure_buckets", sa.Column("retire_reason", sa.Text(), nullable=True))
    op.create_index("ix_failure_buckets_status", "failure_buckets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_failure_buckets_status", table_name="failure_buckets")
    op.drop_column("failure_buckets", "retire_reason")
    op.drop_column("failure_buckets", "retired_by")
    op.drop_column("failure_buckets", "retired_at")
    op.drop_column("failure_buckets", "status")
