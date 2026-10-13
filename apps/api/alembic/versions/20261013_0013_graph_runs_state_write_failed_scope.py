"""add graph_runs.documents_state_write_failed + graph_runs.scope (R02,
2026-10-13 2nd handoff — distinguish merge success from state-write success,
and a partial debug run from a full batch)

Revision ID: 20261013_0013
Revises: 20261013_0012

Purely additive. See docs/features/graph-evidence.md R02.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0013"
down_revision: Union[str, None] = "20261013_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "graph_runs",
        sa.Column("documents_state_write_failed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "graph_runs",
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="full"),
    )


def downgrade() -> None:
    op.drop_column("graph_runs", "scope")
    op.drop_column("graph_runs", "documents_state_write_failed")
