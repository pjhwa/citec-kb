"""add graph_sync_state (knowledge-graph backfill change detection)

Revision ID: 20261008_0010
Revises: 20260930_0009

Purely additive — no existing table touched. See
docs/superpowers/specs/2026-10-07-knowledge-graph-design.md §3.1.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0010"
down_revision: Union[str, None] = "20260930_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graph_sync_state",
        sa.Column("document_id", sa.String(length=64), primary_key=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("graph_extractor_version", sa.String(length=32), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("graph_sync_state")
