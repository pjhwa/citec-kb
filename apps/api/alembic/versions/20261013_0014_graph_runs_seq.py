"""add graph_runs.seq (F04, 2026-10-13 final handoff -- deterministic
"latest run" ordering)

Revision ID: 20261013_0014
Revises: 20261013_0013

Purely additive. Postgres's now()/CURRENT_TIMESTAMP returns the same value
for every statement inside one transaction (unlike clock_timestamp()), so
two GraphRun rows inserted in the same session_scope() tie on started_at --
ORDER BY started_at DESC cannot reliably pick the later one (confirmed via
test_graph_router.py::test_graph_status_ignores_partial_run_for_generation_matches
failing deterministically against a real DB). BIGSERIAL gives every insert
a distinct, monotonically increasing value regardless of transaction
timing, so "latest run" queries switch to ordering by seq instead.
See docs/features/graph-evidence.md F04.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261013_0014"
down_revision: Union[str, None] = "20261013_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BIGSERIAL = BigInteger + an owned sequence + DEFAULT nextval(...) in
    # one step; Postgres backfills existing rows with sequential values.
    op.execute("ALTER TABLE graph_runs ADD COLUMN seq BIGSERIAL UNIQUE")


def downgrade() -> None:
    op.execute("ALTER TABLE graph_runs DROP COLUMN seq")
