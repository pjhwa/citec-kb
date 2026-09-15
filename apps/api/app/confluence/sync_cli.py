"""CLI: python -m app.confluence.sync_cli [--dry-run] [--sources ...] [--max-pages N] [--root-id ID]

--dry-run writes data/raw/<source_type>/confluence_<pageId>.md files only —
no run_ingest/embed_pending_chunks, no Source.last_sync_at update. This is
the required safety net for the first run on a server whose Confluence
responses this code has never actually seen (see module docstring in
app.confluence.sync) — inspect the written files by eye before running
without --dry-run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Confluence 증분 동기화 (LOOKIN confluence_docs + TechRepo tech_repo)"
    )
    parser.add_argument(
        "--raw-dir",
        default=os.getenv("RAW_DIR", "/data/raw"),
        help="Path to raw corpus root",
    )
    parser.add_argument(
        "--sources",
        default="confluence_docs,tech_repo",
        help="Comma list: confluence_docs,tech_repo",
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
        help="단일 root pageId만 처리 (최초 dry-run 시 한 루트만 먼저 확인할 때)",
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

    from app.confluence.sync import sync

    sources = [s.strip() for s in args.sources.split(",") if s.strip()] or None
    stats = sync(
        args.raw_dir,
        dry_run=args.dry_run,
        sources=sources,
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
