"""Human-confirmed decisions API. AI clients may propose + add evidence; verdicts are UI-only."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.decisions import service as svc

router = APIRouter(prefix="/v1", tags=["decisions"])


class OptionIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    summary: str = Field(default="", max_length=500)


class EvidenceIn(BaseModel):
    quote: str = Field(..., min_length=1, max_length=4000)
    stance: str
    option_key: Optional[str] = None
    document_id: Optional[str] = None
    quote_locator: Optional[str] = Field(default=None, max_length=256)
    external_url: Optional[str] = Field(default=None, max_length=2048)
    external_snapshot: Optional[str] = None
    retrieved_at: Optional[datetime] = None


class DecisionCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=300)
    options: list[OptionIn]
    topic: Optional[str] = Field(default=None, max_length=256)
    domain: Optional[str] = Field(default=None, max_length=64)
    stakes: str = "mid"
    ai_recommendation_key: Optional[str] = None
    review_interval_days: Optional[int] = Field(default=180, ge=1, le=3650)
    created_by_kind: str = Field(default="mcp", pattern="^(mcp|server_job|human)$")
    evidence: list[EvidenceIn] = Field(default_factory=list)


class VerdictIn(BaseModel):
    option_key: Optional[str] = None
    free_text: Optional[str] = None
    rationale: Optional[str] = None
    decided_by: Optional[str] = Field(default=None, max_length=128)
    nonce: str
    keep: bool = False
    external_only_confirm: bool = False
    time_to_decide_ms: Optional[int] = Field(default=None, ge=0)


class SupersedeIn(BaseModel):
    new_decision_id: str


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.DecisionNotFound):
        return HTTPException(404, "decision not found")
    if isinstance(exc, svc.DecisionConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(400, str(exc))


_AUTHOR = require_roles("author", "senior", "admin")
_SENIOR = require_roles("senior", "admin")


@router.post("/decisions")
def post_decision(body: DecisionCreate, principal: Principal = Depends(_AUTHOR)) -> dict[str, Any]:
    try:
        return svc.create_decision(
            question=body.question, options=[o.model_dump() for o in body.options], topic=body.topic,
            domain=body.domain, stakes=body.stakes, ai_recommendation_key=body.ai_recommendation_key,
            review_interval_days=body.review_interval_days, created_by_kind=body.created_by_kind,
            created_by=principal.name, evidence=[e.model_dump() for e in body.evidence],
        )
    except (ValueError, svc.DecisionConflict, svc.DecisionNotFound) as exc:
        raise _http(exc) from exc


@router.post("/decisions/{decision_id}/evidence")
def post_evidence(decision_id: str, body: EvidenceIn, principal: Principal = Depends(_AUTHOR)) -> dict[str, Any]:
    try:
        return svc.add_evidence(decision_id, added_by=principal.name, **body.model_dump())
    except (ValueError, svc.DecisionConflict, svc.DecisionNotFound) as exc:
        raise _http(exc) from exc


@router.post("/decisions/{decision_id}/evidence/{evidence_id}/retract")
def post_retract(decision_id: str, evidence_id: str, principal: Principal = Depends(_AUTHOR)) -> dict[str, Any]:
    try:
        return svc.retract_evidence(decision_id, evidence_id)
    except svc.DecisionNotFound as exc:
        raise _http(exc) from exc


@router.get("/decisions")
def get_decisions(
    status: Optional[str] = None, topic: Optional[str] = None, domain: Optional[str] = None,
    sort: str = "priority", include_superseded: bool = False,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        return svc.list_decisions(status=status, topic=topic, domain=domain, sort=sort,
                                  include_superseded=include_superseded, limit=limit, offset=offset)
    except ValueError as exc:
        raise _http(exc) from exc


@router.post("/decisions/_scan-staleness")
def post_scan(principal: Principal = Depends(_SENIOR)) -> dict[str, Any]:
    return svc.scan_staleness()


@router.get("/decisions/metrics")
def get_metrics() -> dict[str, Any]:
    return svc.metrics()


@router.get("/decisions/{decision_id}")
def get_one(decision_id: str) -> dict[str, Any]:
    try:
        return svc.get_decision(decision_id)
    except svc.DecisionNotFound as exc:
        raise _http(exc) from exc


@router.get("/decisions/{decision_id}/nonce")
def get_nonce(decision_id: str, principal: Principal = Depends(_SENIOR)) -> dict[str, Any]:
    try:
        return svc.issue_nonce(decision_id)
    except svc.DecisionNotFound as exc:
        raise _http(exc) from exc


@router.post("/decisions/{decision_id}/verdict")
def post_verdict(decision_id: str, body: VerdictIn, principal: Principal = Depends(_SENIOR)) -> dict[str, Any]:
    try:
        by, via = svc.resolve_actor(principal, body.decided_by)
        return svc.add_verdict(
            decision_id, option_key=body.option_key, decided_by=by, auth_via=via, nonce=body.nonce,
            rationale=body.rationale, free_text=body.free_text, keep=body.keep,
            external_only_confirm=body.external_only_confirm, time_to_decide_ms=body.time_to_decide_ms,
        )
    except (ValueError, svc.DecisionConflict, svc.DecisionNotFound, PermissionError) as exc:
        raise _http(exc) from exc


@router.post("/decisions/{decision_id}/supersede")
def post_supersede(decision_id: str, body: SupersedeIn, principal: Principal = Depends(_SENIOR)) -> dict[str, Any]:
    try:
        return svc.supersede(decision_id, new_decision_id=body.new_decision_id)
    except (ValueError, svc.DecisionConflict, svc.DecisionNotFound) as exc:
        raise _http(exc) from exc


@router.post("/decisions/{decision_id}/retire")
def post_retire(decision_id: str, principal: Principal = Depends(_SENIOR)) -> dict[str, Any]:
    try:
        return svc.retire(decision_id)
    except (svc.DecisionConflict, svc.DecisionNotFound) as exc:
        raise _http(exc) from exc


@router.post("/decisions/{decision_id}/restore")
def post_restore(decision_id: str, principal: Principal = Depends(_SENIOR)) -> dict[str, Any]:
    try:
        return svc.restore(decision_id)
    except (svc.DecisionConflict, svc.DecisionNotFound) as exc:
        raise _http(exc) from exc
