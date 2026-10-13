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
def post_seed(replace: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """G06 — 기본값은 비파괴 upsert(기존 variants와 union 병합, 삭제 없음).
    seed 파일에 없는 기존 항목까지 지우고 다시 심으려면 명시적으로
    ?replace=true를 전달해야 한다. ?dry_run=true면 쓰지 않고 추가/변경/삭제
    예상 canonical만 돌려준다(R03.4)."""
    return seed_lexicon(replace=replace, dry_run=dry_run)


@router.get("/expand")
def expand_preview(q: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Preview synonym expansions for a token/query (debug)."""
    m = load_lexicon_map()
    hits = {}
    for tok in q.replace(",", " ").split():
        if tok.lower() in m:
            hits[tok] = m[tok.lower()]
    return {"q": q, "expansions": hits, "map_size": len(m)}
