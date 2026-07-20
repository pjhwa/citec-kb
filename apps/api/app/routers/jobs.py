"""Background job enqueue / status API."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.jobs.queue import ALLOWED_TYPES, enqueue_job, get_job, list_jobs, worker_status

router = APIRouter(prefix="/v1", tags=["jobs"])


class JobBody(BaseModel):
    type: str = Field(..., description=f"one of {sorted(ALLOWED_TYPES)}")
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0


@router.post("/jobs")
def post_job(body: JobBody) -> dict[str, Any]:
    try:
        return enqueue_job(body.type, payload=body.payload, priority=body.priority)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/jobs")
def get_jobs(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    try:
        return list_jobs(limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def get_job_route(job_id: str) -> dict[str, Any]:
    try:
        job = get_job(job_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/worker/status")
def get_worker_status() -> dict[str, Any]:
    try:
        return worker_status()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
