from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.settings import get_settings

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


class IngestRequest(BaseModel):
    sources: Optional[list[str]] = Field(
        default=None,
        description="support_history, tech_repo, confluence_docs, tuning_ai, checkitem",
    )
    limit: Optional[int] = Field(default=None, ge=1, description="Max docs (debug)")
    async_mode: bool = Field(default=False, description="Run in background thread")


class IngestResponse(BaseModel):
    status: str
    stats: dict[str, Any] | None = None
    message: str | None = None


def _run(raw_dir: str, sources: list[str] | None, limit: int | None) -> dict[str, Any]:
    from app.ingest.pipeline import run_ingest

    return run_ingest(raw_dir, sources=sources, limit=limit)


@router.post("/run", response_model=IngestResponse)
def ingest_run(body: IngestRequest, background: BackgroundTasks) -> IngestResponse:
    settings = get_settings()
    raw = settings.raw_dir
    if body.async_mode:
        background.add_task(_run, raw, body.sources, body.limit)
        return IngestResponse(
            status="accepted",
            message="Ingest started in background; poll /v1/ingest/jobs later (PR-03 minimal)",
        )
    try:
        stats = _run(raw, body.sources, body.limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return IngestResponse(status="ok", stats=stats)


@router.get("/jobs")
def list_jobs(limit: int = 20) -> dict[str, Any]:
    from sqlalchemy import select

    from app.db.models import IngestJob
    from app.db.session import session_scope

    with session_scope() as session:
        rows = session.scalars(
            select(IngestJob).order_by(IngestJob.created_at.desc()).limit(limit)
        ).all()
        return {
            "jobs": [
                {
                    "id": j.id,
                    "mode": j.mode,
                    "status": j.status,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                    "stats": j.stats,
                    "error": j.error,
                }
                for j in rows
            ]
        }


@router.get("/stats")
def corpus_stats() -> dict[str, Any]:
    from sqlalchemy import func, select

    from app.db.models import Checkitem, Chunk, Document
    from app.db.session import session_scope

    with session_scope() as session:
        by_type = dict(
            session.execute(
                select(Document.source_type, func.count())
                .where(Document.status == "active")
                .group_by(Document.source_type)
            ).all()
        )
        return {
            "documents": session.scalar(select(func.count()).select_from(Document)) or 0,
            "chunks_active": session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.is_active.is_(True))
            )
            or 0,
            "checkitems": session.scalar(select(func.count()).select_from(Checkitem)) or 0,
            "by_source_type": by_type,
        }
