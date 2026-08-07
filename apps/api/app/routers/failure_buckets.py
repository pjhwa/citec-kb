"""Failure-bucket registry API (register/refine/match, self-improving)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.failure_buckets.service import (
    create_bucket,
    get_bucket,
    list_buckets,
    match_buckets,
    refine_bucket,
)

router = APIRouter(prefix="/v1", tags=["failure-buckets"])


class FailureBucketCreate(BaseModel):
    bucket_name: str = Field(..., min_length=1, max_length=256)
    fb_domain: str = Field(..., min_length=1, max_length=32)
    symptom: str = Field(default="", max_length=4000)
    discriminating_signals: list[str] = Field(default_factory=list)
    counter_signals: list[str] = Field(default_factory=list)
    root_cause: str = Field(default="", max_length=4000)
    recommended_action: str = Field(default="", max_length=4000)
    evidence_ref: str = Field(..., min_length=1)
    protocol: Optional[str] = Field(default=None, max_length=32)
    environment: Optional[str] = Field(default=None, max_length=16)
    created_by: Optional[str] = None


class FailureBucketRefine(BaseModel):
    add_signal: Optional[str] = None
    add_counter_signal: Optional[str] = None
    environment: Optional[str] = Field(default=None, max_length=16)
    confirm: bool = True


class FailureBucketMatch(BaseModel):
    observed_signals: list[str] = Field(default_factory=list)
    symptom: str = Field(default="", max_length=4000)
    fb_domain: Optional[str] = Field(default=None, max_length=32)
    protocol: Optional[str] = Field(default=None, max_length=32)
    environment: Optional[str] = Field(default=None, max_length=16)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/failure-buckets")
def post_failure_bucket(
    body: FailureBucketCreate,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    if not body.fb_domain.strip():
        raise HTTPException(status_code=400, detail="fb_domain required")
    if not body.evidence_ref.strip():
        raise HTTPException(status_code=400, detail="evidence_ref required")
    return create_bucket(
        bucket_name=body.bucket_name,
        fb_domain=body.fb_domain,
        symptom=body.symptom,
        discriminating_signals=body.discriminating_signals,
        counter_signals=body.counter_signals,
        root_cause=body.root_cause,
        recommended_action=body.recommended_action,
        evidence_ref=body.evidence_ref,
        protocol=body.protocol,
        environment=body.environment,
        created_by=body.created_by or principal.name,
    )


@router.get("/failure-buckets")
def get_failure_buckets(
    fb_domain: Optional[str] = Query(None),
    protocol: Optional[str] = Query(None),
    environment: Optional[str] = Query(None),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return list_buckets(
        fb_domain=fb_domain,
        protocol=protocol,
        environment=environment,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )


@router.get("/failure-buckets/{bucket_id}")
def get_one_failure_bucket(bucket_id: str) -> dict[str, Any]:
    row = get_bucket(bucket_id)
    if not row:
        raise HTTPException(status_code=404, detail="failure_bucket not found")
    return row


@router.post("/failure-buckets/{bucket_id}/refine")
def post_refine_failure_bucket(
    bucket_id: str,
    body: FailureBucketRefine,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    _ = principal
    try:
        return refine_bucket(
            bucket_id,
            add_signal=body.add_signal,
            add_counter_signal=body.add_counter_signal,
            environment=body.environment,
            confirm=body.confirm,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="failure_bucket not found") from None


@router.post("/failure-buckets/match")
def post_match_failure_buckets(body: FailureBucketMatch) -> dict[str, Any]:
    results = match_buckets(
        observed_signals=body.observed_signals,
        symptom=body.symptom,
        fb_domain=body.fb_domain,
        protocol=body.protocol,
        environment=body.environment,
        top_k=body.top_k,
    )
    return {"results": results, "total": len(results)}
