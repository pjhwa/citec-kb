"""Chat / Fast+Deep RAG API (JSON or SSE stream)."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.audit.log import list_recent_queries, log_query_answer
from app.rag.pipeline import run_fast_rag, stream_rag
from app.retrieval.search import SearchFilters

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatFiltersIn(BaseModel):
    source_type: Optional[str] = None
    domain: Optional[str] = None
    environment: Optional[str] = None
    work_type: Optional[str] = None
    path_l2: Optional[str] = None
    status: str = "active"


class ChatBody(BaseModel):
    q: str = Field(..., min_length=1, max_length=4000)
    mode: str = Field(default="fast", description="fast | deep")
    top_k: int = Field(default=8, ge=1, le=30)
    filters: ChatFiltersIn = Field(default_factory=ChatFiltersIn)
    max_context_tokens: Optional[int] = Field(default=None, ge=1000, le=80_000)
    stream: bool = Field(default=False, description="If true, return text/event-stream SSE")
    multi_query: bool = Field(
        default=True,
        description="Use multi-query hybrid retrieval (same as /v1/search default)",
    )
    audit: bool = Field(default=True, description="Persist query/answer audit rows")


@router.post("/chat")
def chat(body: ChatBody) -> Any:
    """RAG chat: retrieve → generate with [C#] citations → Trust banner.

    Set ``stream=true`` for SSE events: meta / token / done / error.
    """
    if body.mode not in {"fast", "deep"}:
        raise HTTPException(status_code=400, detail="mode must be fast or deep")

    filters = SearchFilters(**body.filters.model_dump())
    # deep default larger top_k unless caller set high
    top_k = body.top_k
    if body.mode == "deep" and body.top_k == 8:
        top_k = 16

    if body.stream:
        def event_gen():
            t0 = time.time()
            final: dict[str, Any] = {}
            try:
                for ev in stream_rag(
                    body.q,
                    mode=body.mode,
                    top_k=top_k,
                    filters=filters,
                    max_context_tokens=body.max_context_tokens,
                    multi_query=body.multi_query,
                ):
                    if ev.get("type") == "done":
                        final = ev.get("result") or {}
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if body.audit and final:
                    log_query_answer(
                        query=body.q,
                        mode=body.mode,
                        filters=body.filters.model_dump(),
                        latency_ms=int((time.time() - t0) * 1000),
                        answer_md=str(final.get("answer") or ""),
                        citations=final.get("citations") or [],
                        trust=final.get("trust") or {},
                    )
            except Exception as exc:  # noqa: BLE001
                err = {"type": "error", "error": str(exc)}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    t0 = time.time()
    try:
        result = run_fast_rag(
            body.q,
            mode=body.mode,
            top_k=top_k,
            filters=filters,
            max_context_tokens=body.max_context_tokens or (
                40_000 if body.mode == "deep" else 12_000
            ),
            max_answer_tokens=2500 if body.mode == "deep" else 1200,
            multi_query=body.multi_query,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    latency = int((time.time() - t0) * 1000)
    if body.audit:
        ids = log_query_answer(
            query=body.q,
            mode=body.mode,
            filters=body.filters.model_dump(),
            latency_ms=latency,
            answer_md=str(result.get("answer") or ""),
            citations=result.get("citations") or [],
            trust=result.get("trust") or {},
        )
        result["audit"] = ids
    result["latency_ms"] = latency
    return result


@router.get("/queries/recent")
def recent_queries(limit: int = 50) -> dict[str, Any]:
    """List recent audited queries (query + answer preview)."""
    return list_recent_queries(limit=limit)
