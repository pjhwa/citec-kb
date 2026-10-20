"""Confluence 문서 최근 변경/추가 조회 — MCP kb_recent_changes의 백엔드."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.documents.recent_changes import list_recent_changes
from app.query.time_range import parse_relative_range

router = APIRouter(prefix="/v1", tags=["documents"])

_DEFAULT_RELATIVE = "최근 7일"


@router.get("/documents/recent-changes")
def get_recent_changes(
    relative: Optional[str] = Query(None, description="최근 7일 / 지난 주 / 이번 달 등 (date_from/to 대체)"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    change_type: str = Query("modified", description="modified | added | all"),
    source_types: Optional[str] = Query(
        None, description="콤마 구분: confluence_map,confluence_docs,tech_repo (기본 3종 전부)"
    ),
    space_key: Optional[str] = Query(None),
    title_contains: Optional[str] = Query(None),
    include_folders: bool = Query(False),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    df, dt = date_from, date_to
    label = None
    if relative and relative.strip():
        dr = parse_relative_range(relative)
        if not dr:
            raise HTTPException(status_code=400, detail=f"unrecognized relative range: {relative}")
        df, dt, label = dr.date_from, dr.date_to, dr.label
    elif df is None and dt is None:
        dr = parse_relative_range(_DEFAULT_RELATIVE)
        assert dr is not None
        df, dt, label = dr.date_from, dr.date_to, dr.label
    else:
        # 한쪽만 주면 나머지를 같은 날로(단일 날짜 조회) 둔다.
        df = df or dt
        dt = dt or df
    types = [t.strip() for t in source_types.split(",") if t.strip()] if source_types else None
    try:
        result = list_recent_changes(
            date_from=df,
            date_to=dt,
            change_type=change_type,
            source_types=types,
            space_key=space_key,
            title_contains=title_contains,
            include_folders=include_folders,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if label:
        result["range"]["label"] = label
    return result
