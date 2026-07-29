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
    symptom: str = Field(default="", max_length=4000)
    discriminating_signals: list[str] = Field(default_factory=list)
    counter_signals: list[str] = Field(default_factory=list)
    root_cause: str = Field(default="", max_length=4000)
    recommended_action: str = Field(default="", max_length=4000)
    protocol: Optional[str] = Field(default=None, max_length=32)
    created_by: Optional[str] = None


class FailureBucketRefine(BaseModel):
    add_signal: Optional[str] = None
    add_counter_signal: Optional[str] = None
    confirm: bool = True


class FailureBucketMatch(BaseModel):
    observed_signals: list[str] = Field(default_factory=list)
    symptom: str = Field(default="", max_length=4000)
    protocol: Optional[str] = Field(default=None, max_length=32)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/failure-buckets")
def post_failure_bucket(
    body: FailureBucketCreate,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    return create_bucket(
        bucket_name=body.bucket_name,
        symptom=body.symptom,
        discriminating_signals=body.discriminating_signals,
        counter_signals=body.counter_signals,
        root_cause=body.root_cause,
        recommended_action=body.recommended_action,
        protocol=body.protocol,
        created_by=body.created_by or principal.name,
    )


@router.get("/failure-buckets")
def get_failure_buckets(
    protocol: Optional[str] = Query(None),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return list_buckets(protocol=protocol, min_confidence=min_confidence, limit=limit, offset=offset)


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
            confirm=body.confirm,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="failure_bucket not found") from None


@router.post("/failure-buckets/match")
def post_match_failure_buckets(body: FailureBucketMatch) -> dict[str, Any]:
    results = match_buckets(
        observed_signals=body.observed_signals,
        symptom=body.symptom,
        protocol=body.protocol,
        top_k=body.top_k,
    )
    return {"results": results, "total": len(results)}
