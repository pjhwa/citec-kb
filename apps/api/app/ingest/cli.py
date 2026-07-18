"""CLI: python -m app.ingest.cli --raw-dir /data/raw"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest raw knowledge corpus")
    parser.add_argument(
        "--raw-dir",
        default=os.getenv("RAW_DIR", "/data/raw"),
        help="Path to raw corpus root",
    )
    parser.add_argument(
        "--sources",
        default="",
        help="Comma list: support_history,tech_repo,confluence_docs,tuning_ai,checkitem",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max documents (debug)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from app.ingest.pipeline import run_ingest

    sources = [s.strip() for s in args.sources.split(",") if s.strip()] or None
    stats = run_ingest(args.raw_dir, sources=sources, limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
