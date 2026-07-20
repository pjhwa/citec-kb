"""War-room knowledge bundles (seed packs + write API)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.bundles.match import load_bundles, match_bundles
from app.bundles.store import delete_bundle, get_bundle, save_bundle

router = APIRouter(prefix="/v1", tags=["bundles"])


class BundleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    title: Optional[str] = None
    id: Optional[str] = None
    symptom_hints: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    related_components: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


@router.get("/bundles")
def list_bundles(
    q: Optional[str] = Query(None, description="If set, rank bundles by symptom match"),
    top_k: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    if q and q.strip():
        matched = match_bundles(q.strip(), top_k=top_k)
        return {"total": len(matched), "query": q, "items": matched}
    items = load_bundles()
    return {"total": len(items), "items": items}


@router.get("/bundles/{name}")
def get_bundle_route(name: str) -> dict[str, Any]:
    b = get_bundle(name)
    if not b:
        raise HTTPException(status_code=404, detail="bundle not found")
    return b


@router.post("/bundles")
def create_bundle(body: BundleBody) -> dict[str, Any]:
    """Create or overwrite a bundle JSON seed file."""
    try:
        saved = save_bundle(body.model_dump())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"write failed: {exc}") from exc
    return {"ok": True, "action": "upsert", **saved}


@router.put("/bundles/{name}")
def update_bundle(name: str, body: BundleBody) -> dict[str, Any]:
    data = body.model_dump()
    data["name"] = name
    try:
        saved = save_bundle(data, name=name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"write failed: {exc}") from exc
    return {"ok": True, "action": "update", **saved}


@router.delete("/bundles/{name}")
def remove_bundle(name: str) -> dict[str, Any]:
    if not get_bundle(name):
        raise HTTPException(status_code=404, detail="bundle not found")
    ok = delete_bundle(name)
    if not ok:
        raise HTTPException(status_code=500, detail="delete failed (read-only fs?)")
    return {"ok": True, "deleted": name}
