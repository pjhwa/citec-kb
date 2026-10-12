"""add graph_sync_state.graph_generation + graph_runs (G05 — generation-aware
freshness so a new Neo4j target doesn't inherit a stale PG skip-state, and a
real batch-run record instead of just max(synced_at))

Revision ID: 20261012_0011
Revises: 20261008_0010

Purely additive. See
docs/superpowers/specs/2026-10-12-graph-correctness-decisions.md §G05.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0011"
down_revision: Union[str, None] = "20261008_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "graph_sync_state",
        sa.Column("graph_generation", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "graph_runs",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("generation", sa.String(length=64), nullable=False),
        sa.Column("lexicon_version", sa.String(length=64), nullable=True),
        sa.Column("documents_synced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("buckets_synced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("buckets_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unresolved_references", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hub_recompute_ok", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
    )


def downgrade() -> None:
    op.drop_table("graph_runs")
    op.drop_column("graph_sync_state", "graph_generation")
