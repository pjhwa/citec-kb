"""Lightweight feedback API (moved out of the retired insights router)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.db.models import Feedback
from app.db.session import session_scope

router = APIRouter(prefix="/v1", tags=["feedback"])

FEEDBACK_TARGETS = {"answer", "search", "decision"}


class FeedbackBody(BaseModel):
    target_type: str = Field(..., description="answer | search | decision")
    target_id: str
    rating: int = Field(..., description="-1 or 1")
    comment: Optional[str] = None
    user_id: Optional[str] = None


@router.post("/feedback")
def post_feedback(
    body: FeedbackBody,
    principal: Principal = Depends(require_roles("viewer", "author", "senior", "admin")),
) -> dict[str, Any]:
    if body.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating must be -1 or 1")
    if body.target_type not in FEEDBACK_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid target_type {body.target_type!r} — allowed: {sorted(FEEDBACK_TARGETS)}",
        )
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
