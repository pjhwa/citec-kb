"""Insight flywheel API + lightweight feedback."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.db.models import Feedback
from app.db.session import session_scope
from app.insights.service import (
    create_insight,
    get_insight,
    list_insights,
    reindex_insight,
    transition_insight,
    update_insight,
)

router = APIRouter(prefix="/v1", tags=["insights"])


class InsightCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=1024)
    body_md: str = ""
    source_doc_ids: list[str] = Field(default_factory=list)
    author: Optional[str] = None


class InsightUpdate(BaseModel):
    title: Optional[str] = None
    body_md: Optional[str] = None
    source_doc_ids: Optional[list[str]] = None


class TransitionBody(BaseModel):
    reviewer: Optional[str] = None
    promote: bool = False


class FeedbackBody(BaseModel):
    target_type: str = Field(..., description="answer | insight | search")
    target_id: str
    rating: int = Field(..., description="-1 or 1")
    comment: Optional[str] = None
    user_id: Optional[str] = None


@router.post("/insights")
def post_insight(
    body: InsightCreate,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    return create_insight(
        title=body.title,
        body_md=body.body_md,
        source_doc_ids=body.source_doc_ids,
        author=body.author or principal.name,
    )


@router.get("/insights")
def get_insights(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        return list_insights(status=status, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/insights/{insight_id}")
def get_one(insight_id: str) -> dict[str, Any]:
    row = get_insight(insight_id)
    if not row:
        raise HTTPException(status_code=404, detail="insight not found")
    return row


@router.patch("/insights/{insight_id}")
def patch_insight(
    insight_id: str,
    body: InsightUpdate,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    _ = principal
    try:
        return update_insight(
            insight_id,
            title=body.title,
            body_md=body.body_md,
            source_doc_ids=body.source_doc_ids,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="insight not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/insights/{insight_id}/submit")
def submit_insight(
    insight_id: str,
    body: TransitionBody | None = None,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    _ = principal
    try:
        return transition_insight(
            insight_id,
            to_status="review",
            reviewer=(body.reviewer if body else None),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="insight not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/insights/{insight_id}/approve")
def approve_insight_route(
    insight_id: str,
    body: TransitionBody | None = None,
    principal: Principal = Depends(require_roles("senior", "admin")),
) -> dict[str, Any]:
    body = body or TransitionBody()
    try:
        return transition_insight(
            insight_id,
            to_status="approved",
            reviewer=body.reviewer or principal.name or "senior",
            promote=body.promote,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="insight not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/insights/{insight_id}/reject")
def reject_insight(
    insight_id: str,
    body: TransitionBody | None = None,
    principal: Principal = Depends(require_roles("senior", "admin")),
) -> dict[str, Any]:
    try:
        return transition_insight(
            insight_id,
            to_status="rejected",
            reviewer=(body.reviewer if body else None) or principal.name or "senior",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="insight not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/insights/{insight_id}/reopen")
def reopen_insight(
    insight_id: str,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    """rejected | review → draft so author can edit and re-submit."""
    _ = principal
    try:
        return transition_insight(insight_id, to_status="draft")
    except KeyError:
        raise HTTPException(status_code=404, detail="insight not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/insights/{insight_id}/reindex")
def reindex_insight_route(
    insight_id: str,
    principal: Principal = Depends(require_roles("senior", "admin")),
) -> dict[str, Any]:
    """Re-chunk + embed a promoted/approved insight into the search index."""
    _ = principal
    try:
        return reindex_insight(insight_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="insight not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/feedback")
def post_feedback(
    body: FeedbackBody,
    principal: Principal = Depends(require_roles("viewer", "author", "senior", "admin")),
) -> dict[str, Any]:
    if body.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating must be -1 or 1")
    if body.target_type not in {"answer", "insight", "search"}:
        raise HTTPException(status_code=400, detail="invalid target_type")
    with session_scope() as session:
        row = Feedback(
            target_type=body.target_type,
            target_id=body.target_id,
            rating=body.rating,
            comment=body.comment,
            user_id=body.user_id or principal.sub,
        )
        session.add(row)
        session.flush()
        return {
            "id": row.id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "rating": row.rating,
            "comment": row.comment,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
