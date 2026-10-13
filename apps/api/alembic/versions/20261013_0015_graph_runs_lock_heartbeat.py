"""add graph_runs.lock_holder_pid + graph_runs.heartbeat_at (F04, 2026-10-13
final handoff -- real liveness detection for "running" rows)

Revision ID: 20261013_0015
Revises: 20261013_0014

Purely additive. Lets /v1/graph/status check whether a 'running' row's
advisory-lock holder pid is still actually holding the lock (via pg_locks,
read-only), and whether a running sync_cli has made progress recently
(heartbeat_at). See docs/features/graph-evidence.md F04.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0015"
down_revision: Union[str, None] = "20261013_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("graph_runs", sa.Column("lock_holder_pid", sa.Integer(), nullable=True))
    op.add_column("graph_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("graph_runs", "heartbeat_at")
    op.drop_column("graph_runs", "lock_holder_pid")
