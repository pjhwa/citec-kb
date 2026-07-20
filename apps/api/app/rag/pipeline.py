"""RAG pipeline: hybrid retrieve → pack → generate → trust (fast/deep + stream)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any, Optional

from sqlalchemy import select

from app.db.models import Chunk, Document
from app.db.session import session_scope
from app.embed.model import embed_query
from app.llm_chat import LLMChatError, chat_complete, chat_complete_stream
from app.rag.packer import PackedChunk, format_context_block, pack_chunks
from app.retrieval.multi_query import multi_hybrid_search
from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search
from app.trust.engine import assess_trust, trust_to_dict

logger = logging.getLogger("citec.rag")

_SYSTEM = """당신은 CI-TEC 부서 지식 어시스턴트입니다.
규칙:
1. 아래 제공된 출처 블록에 있는 내용만 사용해 답하세요. 추측·일반론 금지.
2. 사실 문장 끝에는 반드시 출처 번호 [C1], [C2] 형식으로 인용하세요.
3. 출처에 답이 없으면 한 문장으로 기권하세요: "제공된 근거로는 확답할 수 없습니다."
4. 한국어로 간결하게 답하세요. 조치 권고 시 환경을 확인하라고 안내하세요.
5. 단일 신뢰도 % 숫자는 출력하지 마세요.
"""

_MODE_DEFAULTS = {
    "fast": {"top_k": 8, "max_context_tokens": 12_000, "max_answer_tokens": 1200},
    "deep": {"top_k": 16, "max_context_tokens": 40_000, "max_answer_tokens": 2500},
}


def _extract_citation_ids(text: str) -> list[str]:
    found = re.findall(r"\[(C\d+)\]", text or "", flags=re.I)
    out: list[str] = []
    for f in found:
        cid = f.upper() if f.upper().startswith("C") else f
        if cid not in out:
            out.append(cid)
    return out


def _looks_like_abstain(answer: str) -> bool:
    a = (answer or "").strip()
    if not a:
        return True
    markers = (
        "제공된 근거로는 확답할 수 없습니다",
        "근거가 부족",
        "확답할 수 없",
        "기권",
    )
    return any(m in a for m in markers)


def _snippet_fallback(packed: list[PackedChunk], header: str) -> str:
    return header + "\n" + "\n".join(
        f"- [{p.cite_id}] {p.title}: {p.snippet[:160]}" for p in packed[:5]
    )


def _prepare(
    q: str,
    *,
    mode: str,
    top_k: Optional[int],
    filters: Optional[SearchFilters],
    max_context_tokens: Optional[int],
    max_answer_tokens: Optional[int],
    multi_query: bool = True,
) -> dict[str, Any]:
    """Shared retrieve + pack. Returns either abstain payload or generation inputs."""
    defaults = _MODE_DEFAULTS.get(mode, _MODE_DEFAULTS["fast"])
    top_k = top_k or int(defaults["top_k"])
    max_context_tokens = max_context_tokens or int(defaults["max_context_tokens"])
    max_answer_tokens = max_answer_tokens or int(defaults["max_answer_tokens"])
    filters = filters or SearchFilters()

    qvec = None
    embed_fn = None
    try:
        embed_fn = embed_query
        qvec = embed_query(q)
    except Exception:  # noqa: BLE001
        qvec = None

    mq_meta: dict[str, Any] = {"multi_query": False, "queries": [q], "n_queries": 1}
    with session_scope() as session:
        req = SearchRequest(q=q, top_k=top_k, filters=filters)
        if multi_query:
            resp, mq_meta = multi_hybrid_search(
                session,
                req,
                query_vector=qvec,
                embed_fn=embed_fn,
                multi_query=True,
            )
        else:
            resp = hybrid_search(session, req, query_vector=qvec)
        # Enrich with fuller text; also pull content-heavy sibling chunks per top doc
        # (first hit is often metadata-only for support_history).
        hit_ids = [h.chunk_id for h in resp.results if h.chunk_id]
        doc_ids = list({h.document_id for h in resp.results if h.document_id})[:8]
        full_text: dict[str, str] = {}
        meta_by_chunk: dict[str, Any] = {}
        if hit_ids or doc_ids:
            # keyword-preferred chunks for hit documents
            stmt = (
                select(
                    Chunk.id,
                    Chunk.document_id,
                    Chunk.text,
                    Chunk.header_context,
                    Document.title,
                    Document.external_id,
                    Document.source_type,
                    Document.source_uri,
                )
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.is_active.is_(True))
            )
            if doc_ids:
                stmt = stmt.where(Chunk.document_id.in_(doc_ids))
            elif hit_ids:
                stmt = stmt.where(Chunk.id.in_(hit_ids))
            rows = session.execute(stmt.limit(80)).all()
            for r in rows:
                blob = f"{r.header_context or ''}\n{r.title or ''}\n{r.text or ''}".strip()
                full_text[r.id] = blob
                meta_by_chunk[r.id] = r

    from types import SimpleNamespace

    def _content_score(text: str) -> int:
        t = text or ""
        score = 0
        for kw in ("원인", "조치", "해결", "증상", "분석결과", "요청이슈", "배경"):
            if kw in t:
                score += 3
        score += min(len(t) // 200, 5)
        return score

    enriched = []
    seen_chunks: set[str] = set()
    # 1) original search order with full text
    for h in resp.results:
        text = full_text.get(h.chunk_id) or h.snippet or ""
        seen_chunks.add(h.chunk_id)
        enriched.append(
            SimpleNamespace(
                document_id=h.document_id,
                chunk_id=h.chunk_id,
                title=h.title,
                external_id=h.external_id,
                source_type=h.source_type,
                snippet=text[:2000],
                source_uri=h.source_uri,
                score=h.score,
            )
        )
    # 2) inject highest-content sibling chunks not already included
    siblings = []
    for cid, blob in full_text.items():
        if cid in seen_chunks:
            continue
        siblings.append(( _content_score(blob), cid, blob))
    siblings.sort(reverse=True)
    for sc, cid, blob in siblings[: max(4, top_k)]:
        if sc < 3:
            continue
        m = meta_by_chunk.get(cid)
        if not m:
            continue
        enriched.append(
            SimpleNamespace(
                document_id=m.document_id,
                chunk_id=cid,
                title=m.title,
                external_id=m.external_id,
                source_type=m.source_type,
                snippet=blob[:2000],
                source_uri=m.source_uri,
                score=0.01 * sc,
            )
        )

    packed = pack_chunks(enriched, max_context_tokens=max_context_tokens, per_chunk_chars=1600)
    citations = [
        {
            "id": p.cite_id,
            "document_id": p.document_id,
            "chunk_id": p.chunk_id,
            "title": p.title,
            "external_id": p.external_id,
            "source_type": p.source_type,
            "snippet": p.snippet,
            "source_uri": p.source_uri,
            "score": p.score,
        }
        for p in packed
    ]
    retrieval_meta = {
        "total": resp.total,
        "trust_retrieval": resp.trust_retrieval,
        "vector_used": qvec is not None or bool(mq_meta.get("vector_used")),
        "gated": resp.gated,
        "exact_tokens": resp.exact_tokens,
        "multi_query": bool(mq_meta.get("multi_query")),
        "expanded_queries": mq_meta.get("queries"),
    }

    if not packed or resp.trust_retrieval == "empty":
        trust = assess_trust(
            retrieval_trust=resp.trust_retrieval or "empty",
            n_hits=0,
            n_citations_used=0,
            answer="",
            context_blobs=[],
            force_abstain=True,
        )
        return {
            "abstain_early": True,
            "result": {
                "query": q,
                "mode": mode,
                "answer": "제공된 근거로는 확답할 수 없습니다. 검색 질의를 바꾸거나 소스를 지정해 다시 시도하세요.",
                "abstained": True,
                "trust": trust_to_dict(trust),
                "citations": [],
                "citations_used": [],
                "retrieval": retrieval_meta,
                "llm_error": None,
            },
        }

    context = format_context_block(packed)
    user_msg = (
        f"질문: {q}\n\n"
        f"출처 블록:\n{context}\n\n"
        "출처만 근거로 답하고 [C#] 인용을 붙이세요."
    )
    if mode == "deep":
        user_msg += "\n심층 모드: 가능한 원인·조치·주의사항을 출처 범위에서 구조화해 설명하세요."

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    return {
        "abstain_early": False,
        "q": q,
        "mode": mode,
        "packed": packed,
        "citations": citations,
        "retrieval_meta": retrieval_meta,
        "messages": messages,
        "max_answer_tokens": max_answer_tokens,
    }


def _finalize(
    *,
    q: str,
    mode: str,
    packed: list[PackedChunk],
    citations: list[dict[str, Any]],
    retrieval_meta: dict[str, Any],
    answer: str,
    llm_error: Optional[str],
) -> dict[str, Any]:
    used_cites = [c for c in _extract_citation_ids(answer) if c in {p.cite_id for p in packed}]
    trust = assess_trust(
        retrieval_trust=str(retrieval_meta.get("trust_retrieval") or "weak"),
        n_hits=len(packed),
        n_citations_used=len(used_cites),
        answer=answer,
        context_blobs=[p.snippet for p in packed],
        force_abstain=False,
    )
    if trust.abstain and not llm_error:
        answer = (
            "제공된 근거로는 확답할 수 없습니다. 아래 출처를 직접 확인하세요.\n"
            + "\n".join(f"- [{p.cite_id}] {p.title}" for p in packed[:5])
        )
    return {
        "query": q,
        "mode": mode,
        "answer": answer.strip(),
        "abstained": trust.abstain,
        "trust": trust_to_dict(trust),
        "citations": citations,
        "citations_used": used_cites,
        "retrieval": retrieval_meta,
        "llm_error": llm_error,
    }


def run_fast_rag(
    q: str,
    *,
    top_k: int = 8,
    filters: Optional[SearchFilters] = None,
    max_context_tokens: int = 12_000,
    max_answer_tokens: int = 1200,
    mode: str = "fast",
    multi_query: bool = True,
) -> dict[str, Any]:
    """Execute RAG (fast or deep) and return a JSON-serializable result dict."""
    if mode not in _MODE_DEFAULTS:
        mode = "fast"
    prep = _prepare(
        q,
        mode=mode,
        top_k=top_k,
        filters=filters,
        max_context_tokens=max_context_tokens,
        max_answer_tokens=max_answer_tokens,
        multi_query=multi_query,
    )
    if prep.get("abstain_early"):
        return prep["result"]

    packed: list[PackedChunk] = prep["packed"]
    llm_error = None
    answer = ""
    try:
        answer = (
            chat_complete(
                prep["messages"],
                max_tokens=prep["max_answer_tokens"],
                temperature=0.25 if mode == "fast" else 0.3,
            )
            or ""
        ).strip()
        if not answer:
            answer = (
                chat_complete(
                    prep["messages"],
                    max_tokens=prep["max_answer_tokens"],
                    temperature=0.2,
                )
                or ""
            ).strip()
        # Citation enforcement: rewrite once if answer has claims but no [C#]
        if answer and not _looks_like_abstain(answer) and not _extract_citation_ids(answer):
            retry_msgs = list(prep["messages"]) + [
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": (
                        "인용 [C1],[C2]… 가 없습니다. 같은 내용을 유지하되 "
                        "각 사실 문장 끝에 반드시 출처 번호를 붙여 다시 작성하세요. "
                        "출처에 없는 내용은 삭제하세요."
                    ),
                },
            ]
            rewritten = (
                chat_complete(
                    retry_msgs,
                    max_tokens=prep["max_answer_tokens"],
                    temperature=0.15,
                )
                or ""
            ).strip()
            if rewritten:
                answer = rewritten
                logger.info("citation rewrite applied q=%s", q[:80])
        if not answer:
            llm_error = "empty_completion"
            answer = _snippet_fallback(packed, "모델이 빈 응답을 반환해 검색 근거만 요약합니다.")
        # Final safety: still no cites → append grounded bullet summary with cites
        if (
            answer
            and not _looks_like_abstain(answer)
            and not _extract_citation_ids(answer)
            and packed
        ):
            answer = (
                answer.rstrip()
                + "\n\n(자동 보강 근거)\n"
                + "\n".join(
                    f"- [{p.cite_id}] {p.title}: {p.snippet[:120]}" for p in packed[:3]
                )
            )
    except LLMChatError as exc:
        logger.warning("LLM failed: %s", exc)
        llm_error = str(exc)
        answer = _snippet_fallback(packed, "생성 모델 호출에 실패해 검색 근거만 요약합니다.")

    return _finalize(
        q=q,
        mode=mode,
        packed=packed,
        citations=prep["citations"],
        retrieval_meta=prep["retrieval_meta"],
        answer=answer,
        llm_error=llm_error,
    )


def stream_rag(
    q: str,
    *,
    mode: str = "fast",
    top_k: Optional[int] = None,
    filters: Optional[SearchFilters] = None,
    max_context_tokens: Optional[int] = None,
    max_answer_tokens: Optional[int] = None,
    multi_query: bool = True,
) -> Iterator[dict[str, Any]]:
    """Yield SSE event dicts: meta | token | done | error."""
    if mode not in _MODE_DEFAULTS:
        mode = "fast"
    prep = _prepare(
        q,
        mode=mode,
        top_k=top_k,
        filters=filters,
        max_context_tokens=max_context_tokens,
        max_answer_tokens=max_answer_tokens,
        multi_query=multi_query,
    )
    if prep.get("abstain_early"):
        yield {"type": "done", "result": prep["result"]}
        return

    packed: list[PackedChunk] = prep["packed"]
    yield {
        "type": "meta",
        "mode": mode,
        "retrieval": prep["retrieval_meta"],
        "citations": prep["citations"],
    }

    parts: list[str] = []
    llm_error = None
    try:
        for delta in chat_complete_stream(
            prep["messages"],
            max_tokens=prep["max_answer_tokens"],
            temperature=0.25 if mode == "fast" else 0.3,
        ):
            parts.append(delta)
            yield {"type": "token", "text": delta}
        answer = "".join(parts).strip()
        if not answer:
            llm_error = "empty_completion"
            answer = _snippet_fallback(packed, "모델이 빈 응답을 반환해 검색 근거만 요약합니다.")
        elif not _looks_like_abstain(answer) and not _extract_citation_ids(answer) and packed:
            # Post-stream citation safety net (no second stream)
            answer = (
                answer.rstrip()
                + "\n\n(자동 보강 근거)\n"
                + "\n".join(
                    f"- [{p.cite_id}] {p.title}: {p.snippet[:120]}" for p in packed[:3]
                )
            )
    except LLMChatError as exc:
        llm_error = str(exc)
        yield {"type": "error", "error": llm_error}
        answer = _snippet_fallback(packed, "생성 모델 호출에 실패해 검색 근거만 요약합니다.")

    result = _finalize(
        q=q,
        mode=mode,
        packed=packed,
        citations=prep["citations"],
        retrieval_meta=prep["retrieval_meta"],
        answer=answer,
        llm_error=llm_error,
    )
    yield {"type": "done", "result": result}
