"""CLI: python -m app.frames.cli"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract issue frames from support tickets")
    p.add_argument("--source-type", default="support_history")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--min-quality", type=float, default=0.0)
    p.add_argument("--force", action="store_true", help="Re-extract even if frame exists")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    from app.frames.job import extract_frames

    stats = extract_frames(
        source_type=args.source_type,
        limit=args.limit,
        min_quality=args.min_quality,
        force=args.force,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
