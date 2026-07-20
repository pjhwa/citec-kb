"""Insight flywheel: draft → review → approved/rejected (+ optional promote)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import Document, Insight
from app.db.session import session_scope

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


def transition_insight(
    insight_id: str,
    *,
    to_status: str,
    reviewer: Optional[str] = None,
    promote: bool = False,
) -> dict[str, Any]:
    if to_status not in VALID_STATUS:
        raise ValueError(f"invalid status: {to_status}")
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
            if promote and not row.promoted_document_id:
                payload = f"{row.title}\n{row.body_md}"
                content_hash = hashlib.sha256(
                    payload.encode("utf-8", errors="ignore")
                ).hexdigest()
                doc = Document(
                    id=str(uuid4()),
                    source_type="insight",
                    external_id=f"INSIGHT-{row.id[:8]}",
                    title=row.title,
                    body_md=row.body_md,
                    status="active",
                    content_hash=content_hash,
                    evidence_grade="draft",
                    metadata_={
                        "insight_id": row.id,
                        "author": row.author,
                        "reviewer": row.reviewer,
                        "promoted": True,
                    },
                )
                session.add(doc)
                session.flush()
                row.promoted_document_id = doc.id
        session.flush()
        return _to_dict(row)
