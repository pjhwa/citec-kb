#!/usr/bin/env python3
"""One-time bootstrap: convert the citec-mcp-workbench skill's already-
crawled Confluence page indices into data/raw/confluence_map/ entries, so
the live daily crawler (app.confluence.map_sync.sync_map) doesn't have to
re-crawl ~26,000 pages from scratch on its first run.

2026-09-16 (박재화, 2nd decision): LOOKIN/TechRepo/ServiceExcellenceTeam/
ICLOUDUT are **whole-space, continuously (daily) re-crawled** confluence_map
sources now — not a "curated-subtree remainder only" one-time snapshot like
the first version of this script. Concretely this means:
  - Every row in the input jsonl for these 4 spaces is written as a
    confluence_map entry, including pages that are ALSO already full-text
    ingested as confluence_docs/tech_repo. That overlap is intentional and
    harmless (see app.confluence.map_sync.MAP_SOURCE_DEFS comment) — the
    live crawler will keep re-scanning the whole space daily regardless, so
    excluding "already-ingested" rows here would just mean the live
    crawler re-adds them on its very first run anyway, for no benefit.
  - Use --seed-cursors after writing files (see below) so that first live
    run only asks Confluence for pages changed *since* this migration's
    snapshot date, instead of re-crawling all ~26,000 pages live.

Input (from ~/.claude/skills/citec-mcp-workbench/references/, not part of
this repo):
  - confluence-pages.jsonl      → LOOKIN, TechRepo, ServiceExcellenceTeam
                                   (crawled 2026-07-23)
  - confluence-pages-scp.jsonl  → ICLOUDUT (crawled 2026-07-30)
Row shape: {"pageId","title","spaceKey","path":[...ancestor titles...],
"lastModified","url",...} — `url` is already the full absolute webui URL.

Output: data/raw/confluence_map/confluence_map_<pageId>.md, written via a
frontmatter format that MUST match
app.confluence.map_sync.build_frontmatter_confluence_map() exactly — it is
duplicated here (see _build_frontmatter below) rather than imported because
importing app.confluence.map_sync pulls in app.db.models → SQLAlchemy,
which is only installed inside the citec-kb Docker containers, not on a
bare host running this script standalone. If you change one, change both —
apps/api/tests/test_confluence_map_sync.py pins the app-side shape and the
adapter's parsing of it; there's no cross-file test enforcing this script's
copy stays identical, so a manual diff is on the person making the change.

Usage:
    # 1. write the files (no DB access needed, works on a bare host)
    python scripts/migrate_confluence_map_from_skill_index.py \\
        --skill-refs ~/.claude/skills/citec-mcp-workbench/references \\
        --raw-dir data/raw \\
        --dry-run   # counts only, first
    python scripts/migrate_confluence_map_from_skill_index.py \\
        --raw-dir data/raw   # then for real

    # 2. ingest into the DB (wherever the citec-kb app + DB actually run)
    python -m app.ingest.cli --raw-dir data/raw --sources confluence_map

    # 3. seed each space's cursor so the live daily crawler only picks up
    #    pages changed after this snapshot (needs app/SQLAlchemy — run this
    #    step inside the citec-kb app environment, not on a bare host)
    python scripts/migrate_confluence_map_from_skill_index.py --seed-cursors-only

Run steps 1 wherever both the skill's jsonl files and this repo are checked
out. The 5 curated-root spaces added 2026-09-16 (DevOps001/Openstack101/
CLDENG/DFTRTS/EMCloud) are NOT covered by this migration — they have no
prior crawl to reuse, so their first `map_sync_cli` run bootstraps live
from Confluence directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _sanitize_line_value(s: str) -> str:
    import re

    return re.sub(r"\s+", " ", (s or "")).strip()


def _is_folder_title(title: str) -> bool:
    """Duplicate of app.confluence.map_sync._is_folder_title — keep both in
    sync (see that function's docstring for the rationale). Divergence bug
    found and fixed 2026-09-16: an earlier version of this script used a
    different one-line heuristic that disagreed with map_sync's on divider
    pages like "----...", giving the same page a different 유형 depending on
    which path produced it."""
    t = title.strip()
    if not t:
        return True
    if set(t) <= {"-"}:
        return True
    if t[0].isdigit() and ("." in t.split(" ")[0] or t.split(" ")[0].rstrip(".").isdigit()):
        return True
    return t.upper() in {"FREESPACE", "999. FREESPACE"}


def _build_frontmatter(
    *,
    space_key: str,
    space_name: str,
    root_label: str,
    page_id: str,
    title: str,
    url: str,
    path_breadcrumb: str,
    last_modified: str,
    is_folder: bool,
) -> str:
    """Duplicate of app.confluence.map_sync.build_frontmatter_confluence_map
    — see module docstring for why this isn't imported. Keep field
    names/order identical to that function."""
    lines = [
        "---",
        "구분 : 컨플루언스맵",
        f"공간명 : {space_name}",
        f"space_key : {space_key}",
        f"루트 : {root_label}",
        f"Page ID : {page_id}",
        f"제목 : {_sanitize_line_value(title)}",
        f"URL : {url}",
        f"경로 : {_sanitize_line_value(path_breadcrumb)}",
        f"최종수정일 : {last_modified}",
        f"유형 : {'폴더' if is_folder else '문서'}",
        "---",
    ]
    return "\n".join(lines) + "\n"


SPACE_DISPLAY_NAMES = {
    "LOOKIN": "CI-TEC",
    "TechRepo": "[클라우드] 테크리포(Tech-Repository)",
    "ServiceExcellenceTeam": "서비스일류화팀",
    "ICLOUDUT": "Cloud Umbrella Team",
}

# source_id (app.confluence.map_sync.MAP_SOURCE_DEFS 키) ↔ 이 스크립트가 넣는
# jsonl 스냅샷의 실제 크롤 시점. --seed-cursors-only가 Source.last_sync_at을
# 이 값으로 세팅해, 다음 live sync_map() 실행이 이 시점 이후 변경분만 묻도록
# 한다(전체 재크롤 방지). 시각은 그날 자정(UTC)으로 넉넉히 잡아 스킬 크롤이
# 실제로 언제 끝났는지 몰라도 안전(그날 만들어진 페이지를 놓치지 않도록
# 약간 이르게 잡는 쪽으로).
SEED_SOURCE_TIMESTAMPS = {
    "confluence_map_lookin": datetime(2026, 7, 23, tzinfo=timezone.utc),
    "confluence_map_techrepo": datetime(2026, 7, 23, tzinfo=timezone.utc),
    "confluence_map_serviceexcellenceteam": datetime(2026, 7, 23, tzinfo=timezone.utc),
    "confluence_map_icloudut": datetime(2026, 7, 30, tzinfo=timezone.utc),
}


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _row_to_map_file(row: dict[str, Any], *, raw_dir: Path) -> Path:
    space_key = str(row.get("spaceKey") or "")
    space_name = SPACE_DISPLAY_NAMES.get(space_key, space_key)
    page_id = str(row.get("pageId") or "")
    title = str(row.get("title") or "")
    path_list = row.get("path") or []
    path_breadcrumb = " > ".join([str(p) for p in path_list] + [title])
    url = row.get("url") or ""
    last_modified = str(row.get("lastModified") or "")[:10]
    root_label = str(path_list[0]) if path_list else space_name

    is_folder = _is_folder_title(title)

    front = _build_frontmatter(
        space_key=space_key,
        space_name=space_name,
        root_label=f"[마이그레이션: citec-mcp-workbench 스킬 크롤] {root_label}",
        page_id=page_id,
        title=title,
        url=url,
        path_breadcrumb=path_breadcrumb,
        last_modified=last_modified,
        is_folder=is_folder,
    )

    out_dir = raw_dir / "confluence_map"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"confluence_map_{page_id}.md"
    out_path.write_text(front + "\n" + path_breadcrumb + "\n", encoding="utf-8")
    return out_path


def _write_files(args: argparse.Namespace) -> int:
    skill_refs = Path(args.skill_refs)
    raw_dir = Path(args.raw_dir)

    main_jsonl = skill_refs / "confluence-pages.jsonl"
    scp_jsonl = skill_refs / "confluence-pages-scp.jsonl"
    if not main_jsonl.exists():
        print(f"missing {main_jsonl}", file=sys.stderr)
        return 1
    if not scp_jsonl.exists():
        print(f"missing {scp_jsonl}", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}
    written = 0

    for row in _iter_jsonl(main_jsonl):
        space = row.get("spaceKey")
        counts[space] = counts.get(space, 0) + 1
        if not args.dry_run:
            _row_to_map_file(row, raw_dir=raw_dir)
        written += 1

    for row in _iter_jsonl(scp_jsonl):
        space = row.get("spaceKey") or "ICLOUDUT"
        counts[space] = counts.get(space, 0) + 1
        if not args.dry_run:
            _row_to_map_file(row, raw_dir=raw_dir)
        written += 1

    print(json.dumps(
        {
            "dry_run": args.dry_run,
            "written": written,
            "by_space": counts,
            "note": "every row written unconditionally - overlap with confluence_docs/tech_repo is intentional, see module docstring",
        },
        ensure_ascii=False, indent=2,
    ))
    return 0


def _seed_cursors_only() -> int:
    """Needs the app package (SQLAlchemy) — run inside the citec-kb app
    environment (Docker container or a venv with apps/api/requirements
    installed), not on a bare host. Imported lazily so `_write_files` above
    stays usable without any dependencies installed."""
    from app.confluence.map_sync import seed_cursor  # noqa: E402

    for source_id, seeded_at in SEED_SOURCE_TIMESTAMPS.items():
        seed_cursor(source_id, seeded_at)
        print(f"seeded {source_id} last_sync_at={seeded_at.isoformat()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill-refs",
        default=os.path.expanduser("~/.claude/skills/citec-mcp-workbench/references"),
        help="citec-mcp-workbench skill's references/ dir (has the two jsonl files)",
    )
    parser.add_argument("--raw-dir", default="data/raw", help="citec-kb raw corpus root")
    parser.add_argument("--dry-run", action="store_true", help="print counts only, write nothing")
    parser.add_argument(
        "--seed-cursors-only",
        action="store_true",
        help=(
            "Skip file-writing entirely; only set Source.last_sync_at for the "
            "4 whole-space confluence_map sources so the next live sync_map() "
            "run doesn't re-crawl everything this migration already covered. "
            "Requires the app package (run inside the citec-kb environment)."
        ),
    )
    args = parser.parse_args(argv)

    if args.seed_cursors_only:
        return _seed_cursors_only()
    return _write_files(args)


if __name__ == "__main__":
    sys.exit(main())
