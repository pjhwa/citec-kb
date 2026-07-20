"""Exhaustive scan intent: high-recall multi-query list with completeness meta."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.db.session import session_scope
from app.retrieval.multi_query import multi_hybrid_search
from app.retrieval.search import SearchFilters, SearchRequest

_EXHAUSTIVE = re.compile(
    r"전부|전량|모두\s*모아|모아\s*줘|지원\s*건\s*전부|관련\s*지원\s*건|"
    r"이력을\s*모두|전체\s*사례|공통\s*원인|패턴\s*은",
    re.I,
)


def detect_exhaustive_intent(text: str) -> Optional[dict[str, Any]]:
    t = (text or "").strip()
    if not t or not _EXHAUSTIVE.search(t):
        return None
    # leave pure SI / prevention to their detectors (called earlier or later carefully)
    if re.search(r"예방|유사\s*장애\s*검색", t):
        return None
    return {
        "intent": "exhaustive",
        "q": t,
        "top_k": 20,
        "endpoint": "POST /v1/search multi_query exhaustive",
    }


def run_exhaustive(
    *,
    q: str,
    top_k: int = 20,
    source_type: Optional[str] = None,
) -> dict[str, Any]:
    try:
        from app.embed.model import embed_query

        embed_fn = embed_query
        qvec = embed_query(q)
    except Exception:  # noqa: BLE001
        embed_fn = None
        qvec = None

    filters = SearchFilters(status="active")
    if source_type:
        filters.source_type = source_type
    req = SearchRequest(q=q, top_k=min(max(top_k, 10), 40), filters=filters)
    with session_scope() as session:
        resp, meta = multi_hybrid_search(
            session, req, query_vector=qvec, embed_fn=embed_fn, multi_query=True
        )

    items = [
        {
            "rank": r.rank,
            "external_id": r.external_id,
            "title": r.title,
            "score": r.score,
            "source_type": r.source_type,
            "snippet": (r.snippet or "")[:240],
        }
        for r in resp.results
    ]
    # completeness: heuristic — multi-query + top_k large; not a full corpus scan
    completeness = {
        "mode": "high_recall_sample",
        "claimed_complete": False,
        "note": (
            "전량 코퍼스 스캔이 아니라 multi-query hybrid top_k 병합입니다. "
            "누락 가능 — exhaustive 메타로 표시."
        ),
        "n_queries": meta.get("n_queries"),
        "expanded_queries": meta.get("queries"),
        "returned": len(items),
        "top_k": req.top_k,
    }
    return {
        "total": len(items),
        "items": items,
        "completeness": completeness,
        "vector_used": meta.get("vector_used"),
        "multi_query": meta.get("multi_query"),
        "method": "exhaustive_multi_hybrid",
        "llm_used": False,
    }
