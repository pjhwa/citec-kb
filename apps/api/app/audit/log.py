"""Query / answer audit logging (Postgres queries + answers tables)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select

from app.db.models import Answer, QueryLog
from app.db.session import session_scope

logger = logging.getLogger("citec.audit")


def log_query_answer(
    *,
    query: str,
    mode: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
    user_id: Optional[str] = None,
    answer_md: str = "",
    citations: Optional[list[Any]] = None,
    model: Optional[str] = None,
    token_usage: Optional[dict[str, Any]] = None,
    trust: Optional[dict[str, Any]] = None,
    groundedness_score: Optional[float] = None,
) -> dict[str, Any]:
    """Persist one query + optional answer row. Failures are logged, not raised."""
    try:
        with session_scope() as session:
            qrow = QueryLog(
                query=query,
                filters=dict(filters or {}),
                mode=mode,
                latency_ms=latency_ms,
                user_id=user_id,
            )
            session.add(qrow)
            session.flush()
            aid = None
            if answer_md is not None:
                arow = Answer(
                    query_id=qrow.id,
                    answer_md=answer_md or "",
                    citations=list(citations or []),
                    model=model,
                    token_usage=dict(token_usage or {}),
                    trust=dict(trust or {}),
                    groundedness_score=groundedness_score,
                )
                session.add(arow)
                session.flush()
                aid = arow.id
            return {"query_id": qrow.id, "answer_id": aid}
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log failed: %s", exc)
        return {"query_id": None, "answer_id": None, "error": str(exc)}


def list_recent_queries(*, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(QueryLog).order_by(QueryLog.created_at.desc()).limit(limit)
            ).all()
        )
        items = []
        for r in rows:
            ans = session.scalar(
                select(Answer).where(Answer.query_id == r.id).limit(1)
            )
            items.append(
                {
                    "query_id": r.id,
                    "query": r.query,
                    "mode": r.mode,
                    "filters": r.filters,
                    "latency_ms": r.latency_ms,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "answer_id": ans.id if ans else None,
                    "abstained": (ans.trust or {}).get("abstain") if ans else None,
                    "answer_preview": (ans.answer_md or "")[:200] if ans else None,
                }
            )
        return {"total": len(items), "items": items, "llm_used": False}
