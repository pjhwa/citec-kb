"""Synonym candidate review API (search-only; see docs/superpowers/specs/2026-11-02-…)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.synonyms import runner, runtime, store

router = APIRouter(prefix="/v1/lexicon/synonym-candidates", tags=["lexicon"])

_WRITE_ROLES = ("senior", "admin")


class DecisionBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


class ImportItem(BaseModel):
    terms: list[Annotated[str, StringConstraints(max_length=100)]] = Field(max_length=20)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence")
    @classmethod
    def _evidence_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, ensure_ascii=False)) > 8192:
            raise ValueError("evidence too large (max 8192 chars as JSON)")
        return v


class ImportBody(BaseModel):
    run_id: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=128)
    items: list[ImportItem] = Field(max_length=2000)


class MineStart(BaseModel):
    since_years: float = Field(default=2.0, ge=0.1, le=10)
    limit_tickets: Optional[int] = Field(default=None, ge=1, le=100000)
    batch_size: int = Field(default=30, ge=10, le=500)
    max_batches: Optional[int] = Field(default=None, ge=1, le=10000)
    commit: bool = False  # True: accepted candidates go straight in as 'proposed'
    split_on_empty: bool = False  # extra (billable) calls to isolate filter-blocked tickets
    min_terms_per_ticket: int = Field(default=1, ge=1, le=2)  # 1: terms may appear in separate tickets
    stop_after_empty: int = Field(default=3, ge=0, le=50)  # stop after N consecutive blocked batches (0 = never)


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except runner.RunError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


# Mining spends LLM calls and runs a long subprocess, so it is admin-only (not senior).
@router.get("/mine-runs")
def list_mine_runs(principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    return {"runs": runner.list_runs()}


@router.post("/mine-runs")
def start_mine_run(body: MineStart, principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    return _run(runner.start, started_by=principal.name, **body.model_dump())


@router.get("/mine-runs/{run_id}")
def get_mine_run(run_id: str, principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    return _run(runner.describe, run_id, detail=True)


@router.post("/mine-runs/{run_id}/cancel")
def cancel_mine_run(run_id: str, principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    return _run(runner.cancel, run_id)


@router.post("/mine-runs/{run_id}/import")
def import_mine_run(run_id: str, principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    return _run(runner.import_result, run_id)


@router.get("")
def list_candidates(
    status: Optional[str] = Query(None, pattern="^(proposed|approved|rejected|disabled)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    run_id: Optional[str] = Query(None, max_length=64),
) -> dict[str, Any]:
    return store.list_candidates(status=status, limit=limit, offset=offset, run_id=run_id)


@router.post("/import")
def import_candidates(
    body: ImportBody,
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, int]:
    return store.upsert_candidates(
        [i.model_dump() for i in body.items], run_id=body.run_id, model=body.model
    )


def _decide(cid: int, action: str, body: DecisionBody, principal: Principal) -> dict[str, Any]:
    try:
        row = store.set_status(cid, action, decided_by=principal.name, note=body.note)
    except store.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except store.TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    runtime.reset_cache()  # this instance sees the change at once; others after the TTL
    return row


@router.post("/{cid}/approve")
def approve(
    cid: int,
    body: DecisionBody = Body(default_factory=DecisionBody),
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    return _decide(cid, "approve", body, principal)


@router.post("/{cid}/reject")
def reject(
    cid: int,
    body: DecisionBody = Body(default_factory=DecisionBody),
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    return _decide(cid, "reject", body, principal)


@router.post("/{cid}/disable")
def disable(
    cid: int,
    body: DecisionBody = Body(default_factory=DecisionBody),
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    return _decide(cid, "disable", body, principal)
