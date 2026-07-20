"""Entity seed + list API (PR-15)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.db.models import DocumentEntity, Entity
from app.db.session import session_scope
from app.entities.seed import seed_all

router = APIRouter(prefix="/v1/entities", tags=["entities"])


@router.get("/")
def list_entities(
    limit: int = Query(50, ge=1, le=200),
    with_counts: bool = Query(True),
) -> dict[str, Any]:
    with session_scope() as session:
        ents = list(session.scalars(select(Entity).order_by(Entity.id).limit(limit)).all())
        items = []
        for e in ents:
            row: dict[str, Any] = {
                "id": e.id,
                "canonical_name": e.canonical_name,
                "type": e.type,
                "customer": e.customer,
                "aliases": list(e.aliases or []),
            }
            if with_counts:
                n = session.scalar(
                    select(func.count())
                    .select_from(DocumentEntity)
                    .where(DocumentEntity.entity_id == e.id)
                )
                row["document_count"] = int(n or 0)
            items.append(row)
        total = session.scalar(select(func.count()).select_from(Entity)) or 0
    return {"total": int(total), "items": items, "llm_used": False}


@router.post("/seed")
def post_seed_entities(
    replace: bool = False,
    link: bool = True,
) -> dict[str, Any]:
    """Upsert core entities and optionally link support_history docs by title alias."""
    return seed_all(replace=replace, link=link)


@router.get("/{entity_id}/documents")
def list_entity_documents(
    entity_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    from app.db.models import Document

    with session_scope() as session:
        total = (
            session.scalar(
                select(func.count())
                .select_from(DocumentEntity)
                .where(DocumentEntity.entity_id == entity_id)
            )
            or 0
        )
        doc_ids = list(
            session.scalars(
                select(DocumentEntity.document_id)
                .where(DocumentEntity.entity_id == entity_id)
                .offset(offset)
                .limit(limit)
            ).all()
        )
        docs = []
        if doc_ids:
            for d in session.scalars(select(Document).where(Document.id.in_(doc_ids))).all():
                docs.append(
                    {
                        "document_id": d.id,
                        "external_id": d.external_id,
                        "title": d.title,
                        "source_type": d.source_type,
                    }
                )
    return {
        "entity_id": entity_id,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": docs,
    }
