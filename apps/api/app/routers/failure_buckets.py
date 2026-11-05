"""Failure-bucket registry API (register/refine/match, self-improving)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.failure_buckets import registrants_store
from app.failure_buckets.service import (
    BucketEditError,
    BucketRetiredError,
    create_bucket,
    get_bucket,
    list_buckets,
    match_buckets,
    normalize_match_filters,
    refine_bucket,
    restore_bucket,
    retire_batch,
    retire_bucket,
    update_bucket,
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


class FailureBucketEdit(BaseModel):
    """Partial edit: only the fields that are sent are changed."""

    bucket_name: Optional[str] = Field(default=None, max_length=256)
    fb_domain: Optional[str] = Field(default=None, max_length=32)
    protocol: Optional[str] = Field(default=None, max_length=32)
    environment: Optional[str] = Field(default=None, max_length=16)
    evidence_ref: Optional[str] = Field(default=None, max_length=2000)
    symptom: Optional[str] = Field(default=None, max_length=4000)
    root_cause: Optional[str] = Field(default=None, max_length=4000)
    recommended_action: Optional[str] = Field(default=None, max_length=4000)
    discriminating_signals: Optional[list[str]] = Field(default=None, max_length=100)
    counter_signals: Optional[list[str]] = Field(default=None, max_length=100)


class FailureBucketRetire(BaseModel):
    reason: str = Field(..., min_length=8, max_length=2000)


class FailureBucketRetireBatch(BaseModel):
    created_by_prefix: str = Field(..., min_length=3, max_length=128)
    reason: str = Field(..., min_length=8, max_length=2000)
    dry_run: bool = True


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
    result = create_bucket(
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
    # Auth on + API-key caller: the token name is the authenticated identity. A body
    # created_by that differs is allowed (not rejected) but flagged.
    if principal.auth_via == "apikey" and body.created_by and body.created_by != principal.name:
        note = (
            f"created_by='{body.created_by}'가 인증된 토큰 이름('{principal.name}')과 다릅니다. "
            "토큰 이름과 같은 값을 쓰세요."
        )
        result["created_by_warning"] = f"{result['created_by_warning']} / {note}" if result.get("created_by_warning") else note
    return result


@router.get("/failure-buckets")
def get_failure_buckets(
    fb_domain: Optional[str] = Query(None),
    protocol: Optional[str] = Query(None),
    environment: Optional[str] = Query(None),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_retired: bool = Query(False),
    status: Optional[str] = Query(None, pattern="^(active|retired|test)$"),
) -> dict[str, Any]:
    return list_buckets(
        include_retired=include_retired,
        status=status,
        fb_domain=fb_domain,
        protocol=protocol,
        environment=environment,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )


class SystemRegistrantBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


def _registrant_counts_24h() -> dict[str, int]:
    """Registrations in the last 24h grouped by system name (the part before '@')."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.db.models import FailureBucket
    from app.db.session import session_scope

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    out: dict[str, int] = {}
    with session_scope() as session:
        rows = session.execute(
            select(FailureBucket.created_by, func.count())
            .where(FailureBucket.created_at >= since, FailureBucket.created_by.like("%@%"))
            .group_by(FailureBucket.created_by)
        ).all()
    for created_by, n in rows:
        name = str(created_by).split("@", 1)[0]
        out[name] = out.get(name, 0) + int(n)
    return out


def _registrants_view(snap: dict[str, Any]) -> dict[str, Any]:
    try:
        counts: dict[str, int] | None = _registrant_counts_24h()
    except Exception:  # noqa: BLE001 — the list must stay readable when the DB is down
        counts = None
    snap["counts_24h"] = counts
    return snap


@router.get("/failure-buckets/system-registrants")
def list_system_registrants() -> dict[str, Any]:
    """Allowlist of system names accepted as `<system>@<version>` created_by (read-only for all)."""
    return _registrants_view(registrants_store.snapshot())


@router.post("/failure-buckets/system-registrants")
def add_system_registrant(
    body: SystemRegistrantBody, principal: Principal = Depends(require_roles("admin"))
) -> dict[str, Any]:
    try:
        snap = registrants_store.add(body.name, principal.name)
    except registrants_store.RegistrantNameError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _registrants_view(snap)


@router.delete("/failure-buckets/system-registrants/{name}")
def remove_system_registrant(
    name: str, principal: Principal = Depends(require_roles("admin"))
) -> dict[str, Any]:
    snap = registrants_store.remove(name, principal.name)
    if not snap["changed"]:
        raise HTTPException(status_code=404, detail=f"허용 목록에 '{name}'이(가) 없습니다.")
    return _registrants_view(snap)


@router.get("/failure-buckets/{bucket_id}")
def get_one_failure_bucket(bucket_id: str) -> dict[str, Any]:
    row = get_bucket(bucket_id)
    if not row:
        raise HTTPException(status_code=404, detail="failure_bucket not found")
    return row


@router.patch("/failure-buckets/{bucket_id}")
def patch_failure_bucket(
    bucket_id: str,
    body: FailureBucketEdit,
    principal: Principal = Depends(require_roles("senior", "admin")),
) -> dict[str, Any]:
    """Curate a bucket's descriptive fields (senior/admin). Re-indexes the search mirror."""
    _ = principal
    try:
        return update_bucket(bucket_id, body.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="failure_bucket not found") from None
    except BucketRetiredError:
        raise HTTPException(status_code=409, detail="retired: restore 후 수정") from None
    except BucketEditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    except BucketRetiredError:
        raise HTTPException(status_code=409, detail="retired: restore 후 사용") from None


# Declared before /{bucket_id}/... routes only matters for the literal path; POST
# /failure-buckets/retire-batch has no conflicting {bucket_id} POST pattern, but keep
# it above for readability.
@router.post("/failure-buckets/retire-batch")
def post_retire_batch(
    body: FailureBucketRetireBatch,
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    try:
        return retire_batch(
            created_by_prefix=body.created_by_prefix,
            reason=body.reason,
            retired_by=principal.name,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/failure-buckets/{bucket_id}/retire")
def post_retire_failure_bucket(
    bucket_id: str,
    body: FailureBucketRetire,
    principal: Principal = Depends(require_roles("senior", "admin")),
) -> dict[str, Any]:
    try:
        return retire_bucket(bucket_id, reason=body.reason, retired_by=principal.name)
    except KeyError:
        raise HTTPException(status_code=404, detail="failure_bucket not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/failure-buckets/{bucket_id}/restore")
def post_restore_failure_bucket(
    bucket_id: str,
    principal: Principal = Depends(require_roles("senior", "admin")),
) -> dict[str, Any]:
    _ = principal
    try:
        return restore_bucket(bucket_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="failure_bucket not found") from None


@router.post("/failure-buckets/match")
def post_match_failure_buckets(body: FailureBucketMatch) -> dict[str, Any]:
    try:
        fb_domain, environment = normalize_match_filters(body.fb_domain, body.environment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    results = match_buckets(
        observed_signals=body.observed_signals,
        symptom=body.symptom,
        fb_domain=fb_domain,
        protocol=body.protocol,
        environment=environment,
        top_k=body.top_k,
    )
    from app.failure_buckets.match import LABEL_RULES

    return {"results": results, "total": len(results), "label_rules": LABEL_RULES}
