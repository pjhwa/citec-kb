"""Ticket list + query route/planner (Phase 3)."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.query.planner import plan_query, route_query
from app.query.time_range import parse_relative_range
from app.tickets.query import get_ticket_by_external_id, list_tickets

router = APIRouter(prefix="/v1", tags=["tickets"])


@router.get("/tickets/{external_id}")
def get_ticket_detail(
    external_id: str,
    source_type: str = Query("support_history"),
) -> dict[str, Any]:
    """Full ticket body for drill-down from type breakdown UI."""
    row = get_ticket_by_external_id(external_id, source_type=source_type)
    if not row:
        raise HTTPException(status_code=404, detail="ticket not found")
    return row


@router.get("/tickets")
def get_tickets(
    source_type: str = Query("support_history"),
    date_field: str = Query("Created", description="Created | Resolved | Updated"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    relative: Optional[str] = Query(
        None, description="지난 주 / 이번 달 / 최근 7일 등 (date_from/to 대체)"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order: str = Query("desc"),
) -> dict[str, Any]:
    df, dt = date_from, date_to
    range_label = None
    if relative:
        dr = parse_relative_range(relative)
        if not dr:
            raise HTTPException(status_code=400, detail=f"unrecognized relative range: {relative}")
        df, dt = dr.date_from, dr.date_to
        range_label = dr.label
    result = list_tickets(
        source_type=source_type,
        date_field=date_field,
        date_from=df,
        date_to=dt,
        limit=limit,
        offset=offset,
        order=order,
    )
    if range_label:
        result["range_label"] = range_label
    return result


@router.post("/query/route")
def post_query_route(body: dict[str, Any]) -> dict[str, Any]:
    """Intent router + optional execution (default execute=true).

    Priority: capacity → analytics → time_scoped_list → checklist →
    similar_incident → entity_aggregate → hybrid_search.
    """
    q = str((body or {}).get("q") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="q required")
    execute = (body or {}).get("execute", True)
    if execute is False or str(execute).lower() in {"0", "false", "no"}:
        plan = plan_query(q)
        return {"intent": plan.get("intent"), "params": plan, "executed": False}
    out = route_query(q, body=body or {}, execute=True)
    if out.get("intent") == "error":
        raise HTTPException(status_code=400, detail=out.get("error") or "bad query")
    return out


@router.post("/query")
def post_query(body: dict[str, Any]) -> dict[str, Any]:
    """Full planner facade (alias of /query/route with execution)."""
    return post_query_route(body)
