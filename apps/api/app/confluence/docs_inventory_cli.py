"""CLI: python -m app.confluence.docs_inventory_cli [--dry-run] [--sources ...]

Weekly reconciliation of confluence_docs / tech_repo against Confluence
(moved, renamed, newly tracked and moved-out/deleted pages). See
app.confluence.docs_inventory. Always run --dry-run first and read
``would_archive`` before the first real run.

    docker compose exec -T api python -m app.confluence.docs_inventory_cli --dry-run
    docker compose exec -T api python -m app.confluence.docs_inventory_cli
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    from app.confluence.docs_inventory import DEFAULT_MAX_ARCHIVE_RATIO

    parser = argparse.ArgumentParser(
        description="confluence_docs / tech_repo 주간 전체 대조 (이동·이름변경·신규·이동-밖/삭제 반영)"
    )
    parser.add_argument("--raw-dir", default=os.getenv("RAW_DIR", "/data/raw"))
    parser.add_argument("--sources", default="confluence_docs,tech_repo", help="Comma list: confluence_docs,tech_repo")
    parser.add_argument("--dry-run", action="store_true", help="목록 조회와 비교만. 파일/DB 쓰기 없음")
    parser.add_argument("--no-archive", action="store_true", help="목록에서 사라진 문서를 보관 처리하지 않음")
    parser.add_argument(
        "--max-archive-ratio",
        type=float,
        default=DEFAULT_MAX_ARCHIVE_RATIO,
        help="보관 후보가 활성 문서의 이 비율을 넘으면 보관 처리를 건너뜀 (기본 %(default)s)",
    )
    parser.add_argument(
        "--allow-full-fetch",
        action="store_true",
        help="검색 결과에 조상 정보가 없는 페이지도 전부 다시 가져옴 (사실상 전체 크롤, 수 시간)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from app.confluence.docs_inventory import run_docs_inventory

    report = run_docs_inventory(
        args.raw_dir,
        sources=[s.strip() for s in args.sources.split(",") if s.strip()] or None,
        dry_run=args.dry_run,
        archive=not args.no_archive,
        max_archive_ratio=args.max_archive_ratio,
        allow_full_fetch=args.allow_full_fetch,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    errors = 0
    for s in report.get("sources", {}).values():
        if isinstance(s, dict):
            errors += len(s.get("listing_errors") or []) + len(s.get("fetch_errors") or [])
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
