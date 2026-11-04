"""Embed doc_expansion text (summary + keywords) into doc_expansion_embedding.

Ranking signal only. Rows are keyed by document and carry the document hash they
were built from; stale rows are ignored by search. Each DB call opens its own short
session and the model runs between them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import DocExpansion, DocExpansionEmbedding, Document
from app.db.session import session_scope

BATCH = 64


def build_text(summary: str | None, keywords: Sequence[str] | None) -> str:
    """Passage body for the embedding model (the e5 'passage: ' prefix is added by the model wrapper)."""
    kw = ", ".join(keywords or [])
    return " ".join(x for x in ((summary or "").strip(), f"키워드: {kw}" if kw else "") if x)


def _pending(limit: int | None, force: bool) -> list[tuple[str, str | None, str, list[str]]]:
    """(document_id, content_hash, summary, keywords) for active docs needing a (fresh) vector."""
    with session_scope() as session:
        stmt = (
            select(DocExpansion.document_id, Document.content_hash, DocExpansion.summary, DocExpansion.keywords)
            .join(Document, Document.id == DocExpansion.document_id)
            .where(Document.status == "active")
            .where(DocExpansion.source_content_hash == Document.content_hash)
        )
        if not force:
            have = (
                select(DocExpansionEmbedding.document_id)
                .where(DocExpansionEmbedding.document_id == DocExpansion.document_id)
                .where(DocExpansionEmbedding.source_content_hash == Document.content_hash)
                .exists()
            )
            stmt = stmt.where(~have)
        stmt = stmt.order_by(DocExpansion.document_id)
        if limit:
            stmt = stmt.limit(limit)
        return [(r[0], r[1], r[2], list(r[3] or [])) for r in session.execute(stmt).all()]


def _store(rows: list[dict[str, Any]]) -> None:
    with session_scope() as session:
        stmt = pg_insert(DocExpansionEmbedding).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[DocExpansionEmbedding.document_id],
            set_={
                "vector": stmt.excluded.vector,
                "model": stmt.excluded.model,
                "source_content_hash": stmt.excluded.source_content_hash,
            },
        )
        session.execute(stmt)


def embed_all(
    *,
    commit: bool,
    force: bool = False,
    limit: int | None = None,
    embed: Callable[[list[str]], list[list[float]]] | None = None,
    model: str = "intfloat/multilingual-e5-base",
) -> dict[str, int]:
    """Embed pending expansions. Dry-run (commit=False) only counts."""
    todo = _pending(limit, force)
    out = {"pending": len(todo), "written": 0}
    if not commit or not todo:
        return out
    if embed is None:
        from app.embed.model import embed_passages as embed  # lazy: loads the model
    for i in range(0, len(todo), BATCH):
        chunk = todo[i : i + BATCH]
        vecs = embed([build_text(s, k) for _, _, s, k in chunk])
        _store(
            [
                {"document_id": did, "model": model, "vector": v, "source_content_hash": h}
                for (did, h, _, _), v in zip(chunk, vecs)
            ]
        )
        out["written"] += len(chunk)
    return out
