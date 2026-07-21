"""Insight flywheel: draft → review → approved/rejected (+ optional promote/index)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.db.models import Chunk, Document, Embedding, Insight
from app.db.session import session_scope
from app.ingest.adapters import DocumentDraft

logger = logging.getLogger("citec.insights")

VALID_STATUS = frozenset({"draft", "review", "approved", "rejected"})
TRANSITIONS = {
    "draft": {"review", "rejected"},
    "review": {"approved", "rejected", "draft"},
    "approved": set(),  # terminal unless re-open later
    "rejected": {"draft"},
}


def _to_dict(i: Insight) -> dict[str, Any]:
    return {
        "id": i.id,
        "title": i.title,
        "body_md": i.body_md,
        "source_doc_ids": list(i.source_doc_ids or []),
        "status": i.status,
        "author": i.author,
        "reviewer": i.reviewer,
        "approved_at": i.approved_at.isoformat() if i.approved_at else None,
        "promoted_document_id": i.promoted_document_id,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


def create_insight(
    *,
    title: str,
    body_md: str = "",
    source_doc_ids: Optional[list[str]] = None,
    author: Optional[str] = None,
) -> dict[str, Any]:
    with session_scope() as session:
        row = Insight(
            title=title.strip(),
            body_md=body_md or "",
            source_doc_ids=list(source_doc_ids or []),
            status="draft",
            author=author,
        )
        session.add(row)
        session.flush()
        return _to_dict(row)


def get_insight(insight_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as session:
        row = session.get(Insight, insight_id)
        return _to_dict(row) if row else None


def list_insights(
    *,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with session_scope() as session:
        stmt = select(Insight).order_by(Insight.created_at.desc())
        if status:
            if status not in VALID_STATUS:
                raise ValueError(f"invalid status: {status}")
            stmt = stmt.where(Insight.status == status)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(session.scalars(stmt.offset(offset).limit(limit)).all())
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [_to_dict(r) for r in rows],
        }


def update_insight(
    insight_id: str,
    *,
    title: Optional[str] = None,
    body_md: Optional[str] = None,
    source_doc_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(Insight, insight_id)
        if not row:
            raise KeyError(insight_id)
        if row.status not in {"draft", "rejected"}:
            raise PermissionError(f"cannot edit insight in status={row.status}")
        if title is not None:
            row.title = title.strip()
        if body_md is not None:
            row.body_md = body_md
        if source_doc_ids is not None:
            row.source_doc_ids = list(source_doc_ids)
        session.flush()
        return _to_dict(row)


def _insight_draft(row: Insight) -> DocumentDraft:
    body = (row.body_md or "").strip() or row.title
    return DocumentDraft(
        source_type="insight",
        external_id=f"INSIGHT-{row.id[:8]}",
        title=row.title,
        body_md=body,
        metadata={
            "insight_id": row.id,
            "author": row.author,
            "reviewer": row.reviewer,
            "promoted": True,
        },
        evidence_grade="draft",
        source_uri=f"insight://{row.id}",
    ).finalize()


def _index_stats(document_id: str) -> dict[str, Any]:
    with session_scope() as session:
        n_chunks = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.document_id == document_id, Chunk.is_active.is_(True))
        ) or 0
        n_emb = session.scalar(
            select(func.count())
            .select_from(Embedding)
            .join(Chunk, Chunk.id == Embedding.chunk_id)
            .where(Chunk.document_id == document_id, Chunk.is_active.is_(True))
        ) or 0
        doc = session.get(Document, document_id)
        return {
            "document_id": document_id,
            "chunks": int(n_chunks),
            "embeddings": int(n_emb),
            "source_type": doc.source_type if doc else None,
            "external_id": doc.external_id if doc else None,
        }


def promote_and_index(
    insight_id: str,
    *,
    reindex: bool = False,
    embed: bool = True,
) -> dict[str, Any]:
    """Upsert promoted document, chunk+FTS, optionally embed pending chunks.

    When ``embed`` is False, only document/FTS is written (worker can embed later).
    """
    from app.embed.job import embed_pending_chunks
    from app.ingest.pipeline import upsert_document_from_draft

    with session_scope() as session:
        row = session.get(Insight, insight_id)
        if not row:
            raise KeyError(insight_id)
        if row.status != "approved" and not reindex:
            if not (reindex and row.promoted_document_id):
                raise PermissionError(
                    f"promote requires approved status (got {row.status})"
                )
        draft = _insight_draft(row)

    upsert = upsert_document_from_draft(draft)
    doc_id = upsert["document_id"]

    with session_scope() as session:
        row = session.get(Insight, insight_id)
        if not row:
            raise KeyError(insight_id)
        row.promoted_document_id = doc_id
        session.flush()
        insight_snapshot = _to_dict(row)

    emb: dict[str, Any]
    if embed:
        try:
            emb = embed_pending_chunks(document_id=doc_id, batch_size=16)
        except Exception as exc:  # noqa: BLE001
            logger.exception("embed after promote failed doc=%s", doc_id)
            emb = {"error": str(exc), "embedded": 0, "document_id": doc_id}
    else:
        emb = {
            "embedded": 0,
            "deferred": True,
            "document_id": doc_id,
            "model": None,
            "errors": 0,
        }

    stats = _index_stats(doc_id)
    insight_snapshot["index"] = {
        **stats,
        "upsert_action": upsert.get("action"),
        "embedded": emb.get("embedded", 0),
        "embed_errors": emb.get("errors", 0),
        "model": emb.get("model"),
        "embed_error": emb.get("error"),
        "deferred": bool(emb.get("deferred")),
    }
    return insight_snapshot


def transition_insight(
    insight_id: str,
    *,
    to_status: str,
    reviewer: Optional[str] = None,
    promote: bool = False,
    async_index: bool = False,
) -> dict[str, Any]:
    if to_status not in VALID_STATUS:
        raise ValueError(f"invalid status: {to_status}")

    do_promote = False
    with session_scope() as session:
        row = session.get(Insight, insight_id)
        if not row:
            raise KeyError(insight_id)
        allowed = TRANSITIONS.get(row.status, set())
        if to_status not in allowed:
            raise PermissionError(f"cannot transition {row.status} → {to_status}")
        row.status = to_status
        if reviewer:
            row.reviewer = reviewer
        if to_status == "approved":
            row.approved_at = datetime.now(timezone.utc)
            if promote:
                do_promote = True
        session.flush()
        result = _to_dict(row)

    if do_promote:
        try:
            if async_index:
                promoted = promote_and_index(insight_id, embed=False)
                from app.jobs.queue import enqueue_job

                job = enqueue_job(
                    "insight_reindex",
                    payload={"insight_id": insight_id},
                )
                promoted["index_job"] = {
                    "id": job.get("id"),
                    "type": job.get("type"),
                    "status": job.get("status"),
                }
                if promoted.get("index"):
                    promoted["index"]["async"] = True
                    promoted["index"]["job_id"] = job.get("id")
                result = promoted
            else:
                result = promote_and_index(insight_id, embed=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("promote/index failed insight=%s", insight_id)
            result["index"] = {"error": str(exc)}
    return result


def reindex_insight(insight_id: str, *, embed: bool = True) -> dict[str, Any]:
    """Re-run chunk+embed for an already approved (or previously promoted) insight."""
    with session_scope() as session:
        row = session.get(Insight, insight_id)
        if not row:
            raise KeyError(insight_id)
        if row.status != "approved" and not row.promoted_document_id:
            raise PermissionError("reindex requires approved insight or existing promote")
    return promote_and_index(insight_id, reindex=True, embed=embed)
