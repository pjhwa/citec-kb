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
    p.add_argument(
        "--citec-domains",
        action="store_true",
        help=(
            "Run extract_citec_domains() instead of extract_frames() — tags "
            "issue_frames.citec_domains/severity_tier (CI-TEC 11-domain "
            "dashboard lens) for --source-type (default incident_reports "
            "when this flag is set). --min-quality is ignored in this mode."
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    if args.citec_domains:
        from app.frames.job import extract_citec_domains

        source_type = (
            args.source_type if args.source_type != "support_history" else "incident_reports"
        )
        stats = extract_citec_domains(
            source_type=source_type,
            limit=args.limit,
            force=args.force,
        )
    else:
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
