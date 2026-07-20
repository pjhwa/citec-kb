from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.audit.log import log_query_answer
from app.db.session import session_scope
from app.retrieval.multi_query import multi_hybrid_search
from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

router = APIRouter(prefix="/v1", tags=["search"])


class SearchFiltersIn(BaseModel):
    source_type: Optional[str] = None
    domain: Optional[str] = None
    environment: Optional[str] = None
    work_type: Optional[str] = None
    path_l2: Optional[str] = None
    status: str = "active"


class SearchBody(BaseModel):
    q: str = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=50)
    filters: SearchFiltersIn = Field(default_factory=SearchFiltersIn)
    multi_query: bool = Field(
        default=True,
        description="Expand query (CITECTS/PISA/FAQ/entity/synonyms) and merge hybrid hits",
    )
    audit: bool = Field(default=False, description="If true, write queries row (no answer)")


@router.post("/search")
def search(body: SearchBody) -> dict[str, Any]:
    t0 = time.time()
    embed_fn = None
    qvec = None
    embed_error: str | None = None
    try:
        from app.embed.model import embed_query

        embed_fn = embed_query
        qvec = embed_query(body.q)
    except Exception as exc:  # noqa: BLE001
        # Degrade to FTS-only when model/deps unavailable (keep search usable).
        embed_error = f"{type(exc).__name__}: {exc}"

    req = SearchRequest(
        q=body.q,
        top_k=body.top_k,
        filters=SearchFilters(**body.filters.model_dump()),
    )
    try:
        with session_scope() as session:
            if body.multi_query:
                resp, mq_meta = multi_hybrid_search(
                    session,
                    req,
                    query_vector=qvec,
                    embed_fn=embed_fn,
                    multi_query=True,
                )
            else:
                resp = hybrid_search(session, req, query_vector=qvec)
                mq_meta = {"multi_query": False, "queries": [body.q], "n_queries": 1}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    vector_used = bool(qvec is not None) or bool(mq_meta.get("vector_used"))
    out: dict[str, Any] = {
        "query": resp.query,
        "exact_tokens": resp.exact_tokens,
        "total": resp.total,
        "gated": resp.gated,
        "trust_retrieval": resp.trust_retrieval,
        "vector_used": vector_used,
        "multi_query": mq_meta.get("multi_query"),
        "expanded_queries": mq_meta.get("queries"),
        "results": [
            {
                "rank": r.rank,
                "score": r.score,
                "document_id": r.document_id,
                "chunk_id": r.chunk_id,
                "title": r.title,
                "snippet": r.snippet,
                "source_type": r.source_type,
                "external_id": r.external_id,
                "evidence_grade": r.evidence_grade,
                "domain": r.domain,
                "environment": r.environment,
                "work_type": r.work_type,
                "path_l2": r.path_l2,
                "source_uri": r.source_uri,
                "fts_rank": r.fts_rank,
                "vec_rank": r.vec_rank,
            }
            for r in resp.results
        ],
    }
    if embed_error and qvec is None:
        out["embed_degraded"] = True
        out["embed_error"] = embed_error[:240]
    if body.audit:
        out["audit"] = log_query_answer(
            query=body.q,
            mode="search",
            filters=body.filters.model_dump(),
            latency_ms=int((time.time() - t0) * 1000),
            answer_md="",
            citations=[{"external_id": r["external_id"]} for r in out["results"][:10]],
            trust={"retrieval": resp.trust_retrieval},
        )
    return out
