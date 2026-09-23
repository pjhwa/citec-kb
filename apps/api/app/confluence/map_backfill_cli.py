"""CLI: python -m app.confluence.map_backfill_cli

One-shot backfill of every active confluence_map source: full re-crawl
(excerpt + tech tag), one ingest, then embed until no map chunk is pending.
Safe to re-run; finished sources are skipped. Does not touch last_sync_at.

    docker exec citec-kb-api-1 python -m app.confluence.map_backfill_cli --dry-run
    docker exec -d citec-kb-api-1 python -m app.confluence.map_backfill_cli

Progress is appended to data/raw/confluence_map/.backfill.log and the
state file .backfill_state.json next to it. Exit 0 only when every source
crawled cleanly, ingest reported no errors, and pending map embeddings are 0.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="confluence_map 전체 백필 (크롤 → 인제스트 → 임베딩)")
    parser.add_argument("--raw-dir", default=os.getenv("RAW_DIR", "/data/raw"))
    parser.add_argument(
        "--source-ids",
        default="",
        help="쉼표 구분. 비우면 status=active 인 confluence_map 소스 전부",
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="완료된 소스도 다시 크롤한다",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="소스 목록과 예상 시간만 출력. Confluence 를 호출하지 않음",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    log_path = Path(args.raw_dir) / "confluence_map" / ".backfill.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )

    logging.info("map backfill cli start raw_dir=%s args=%s", args.raw_dir, args)
    from app.confluence.map_backfill import run_backfill

    source_ids = [s.strip() for s in args.source_ids.split(",") if s.strip()] or None
    try:
        report = run_backfill(
            args.raw_dir,
            source_ids=source_ids,
            resume=not args.from_scratch,
            dry_run=args.dry_run,
        )
    except Exception:
        logging.exception("map backfill crashed before a normal finish")
        raise
    logging.info("map backfill cli finish ok=%s", report.get("ok"))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if report.get("skipped"):
        return 2
    if args.dry_run:
        return 0
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
