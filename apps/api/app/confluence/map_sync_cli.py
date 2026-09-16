"""CLI: python -m app.confluence.map_sync_cli [--dry-run] [--source-ids ...] [--max-pages N] [--root-id ID]

Structure-only sibling of app.confluence.sync_cli — see app.confluence.
map_sync module docstring for why this is a separate crawl (per-space
cursors) instead of an extra `sources=` value on the existing sync().

--dry-run writes data/raw/confluence_map/confluence_map_<pageId>.md files
only — no run_ingest/embed_pending_chunks, no Source.last_sync_at update.
Same first-run safety net rule as sync_cli.py: inspect a small --max-pages
run by eye before letting it loose on a full space.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    from app.confluence.map_sync import MAP_SOURCE_DEFS

    parser = argparse.ArgumentParser(
        description="Confluence 맵(구조 전용) 증분 동기화 — 5개 신규 공간"
    )
    parser.add_argument(
        "--raw-dir",
        default=os.getenv("RAW_DIR", "/data/raw"),
        help="Path to raw corpus root",
    )
    parser.add_argument(
        "--source-ids",
        default=",".join(MAP_SOURCE_DEFS.keys()),
        help=f"Comma list of source_id, e.g. {next(iter(MAP_SOURCE_DEFS))}",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="루트당 최대 처리 페이지 수 (최초 dry-run 시 소규모 검증용)",
    )
    parser.add_argument(
        "--root-id",
        default=None,
        help="단일 root pageId만 처리 (모든 source-id 중 그 root_id를 가진 것만 대상)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일만 쓰고 run_ingest/embed_pending_chunks 및 last_sync_at 갱신은 하지 않음",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from app.confluence.map_sync import sync_map

    source_ids = [s.strip() for s in args.source_ids.split(",") if s.strip()] or None
    stats = sync_map(
        args.raw_dir,
        dry_run=args.dry_run,
        source_ids=source_ids,
        max_pages_per_root=args.max_pages,
        root_id=args.root_id,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))

    total_errors = sum(
        s.get("errors", 0) for s in stats.get("sources", {}).values() if isinstance(s, dict)
    )
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
