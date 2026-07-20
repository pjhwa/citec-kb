"""Lexicon API — synonym dictionary for search expansion."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.db.models import LexiconTerm
from app.db.session import session_scope
from app.lexicon.seed import load_lexicon_map, seed_lexicon

router = APIRouter(prefix="/v1/lexicon", tags=["lexicon"])


@router.get("/terms")
def list_terms(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    with session_scope() as session:
        rows = list(session.scalars(select(LexiconTerm).order_by(LexiconTerm.priority).limit(limit)).all())
        items = [
            {
                "id": r.id,
                "canonical": r.canonical,
                "variants": list(r.variants or []),
                "priority": r.priority,
            }
            for r in rows
        ]
        total = len(items)
    return {"total": total, "items": items, "llm_used": False}


@router.post("/seed")
def post_seed(replace: bool = True) -> dict[str, Any]:
    return seed_lexicon(replace=replace)


@router.get("/expand")
def expand_preview(q: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Preview synonym expansions for a token/query (debug)."""
    m = load_lexicon_map()
    hits = {}
    for tok in q.replace(",", " ").split():
        if tok.lower() in m:
            hits[tok] = m[tok.lower()]
    return {"q": q, "expansions": hits, "map_size": len(m)}
