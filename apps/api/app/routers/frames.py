"""Issue frame admin / stats API."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db.models import Document, IssueFrame
from app.db.session import session_scope
from app.frames.job import extract_frames

router = APIRouter(prefix="/v1", tags=["frames"])


class FrameExtractBody(BaseModel):
    source_type: str = "support_history"
    limit: Optional[int] = Field(default=None, ge=1, le=5000)
    force: bool = False
    min_quality: float = Field(default=0.0, ge=0.0, le=1.0)


@router.post("/frames/extract")
def run_extract(body: FrameExtractBody) -> dict[str, Any]:
    """Batch extract issue frames (rules). May take a while for full corpus."""
    try:
        return extract_frames(
            source_type=body.source_type,
            limit=body.limit,
            force=body.force,
            min_quality=body.min_quality,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/frames/stats")
def frame_stats() -> dict[str, Any]:
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(IssueFrame)) or 0
        avg_q = session.scalar(select(func.avg(IssueFrame.quality))) or 0.0
        both = session.scalar(
            select(func.count())
            .select_from(IssueFrame)
            .where(IssueFrame.root_cause.is_not(None))
            .where(IssueFrame.resolution.is_not(None))
        ) or 0
        q_ge = session.scalar(
            select(func.count()).select_from(IssueFrame).where(IssueFrame.quality >= 0.5)
        ) or 0
    return {
        "frames_total": int(total),
        "avg_quality": round(float(avg_q), 3),
        "with_cause_and_resolution": int(both),
        "quality_ge_0_5": int(q_ge),
    }


@router.get("/frames/{external_id}")
def get_frame(external_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.execute(
            select(IssueFrame, Document)
            .join(Document, Document.id == IssueFrame.document_id)
            .where(Document.external_id == external_id)
            .limit(1)
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="frame not found")
        fr, doc = row
        return {
            "external_id": doc.external_id,
            "title": doc.title,
            "document_id": doc.id,
            "symptom": fr.symptom,
            "root_cause": fr.root_cause,
            "resolution": fr.resolution,
            "workaround": fr.workaround,
            "components": fr.components,
            "environment": fr.environment,
            "commands": fr.commands,
            "quality": fr.quality,
            "raw_extract": fr.raw_extract,
        }
