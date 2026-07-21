"""Analytics API — metadata aggregates only (no LLM counts)."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.analytics.aggregate import aggregate_tickets, entity_share
from app.analytics.title_tokens import title_token_stats
from app.query.time_range import parse_relative_range

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get("/tickets")
def analytics_tickets(
    group_by: str = Query(
        "year",
        description="year|month|component|issue_type|status|assignee|total",
    ),
    source_type: str = Query("support_history"),
    date_field: str = Query("Created"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    relative: Optional[str] = Query(None, description="지난 주 / 이번 달 등"),
    component: Optional[str] = Query(None),
    entity: Optional[str] = Query(None, description="title ILIKE filter"),
    top_k: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    df, dt = date_from, date_to
    range_label = None
    if relative:
        dr = parse_relative_range(relative)
        if not dr:
            raise HTTPException(status_code=400, detail=f"unrecognized relative range: {relative}")
        df, dt = dr.date_from, dr.date_to
        range_label = dr.label
    result = aggregate_tickets(
        source_type=source_type,
        group_by=group_by,
        date_field=date_field,
        date_from=df,
        date_to=dt,
        component=component,
        entity=entity,
        top_k=top_k,
    )
    if range_label:
        result["range_label"] = range_label
    return result


@router.get("/entity_share")
def analytics_entity_share(
    entity: str = Query(..., min_length=1),
    source_type: str = Query("support_history"),
    date_field: str = Query("Created"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    relative: Optional[str] = Query(None),
) -> dict[str, Any]:
    df, dt = date_from, date_to
    range_label = None
    if relative:
        dr = parse_relative_range(relative)
        if not dr:
            raise HTTPException(status_code=400, detail=f"unrecognized relative range: {relative}")
        df, dt = dr.date_from, dr.date_to
        range_label = dr.label
    result = entity_share(
        entity=entity,
        source_type=source_type,
        date_field=date_field,
        date_from=df,
        date_to=dt,
    )
    if range_label:
        result["range_label"] = range_label
    return result


@router.get("/title_tokens")
def analytics_title_tokens(
    source_type: str = Query("support_history"),
    component: Optional[str] = Query(None, description="e.g. 장애지원"),
    top_k: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Title token frequency (Component filter optional). No LLM."""
    return title_token_stats(
        source_type=source_type,
        component=component,
        top_k=top_k,
    )
