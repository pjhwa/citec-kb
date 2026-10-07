"""CLI: python -m app.confluence.map_inventory_cli

Weekly rotation runner for app.confluence.map_sync.run_map_inventory().

That function is a *full* re-crawl of one confluence_map source (same
cost as the initial bootstrap crawl / 2026-10 backfill) — it exists
specifically to catch pages that moved out of a root's subtree, got
relabeled, or were deleted, none of which the daily incremental
sync_map() (CQL lastmodified > cursor) can ever detect: an absence is
invisible to a "what changed since X" query. The 2026-10 backfill round
hit exactly this gap live — DevOps001 ended up with ~184 stale documents
that were neither refreshed nor archived because no periodic full
reconciliation had ever run for it.

Running every active source's full inventory every week would repeat
the *entire* backfill's page volume (~78k pages as of 2026-10) on a
weekly cadence — likely 10-20+ hours of Confluence traffic depending on
the rate limit, every single week, on a Confluence account other tools
also share. This CLI instead rotates a fixed-size slice (--count,
default 3) of the active source list each run, cycling through all of
them over several weeks (16 active sources / 3 per run ≈ one full cycle
every 6 runs). Intended to be cron'd weekly; each run only pays for a
few spaces' worth of time.

State: data/raw/confluence_map/.inventory_rotation_state.json tracks the
index into the (sorted) active-source-id list to resume from next run.
Only advanced on a real (non-dry-run, non --source-ids) run.

Deliberately does not touch last_sync_at/checkpoint — see
run_map_inventory()'s own docstring; that's the daily incremental sync's
bookkeeping and this must not perturb it.

    docker exec citec-kb-api-1 python -m app.confluence.map_inventory_cli --dry-run
    docker exec -d citec-kb-api-1 python -m app.confluence.map_inventory_cli --count 3
    docker exec -d citec-kb-api-1 python -m app.confluence.map_inventory_cli --source-ids confluence_map_devops001
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STATE_NAME = ".inventory_rotation_state.json"


def _state_path(raw_dir: str) -> Path:
    return Path(raw_dir) / "confluence_map" / _STATE_NAME


def _load_state(raw_dir: str) -> dict[str, Any]:
    path = _state_path(raw_dir)
    if not path.is_file():
        return {"index": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.warning("inventory rotation state unreadable, starting from index 0: %s", path)
        return {"index": 0}
    data.setdefault("index", 0)
    return data


def _save_state(raw_dir: str, state: dict[str, Any]) -> None:
    path = _state_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _pick_rotation(all_ids: list[str], index: int, count: int) -> tuple[list[str], int]:
    """Next `count` ids starting at `index`, wrapping around; never repeats
    within one pick unless count >= len(all_ids)."""
    n = len(all_ids)
    if n == 0:
        return [], 0
    index = index % n
    k = min(count, n)
    picked = [all_ids[(index + i) % n] for i in range(k)]
    next_index = (index + k) % n
    return picked, next_index


def main(argv: list[str] | None = None) -> int:
    from app.confluence.map_sync import get_source_defs

    parser = argparse.ArgumentParser(
        description="confluence_map 주간 전신(run_map_inventory) 로테이션 실행 — 한 번에 일부 공간만"
    )
    parser.add_argument("--raw-dir", default=os.getenv("RAW_DIR", "/data/raw"))
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="이번 실행에서 처리할 공간 수 (기본 3 — 전체를 몇 주에 걸쳐 한 바퀴)",
    )
    parser.add_argument(
        "--source-ids",
        default="",
        help="로테이션 대신 지정한 소스만 처리(쉼표 구분) — 지정 시 로테이션 상태는 건드리지 않음",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="app.confluence.map_sync.run_map_inventory의 dry_run 그대로 전달. 로테이션 상태도 전진하지 않음",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from app.confluence.map_sync import run_map_inventory

    explicit_ids = [s.strip() for s in args.source_ids.split(",") if s.strip()]
    active_defs = get_source_defs(active_only=True)
    all_ids = sorted(active_defs.keys())

    state: dict[str, Any] | None = None
    next_index: int | None = None

    if explicit_ids:
        unknown = [s for s in explicit_ids if s not in active_defs]
        if unknown:
            print(f"unknown or disabled confluence_map source_id(s): {unknown}", file=sys.stderr)
            return 64
        picked = explicit_ids
    else:
        state = _load_state(args.raw_dir)
        picked, next_index = _pick_rotation(all_ids, int(state.get("index") or 0), args.count)

    logging.info(
        "map inventory rotation picked=%s (of %d active sources, dry_run=%s)",
        picked, len(all_ids), args.dry_run,
    )

    report: dict[str, Any] = {"dry_run": args.dry_run, "active_sources_total": len(all_ids), "sources": {}}
    total_errors = 0
    for sid in picked:
        row = run_map_inventory(sid, args.raw_dir, dry_run=args.dry_run)
        report["sources"][sid] = row
        if row.get("skipped"):
            logging.warning("inventory skipped %s: %s", sid, row.get("reason"))
            continue
        total_errors += len(row.get("errors") or [])

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    if state is not None and not args.dry_run:
        state["index"] = next_index
        state["last_picked"] = picked
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(args.raw_dir, state)

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
