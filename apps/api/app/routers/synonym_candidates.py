"""Synonym candidate review API (search-only; see docs/superpowers/specs/2026-11-02-…)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.synonyms import runtime, store

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


@router.get("")
def list_candidates(
    status: Optional[str] = Query(None, pattern="^(proposed|approved|rejected|disabled)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return store.list_candidates(status=status, limit=limit, offset=offset)


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
