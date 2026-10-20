"""Confluence 문서(confluence_map/confluence_docs/tech_repo)의 최근 변경·추가 조회.

- modified: metadata.source_modified_at (Confluence version.when, ISO8601+오프셋)이 범위 내
- added:    documents.created_at (KB가 처음 본 시각 — Confluence 생성일이 아님)
읽기 전용. 설계: docs/superpowers/specs/2026-10-20-kb-recent-changes-design.md
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence

from sqlalchemy import text

from app.db.session import session_scope
from app.query.time_range import KST

CONFLUENCE_SOURCE_TYPES = ("confluence_map", "confluence_docs", "tech_repo")
CHANGE_TYPES = ("modified", "added", "all")

# source_modified_at → timestamptz. 형식이 맞지 않으면 NULL(쿼리 전체가 깨지지 않게).
# 오프셋(Z 또는 ±hh[:]mm)이 있는 ISO8601만 인정하고, 월/일/시/분/초 범위를 정규식으로 제한한다.
_TS_RE = (
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T([01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})$"
)
_MOD_AT = (
    "CASE WHEN d.metadata->>'source_modified_at' ~ :ts_re "
    "THEN (d.metadata->>'source_modified_at')::timestamptz END"
)
_SPACE = "coalesce(d.metadata->>'space_key', d.metadata->>'공간명')"
_PATH = (
    "CASE d.source_type WHEN 'confluence_map' THEN d.metadata->>'경로' "
    "WHEN 'confluence_docs' THEN d.metadata->>'폴더분류' "
    "ELSE d.metadata->>'디렉토리' END"
)


def list_recent_changes(
    *,
    date_from: date,
    date_to: date,
    change_type: str = "modified",
    source_types: Optional[Sequence[str]] = None,
    space_key: Optional[str] = None,
    title_contains: Optional[str] = None,
    include_folders: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    if change_type not in CHANGE_TYPES:
        raise ValueError(f"change_type must be one of {CHANGE_TYPES}")
    types = list(source_types) if source_types else list(CONFLUENCE_SOURCE_TYPES)
    bad = [t for t in types if t not in CONFLUENCE_SOURCE_TYPES]
    if bad:
        raise ValueError(f"source_types must be within {CONFLUENCE_SOURCE_TYPES}, got {bad}")
    if date_from > date_to:
        raise ValueError("date_from must be <= date_to")
    limit = min(max(int(limit), 1), 200)
    offset = max(int(offset), 0)

    start = datetime(date_from.year, date_from.month, date_from.day, tzinfo=KST)
    end_d = date_to + timedelta(days=1)
    end = datetime(end_d.year, end_d.month, end_d.day, tzinfo=KST)

    where = ["d.status = 'active'", "d.source_type = ANY(:types)"]
    params: dict[str, Any] = {"types": types, "ts_re": _TS_RE, "start": start, "end": end}
    if not include_folders:
        # 유형 키가 없는 문서(이관분 등)는 NULL 3값 논리로 조용히 빠지면 안 되므로 coalesce.
        where.append(
            "NOT (d.source_type = 'confluence_map' AND coalesce(d.metadata->>'유형', '') = '폴더')"
        )
    if space_key and space_key.strip():
        where.append(f"lower({_SPACE}) = lower(:space_key)")
        params["space_key"] = space_key.strip()
    if title_contains and title_contains.strip():
        # LIKE 와일드카드(% _)를 리터럴로 다루기 위해 strpos 사용
        where.append("strpos(lower(d.title), lower(:title_q)) > 0")
        params["title_q"] = title_contains.strip()
    base_where = " AND ".join(where)

    is_mod = "(m.mod_at >= :start AND m.mod_at < :end)"
    is_add = "(m.created_at >= :start AND m.created_at < :end)"
    cond = {"modified": is_mod, "added": is_add, "all": f"({is_mod} OR {is_add})"}[change_type]
    order = "m.created_at DESC" if change_type == "added" else "coalesce(m.mod_at, m.created_at) DESC"

    sql = text(
        f"""
        WITH m AS (
          SELECT d.id, d.title, d.source_type, d.external_id, d.source_uri,
                 d.created_at, d.updated_at,
                 {_SPACE} AS space_key, {_PATH} AS path,
                 d.metadata->>'source_version' AS source_version,
                 d.metadata->>'source_modified_at' AS source_modified_at,
                 {_MOD_AT} AS mod_at
          FROM documents d
          WHERE {base_where}
        )
        SELECT m.*, {is_mod} AS is_mod, {is_add} AS is_add, count(*) OVER () AS total
        FROM m
        WHERE {cond}
        ORDER BY {order}, m.id
        LIMIT :limit OFFSET :offset
        """
    )
    missing_sql = text(
        f"""
        SELECT count(*) FROM (
          SELECT {_MOD_AT} AS mod_at FROM documents d WHERE {base_where}
        ) x WHERE x.mod_at IS NULL
        """
    )
    count_only_sql = text(
        f"""
        WITH m AS (
          SELECT d.created_at, {_MOD_AT} AS mod_at FROM documents d WHERE {base_where}
        ) SELECT count(*) FROM m WHERE {cond}
        """
    )

    with session_scope() as session:
        rows = session.execute(sql, {**params, "limit": limit, "offset": offset}).mappings().all()
        missing = session.execute(missing_sql, params).scalar() or 0
        # offset이 범위를 넘어 빈 페이지가 되어도 total은 정확해야 한다.
        total = int(rows[0]["total"]) if rows else int(session.execute(count_only_sql, params).scalar() or 0)

    items = []
    for r in rows:
        change = "both" if (r["is_mod"] and r["is_add"]) else ("modified" if r["is_mod"] else "added")
        sv = r["source_version"]
        items.append(
            {
                "title": r["title"],
                "source_type": r["source_type"],
                "space_key": r["space_key"],
                "path": r["path"],
                "page_id": r["external_id"],
                "url": r["source_uri"],
                "source_version": int(sv) if sv is not None and str(sv).isdigit() else sv,
                "source_modified_at": r["source_modified_at"],
                "kb_created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "kb_updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                "change": change,
            }
        )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "change_type": change_type,
        "range": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "missing_modified_at": int(missing),
        "items": items,
    }
