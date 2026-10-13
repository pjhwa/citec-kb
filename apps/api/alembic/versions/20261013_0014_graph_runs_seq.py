"""add graph_runs.seq (F04, 2026-10-13 final handoff -- deterministic
"latest run" ordering)

Revision ID: 20261013_0014
Revises: 20261013_0013

Purely additive. Postgres's now()/CURRENT_TIMESTAMP returns the same value
for every statement inside one transaction (unlike clock_timestamp()), so
two GraphRun rows inserted in the same session_scope() tie on started_at --
ORDER BY started_at DESC cannot reliably pick the later one (confirmed via
test_graph_router.py::test_graph_status_ignores_partial_run_for_generation_matches
failing deterministically against a real DB).

A plain `ADD COLUMN seq BIGSERIAL` was tried first and is WRONG: BIGSERIAL
numbers existing rows in whatever physical heap order Postgres happens to
scan them in, not in started_at/insertion order -- on a table with real
history (prod) that can silently reorder which run looks "latest" right
after this migration runs, exactly the bug this migration exists to fix.
Instead: add a plain BIGINT, backfill it explicitly ordered by
(started_at, completed_at NULLS LAST, run_id) via row_number(), then attach
a sequence (owned by the column) advanced past the backfilled max so new
inserts keep counting up from there.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261013_0014"
down_revision: Union[str, None] = "20261013_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE graph_runs ADD COLUMN seq BIGINT")
    # 기존 행을 "진짜 시간 순서"로 번호 매김 — heap 스캔 순서(BIGSERIAL
    # 기본 백필이 쓰는 순서)가 아니라 started_at/completed_at/run_id로 정렬.
    op.execute(
        """
        WITH ordered AS (
            SELECT run_id,
                   row_number() OVER (
                       ORDER BY started_at, completed_at NULLS LAST, run_id
                   ) AS rn
            FROM graph_runs
        )
        UPDATE graph_runs
        SET seq = ordered.rn
        FROM ordered
        WHERE graph_runs.run_id = ordered.run_id
        """
    )
    op.execute("CREATE SEQUENCE IF NOT EXISTS graph_runs_seq_seq OWNED BY graph_runs.seq")
    op.execute(
        "SELECT setval('graph_runs_seq_seq', COALESCE((SELECT max(seq) FROM graph_runs), 0) + 1, false)"
    )
    op.execute("ALTER TABLE graph_runs ALTER COLUMN seq SET DEFAULT nextval('graph_runs_seq_seq')")
    op.execute("ALTER TABLE graph_runs ALTER COLUMN seq SET NOT NULL")
    op.execute("ALTER TABLE graph_runs ADD CONSTRAINT graph_runs_seq_key UNIQUE (seq)")


def downgrade() -> None:
    # 컬럼을 먼저 지워야 그 DEFAULT가 쥐고 있던 시퀀스 의존성이 없어진다
    # (순서를 바꾸면 "시퀀스에 의존하는 DEFAULT가 남아있다"는 에러가 난다).
    op.execute("ALTER TABLE graph_runs DROP CONSTRAINT IF EXISTS graph_runs_seq_key")
    op.execute("ALTER TABLE graph_runs DROP COLUMN IF EXISTS seq")
    op.execute("DROP SEQUENCE IF EXISTS graph_runs_seq_seq")
