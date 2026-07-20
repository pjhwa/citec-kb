"""Capacity rules + estimate API (Phase 3). Numbers from rules only."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.capacity.calculator import estimate_capacity, list_capacity_rules
from app.capacity.db_seed import seed_capacity_db
from app.capacity.seed import clear_basis_seed_cache

router = APIRouter(prefix="/v1/capacity", tags=["capacity"])


class EstimateBody(BaseModel):
    period_days: int = Field(7, ge=1, le=365)
    basis: str = "1안"
    fields: Optional[list[str]] = None
    include_pricing: bool = True


@router.get("/rules")
def get_rules(basis: str = "1안") -> dict[str, Any]:
    return list_capacity_rules(basis=basis)


@router.post("/estimate")
def post_estimate(body: EstimateBody) -> dict[str, Any]:
    result = estimate_capacity(
        period_days=body.period_days,
        basis=body.basis,
        fields=body.fields,
        include_pricing=body.include_pricing,
    )
    if body.fields and result.get("unknown_fields") and not result.get("fields"):
        raise HTTPException(
            status_code=400,
            detail=f"unknown fields: {result['unknown_fields']}",
        )
    return result


@router.post("/seed")
def post_seed_capacity(replace: bool = True) -> dict[str, Any]:
    """Sync capacity_rules + pricing_rules from JSON seed into DB."""
    return seed_capacity_db(replace=replace)
