"""Similar incident API."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.si.retrieve import similar_incidents

router = APIRouter(prefix="/v1", tags=["similar-incident"])


class SimilarIncidentBody(BaseModel):
    symptom: str = Field(..., min_length=2, max_length=2000)
    environment: Optional[str] = None
    product: Optional[str] = None
    service: Optional[str] = None
    top_k: int = Field(default=3, ge=1, le=10)
    source_types: Optional[list[str]] = Field(
        default=None,
        description="subset of support_history|incident_reports; default searches both",
    )


@router.post("/similar-incident")
def similar_incident(body: SimilarIncidentBody) -> dict[str, Any]:
    try:
        return similar_incidents(
            body.symptom,
            top_k=body.top_k,
            environment=body.environment,
            product=body.product,
            service=body.service,
            source_types=body.source_types,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
