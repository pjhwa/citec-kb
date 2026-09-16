"""CI-TEC recurring-incident dashboard API — read-only query surface over
issue_frames.citec_domains/severity_tier (see app.citec_dashboard.service
and docs/CITEC_DASHBOARD_API.md for the full field/response reference).

All endpoints are unauthenticated reads, matching GET /v1/failure-buckets'
convention in this codebase (write endpoints require roles; there are no
writes here).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.citec_dashboard.service import (
    domain_catalog,
    failure_bucket_coverage,
    query_recurring_patterns,
)

router = APIRouter(prefix="/v1/citec-dashboard", tags=["citec-dashboard"])


def _csv(value: Optional[str]) -> Optional[list[str]]:
    if not value or not value.strip():
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


@router.get("/domains")
def get_domain_catalog() -> dict[str, Any]:
    """Static legend: the 11 CI-TEC domains, severity-tier meanings, and
    the valid group_by dimensions for /recurring-patterns. Cheap, no DB
    query — fetch once and cache client-side."""
    return domain_catalog()


@router.get("/recurring-patterns")
def get_recurring_patterns(
    group_by: str = Query(..., description="Comma list, 1-3 of: domain,severity_tier,dept,customer,year"),
    domains: Optional[str] = Query(None, description="Comma list to filter to (e.g. Network,Storage)"),
    severity_tiers: Optional[str] = Query(
        None, description="Comma list to filter to (major,minor,failover_no_impact,customer_fault,vendor_fault,unknown)"
    ),
    dept_contains: Optional[str] = Query(None, description="Substring match on 운영부서"),
    customer_contains: Optional[str] = Query(None, description="Substring match on 고객사"),
    since_days: Optional[int] = Query(None, ge=1, description="Only incidents occurred within the last N days"),
    min_count: int = Query(3, ge=1, description="Drop groups smaller than this"),
    limit: int = Query(50, ge=1, le=500),
    samples_per_group: int = Query(3, ge=0, le=20),
) -> dict[str, Any]:
    try:
        return query_recurring_patterns(
            group_by=_csv(group_by) or [],
            domains=_csv(domains),
            severity_tiers=_csv(severity_tiers),
            dept_contains=dept_contains,
            customer_contains=customer_contains,
            since_days=since_days,
            min_count=min_count,
            limit=limit,
            samples_per_group=samples_per_group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/failure-bucket-coverage")
def get_failure_bucket_coverage(
    since_days: Optional[int] = Query(730, ge=1),
    min_count: int = Query(3, ge=1),
) -> dict[str, Any]:
    return failure_bucket_coverage(since_days=since_days, min_count=min_count)
