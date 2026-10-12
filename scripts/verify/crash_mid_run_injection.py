"""G05 — 루프 중 예상 못 한 예외가 나면 graph_runs가 'running'에 영원히 멈추지
않고 'failed'로 전환되는지 확인. scratch Neo4j+Postgres에서만 실행
(환경변수는 README.md 참고)."""

from __future__ import annotations

import sys

from sqlalchemy import select

import app.graph.sync_cli as cli
from app.db.models import GraphRun
from app.db.session import session_scope


def boom(*args, **kwargs):
    raise RuntimeError("simulated catastrophic failure (verify script)")


def main() -> int:
    cli.sync_document = boom
    try:
        cli.main([])
    except RuntimeError:
        pass
    else:
        print("expected RuntimeError to propagate, but main() returned normally", file=sys.stderr)
        return 1

    with session_scope() as session:
        last_run = session.scalar(select(GraphRun).order_by(GraphRun.started_at.desc()))
        status = last_run.status if last_run is not None else None
    print(f"last run status: {status}", file=sys.stderr)
    assert status == "failed", "run must not be left 'running'"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
