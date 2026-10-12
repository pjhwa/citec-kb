"""G05 — hub 재집계 실패가 숨겨지지 않고 completed_warn/exit 1로 보이는지 확인.
scratch Neo4j+Postgres에서만 실행(환경변수는 README.md 참고)."""

from __future__ import annotations

import sys

import app.graph.sync_cli as cli


def boom(client):
    raise RuntimeError("simulated hub recompute failure (verify script)")


def main() -> int:
    cli.recompute_hub_flags = boom
    rc = cli.main([])
    print(f"exit code: {rc}", file=sys.stderr)
    assert rc == 1, "expected non-zero exit when hub recompute fails"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
