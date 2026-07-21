"""wiki-qa compatible external integration API surface.

Exposes `/api/*` paths that mirror citec-wiki-qa endpoints used by MCP and
other external systems, mapping onto citec-kb hybrid search / RAG / insights.

Native citec-kb APIs remain under `/v1/*`. Prefer `/v1/*` for new integrations;
use `/api/*` when migrating clients that already call wiki-qa.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import __version__
from app.audit.log import list_recent_queries, log_query_answer
from app.db.models import Document, Feedback, Insight
from app.db.session import session_scope
from app.doc_access import attach_document_access, document_access
from app.rag.pipeline import run_fast_rag, stream_rag
from app.retrieval.multi_query import multi_hybrid_search
from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search
from app.settings import get_settings

router = APIRouter(tags=["external-compat (wiki-qa)"])

# wiki-qa template / section → citec-kb source_type
_SECTION_MAP: dict[str, Optional[str]] = {
    "": None,
    "general": None,
    "checkitems": "checkitem",
    "checkitem": "checkitem",
    "support_history": "support_history",
    "incident_reports": "support_history",
    "vendor_docs": "vendor_docs",
    "tech_repo": "tech_repo",
    "tuning_ai": "tuning_ai",
    "sql_tuning": "tuning_ai",
    "confluence_docs": "confluence_docs",
    "synthesis": "insight",
    "insight": "insight",
    "insights": "insight",
}

_TEMPLATE_LABELS = {
    "general": "전체",
    "checkitems": "PISA 체크리스트",
    "support_history": "기술지원이력",
    "incident_reports": "장애/지원이력",
    "vendor_docs": "벤더 문서",
    "tech_repo": "테크리포",
    "tuning_ai": "DBMS튜닝",
    "synthesis": "Insight/합성지식",
}


def _map_section(section: str | None) -> Optional[str]:
    if not section:
        return None
    key = section.strip().lower()
    if key in _SECTION_MAP:
        return _SECTION_MAP[key]
    # passthrough known source_type values
    return section.strip() or None


def _doc_path(d: Document) -> str:
    """Stable path-like id for wiki-qa clients (section/external_id)."""
    st = d.source_type or "doc"
    eid = (d.external_id or d.id or "").strip()
    if eid.endswith(".md"):
        return f"{st}/{eid}"
    return f"{st}/{eid}.md" if eid else f"{st}/{d.id}.md"


def _search_results(
    q: str,
    *,
    source_type: Optional[str],
    domain: Optional[str],
    limit: int,
) -> dict[str, Any]:
    q = (q or "").strip()[:200]
    if not q:
        return {"results": [], "total": 0, "fts_ready": True, "backend": "citec-kb"}

    embed_fn = None
    qvec = None
    try:
        from app.embed.model import embed_query

        embed_fn = embed_query
        qvec = embed_query(q)
    except Exception:  # noqa: BLE001
        pass

    filters = SearchFilters(
        source_type=source_type,
        domain=domain,
        status="active",
    )
    req = SearchRequest(q=q, top_k=min(max(limit, 1), 100), filters=filters)
    with session_scope() as session:
        if embed_fn is not None:
            resp, _meta = multi_hybrid_search(
                session, req, query_vector=qvec, embed_fn=embed_fn, multi_query=True
            )
        else:
            resp = hybrid_search(session, req, query_vector=qvec)

    results = []
    for r in resp.results:
        st = r.source_type or ""
        eid = r.external_id or r.document_id or ""
        path = f"{st}/{eid}.md" if eid and not str(eid).endswith(".md") else f"{st}/{eid}"
        results.append(
            attach_document_access(
                {
                    "path": path,
                    "section": st,
                    "title": r.title or eid,
                    "snippet": (r.snippet or "")[:500],
                    "area": r.domain or "",
                    "category": r.work_type or "",
                    "score": r.score,
                    "document_id": r.document_id,
                    "external_id": r.external_id,
                    "source_type": st,
                    "source_uri": r.source_uri,
                }
            )
        )
    return {
        "results": results,
        "total": len(results),
        "fts_ready": True,
        "vector_used": bool(qvec is not None),
        "backend": "citec-kb",
        "trust_retrieval": resp.trust_retrieval,
    }


def _resolve_document(path: str) -> Optional[Document]:
    raw = (path or "").strip().lstrip("/")
    if not raw:
        return None
    for prefix in ("wiki/", "raw/", "file://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    p = Path(raw)
    parts = list(p.parts)
    stem = p.stem if p.suffix else p.name
    candidates = [stem, p.name, raw]
    source_type = parts[0] if len(parts) >= 2 else None
    if source_type and source_type in {
        "support_history",
        "tech_repo",
        "checkitem",
        "tuning_ai",
        "confluence_docs",
        "vendor_docs",
        "insight",
        "incident_reports",
    }:
        if source_type == "incident_reports":
            source_type = "support_history"
        if source_type == "checkitems":
            source_type = "checkitem"

    with session_scope() as session:
        # by primary key
        for c in candidates:
            doc = session.get(Document, c)
            if doc and doc.status == "active":
                session.expunge(doc)
                return doc
        # by external_id (+ optional source_type)
        for c in candidates:
            stmt = select(Document).where(Document.status == "active").where(
                Document.external_id == c
            )
            if source_type:
                stmt = stmt.where(Document.source_type == source_type)
            doc = session.scalars(stmt.limit(1)).first()
            if doc:
                session.expunge(doc)
                return doc
        # fuzzy: external_id startswith / contains
        for c in candidates:
            if len(c) < 3:
                continue
            stmt = (
                select(Document)
                .where(Document.status == "active")
                .where(
                    or_(
                        Document.external_id == c,
                        Document.external_id.ilike(c + "%"),
                        Document.source_uri.ilike(f"%{c}%"),
                    )
                )
                .limit(1)
            )
            if source_type:
                stmt = stmt.where(Document.source_type == source_type)
            doc = session.scalars(stmt).first()
            if doc:
                session.expunge(doc)
                return doc
    return None


# ── Health / version / stats ───────────────────────────────────────


@router.get("/api/health")
def api_health() -> dict[str, Any]:
    """wiki-qa style lightweight health (no heavy I/O)."""
    return {"ok": True, "ts": int(time.time()), "service": "citec-kb", "version": __version__}


@router.get("/api/version")
def api_version() -> dict[str, Any]:
    settings = get_settings()
    return {
        "version": __version__,
        "service": "citec-kb",
        "env": settings.app_env,
        "compat": "wiki-qa-external",
    }


@router.get("/api/wiki-stats")
def api_wiki_stats() -> dict[str, Any]:
    """Corpus stats shaped for wiki-qa clients."""
    with session_scope() as session:
        by_type = dict(
            session.execute(
                select(Document.source_type, func.count())
                .where(Document.status == "active")
                .group_by(Document.source_type)
            ).all()
        )
        total = int(session.scalar(
            select(func.count()).select_from(Document).where(Document.status == "active")
        ) or 0)
        insights = int(
            session.scalar(select(func.count()).select_from(Insight)) or 0
        )
    # wiki-qa used section names; include both
    sections = dict(by_type)
    if "checkitem" in sections and "checkitems" not in sections:
        sections["checkitems"] = sections["checkitem"]
    return {
        "total": total,
        "by_source_type": by_type,
        "sections": sections,
        "synthesis": insights,
        "insights": insights,
        "backend": "citec-kb",
    }


@router.get("/api/recent-questions")
def api_recent_questions(limit: int = 20) -> dict[str, Any]:
    data = list_recent_queries(limit=limit)
    items = []
    for it in data.get("items") or []:
        items.append(
            {
                "query": it.get("query"),
                "ts": it.get("created_at"),
                "mode": it.get("mode"),
                "query_id": it.get("query_id"),
            }
        )
    return {"items": items, "total": len(items)}


# ── Search / document ──────────────────────────────────────────────


@router.get("/api/wiki/search")
def api_wiki_search(
    q: str = "",
    section: str = "",
    area: str = "",
    category: str = "",  # accepted for compat; maps to work_type-ish via ignore if empty
    limit: int = 20,
) -> dict[str, Any]:
    """wiki-qa MCP `wiki_search` compatible document search."""
    _ = category  # reserved
    source_type = _map_section(section)
    domain = area.strip() or None
    return _search_results(q, source_type=source_type, domain=domain, limit=limit)


@router.get("/api/wiki/search/facets")
def api_wiki_search_facets() -> dict[str, Any]:
    with session_scope() as session:
        sections = [
            r[0]
            for r in session.execute(
                select(Document.source_type)
                .where(Document.status == "active")
                .distinct()
                .order_by(Document.source_type)
            ).all()
            if r[0]
        ]
        areas = [
            r[0]
            for r in session.execute(
                select(Document.domain)
                .where(Document.status == "active")
                .where(Document.domain.isnot(None))
                .distinct()
                .order_by(Document.domain)
                .limit(200)
            ).all()
            if r[0]
        ]
        categories = [
            r[0]
            for r in session.execute(
                select(Document.work_type)
                .where(Document.status == "active")
                .where(Document.work_type.isnot(None))
                .distinct()
                .order_by(Document.work_type)
                .limit(200)
            ).all()
            if r[0]
        ]
    return {"sections": sections, "areas": areas, "categories": categories}


@router.get("/api/wiki/file")
def api_wiki_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    """wiki-qa MCP `wiki_get_document` — return document body by path/external_id."""
    doc = _resolve_document(path)
    if not doc:
        raise HTTPException(status_code=404, detail=f"파일 없음: {path}")
    meta = dict(doc.metadata_ or {}) if isinstance(doc.metadata_, dict) else {}
    meta.update(
        {
            "source_type": doc.source_type,
            "external_id": doc.external_id,
            "document_id": doc.id,
            "domain": doc.domain,
            "environment": doc.environment,
            "work_type": doc.work_type,
            "evidence_grade": doc.evidence_grade,
        }
    )
    acc = document_access(
        external_id=doc.external_id,
        source_type=doc.source_type,
        document_id=doc.id,
        path=_doc_path(doc),
        title=doc.title,
    )
    return {
        "path": _doc_path(doc),
        "content": doc.body_md or "",
        "meta": meta,
        "title": doc.title,
        "external_id": doc.external_id,
        "source_type": doc.source_type,
        "document_id": doc.id,
        "access": acc,
        "web_url": acc.get("web_url"),
        "body_api": acc.get("body_api"),
    }


# ── Q&A (SSE) ──────────────────────────────────────────────────────


class WikiQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    template: str = Field(
        default="general",
        description="general|checkitems|support_history|tech_repo|tuning_ai|synthesis|…",
    )
    mode: str = Field(default="fast", description="fast|deep (citec-kb extension)")
    stream: bool = Field(default=True, description="SSE when true (wiki-qa default)")


@router.post("/api/query")
def api_query(req: WikiQueryRequest) -> Any:
    """wiki-qa MCP `wiki_ask` compatible Q&A.

    SSE events (wiki-qa shape):
      status {text}, sources {files}, token {text}, error {text}, done {result}
    """
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="쿼리가 비어있습니다.")

    template = (req.template or "general").strip().lower()
    source_type = _map_section(template)
    mode = req.mode if req.mode in {"fast", "deep"} else "fast"
    filters = SearchFilters(source_type=source_type, status="active")
    top_k = 16 if mode == "deep" else 8
    label = _TEMPLATE_LABELS.get(template, template)

    if not req.stream:
        t0 = time.time()
        try:
            result = run_fast_rag(
                q, mode=mode, top_k=top_k, filters=filters, multi_query=True
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        latency = int((time.time() - t0) * 1000)
        log_query_answer(
            query=q,
            mode=f"api_query:{mode}",
            filters={"template": template, "source_type": source_type},
            latency_ms=latency,
            answer_md=str(result.get("answer") or ""),
            citations=result.get("citations") or [],
            trust=result.get("trust") or {},
        )
        result["latency_ms"] = latency
        result["template"] = template
        return result

    def event_gen():
        t0 = time.time()
        final: dict[str, Any] = {}
        try:
            yield _sse(
                {
                    "type": "status",
                    "text": f"[{label}] 지식 검색 중… (citec-kb)",
                }
            )
            for ev in stream_rag(
                q, mode=mode, top_k=top_k, filters=filters, multi_query=True
            ):
                et = ev.get("type")
                if et == "meta":
                    cites = ev.get("citations") or []
                    files = []
                    for c in cites:
                        if not isinstance(c, dict):
                            continue
                        st = c.get("source_type") or ""
                        eid = c.get("external_id") or c.get("title") or ""
                        if eid:
                            files.append(
                                f"{st}/{eid}.md" if st else str(eid)
                            )
                    yield _sse({"type": "sources", "files": files})
                    yield _sse({"type": "status", "text": "답변 생성 중…"})
                elif et == "token":
                    yield _sse({"type": "token", "text": ev.get("text") or ""})
                elif et == "error":
                    yield _sse(
                        {
                            "type": "error",
                            "text": ev.get("error") or ev.get("text") or "error",
                            "error": ev.get("error"),
                        }
                    )
                elif et == "done":
                    final = ev.get("result") or {}
                    # ensure sources if early-abstain skipped meta
                    if final.get("citations") and not final.get("_sources_sent"):
                        files = []
                        for c in final.get("citations") or []:
                            if isinstance(c, dict):
                                st = c.get("source_type") or ""
                                eid = c.get("external_id") or ""
                                if eid:
                                    files.append(f"{st}/{eid}.md" if st else eid)
                        if files:
                            yield _sse({"type": "sources", "files": files})
                    yield _sse({"type": "done", "result": final})
            if final:
                log_query_answer(
                    query=q,
                    mode=f"api_query:{mode}",
                    filters={"template": template, "source_type": source_type},
                    latency_ms=int((time.time() - t0) * 1000),
                    answer_md=str(final.get("answer") or ""),
                    citations=final.get("citations") or [],
                    trust=final.get("trust") or {},
                )
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "text": str(exc), "error": str(exc)})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ── Synthesis ≈ Insights ───────────────────────────────────────────


@router.get("/api/synthesis")
def api_list_synthesis(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """wiki-qa synthesis list → citec-kb insights."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with session_scope() as session:
        total = int(session.scalar(select(func.count()).select_from(Insight)) or 0)
        rows = list(
            session.scalars(
                select(Insight)
                .order_by(Insight.updated_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        items = [
            {
                "slug": r.id,
                "id": r.id,
                "query": r.title,
                "title": r.title,
                "quality": r.status,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "source_count": len(r.source_doc_ids or []),
                "author": r.author,
            }
            for r in rows
        ]
    return {"total": total, "items": items, "backend": "citec-kb-insights"}


@router.get("/api/synthesis/{slug}")
def api_get_synthesis(slug: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(Insight, slug)
        if not row:
            raise HTTPException(status_code=404, detail="합성 답변을 찾을 수 없습니다.")
        return {
            "slug": row.id,
            "id": row.id,
            "query": row.title,
            "answer": row.body_md or "",
            "quality": row.status,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "sources": list(row.source_doc_ids or []),
            "author": row.author,
            "reviewer": row.reviewer,
            "promoted_document_id": row.promoted_document_id,
        }


# ── Feedback ───────────────────────────────────────────────────────


class WikiFeedbackReq(BaseModel):
    """wiki-qa feedback shape + citec-kb rating shape."""

    verdict: Optional[str] = Field(
        default=None,
        description="helpful|not_helpful|resolved|failed|edited (wiki-qa)",
    )
    rating: Optional[int] = Field(default=None, description="+1 or -1 (citec-kb)")
    target_type: str = Field(default="answer")
    target_id: str = Field(default="unknown")
    query_id: Optional[str] = None
    synthesis_slug: Optional[str] = None
    comment: Optional[str] = None
    note: Optional[str] = None
    user_id: Optional[str] = None


_VERDICT_RATING = {
    "helpful": 1,
    "resolved": 1,
    "not_helpful": -1,
    "failed": -1,
    "edited": 1,
    "refuted": -1,
}


@router.post("/api/feedback")
def api_feedback(req: WikiFeedbackReq) -> dict[str, Any]:
    rating = req.rating
    if rating is None and req.verdict:
        rating = _VERDICT_RATING.get(req.verdict.lower().strip())
    if rating not in (-1, 1):
        raise HTTPException(
            status_code=400,
            detail="need rating ±1 or verdict helpful|not_helpful|resolved|failed|edited",
        )
    target_type = req.target_type or "answer"
    target_id = req.target_id or req.synthesis_slug or req.query_id or "unknown"
    if target_type not in {"answer", "insight", "search", "synthesis", "query"}:
        target_type = "answer"
    if target_type == "synthesis":
        target_type = "insight"
    comment = req.comment or req.note
    if req.verdict and comment:
        comment = f"[{req.verdict}] {comment}"
    elif req.verdict:
        comment = f"[{req.verdict}]"
    with session_scope() as session:
        row = Feedback(
            target_type=target_type,
            target_id=target_id,
            rating=int(rating),
            comment=comment,
            user_id=req.user_id,
        )
        session.add(row)
        session.flush()
        return {
            "ok": True,
            "id": row.id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "rating": row.rating,
            "verdict": req.verdict,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


# ── Native external aliases under /v1/external ─────────────────────


@router.get("/v1/external/health")
def v1_ext_health() -> dict[str, Any]:
    return api_health()


@router.get("/v1/external/search")
def v1_ext_search(
    q: str = "",
    source_type: str = "",
    domain: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    return _search_results(
        q,
        source_type=source_type.strip() or None,
        domain=domain.strip() or None,
        limit=limit,
    )


@router.get("/v1/external/document")
def v1_ext_document(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    return api_wiki_file(path)


@router.get("/v1/external/catalog")
def v1_ext_catalog() -> dict[str, Any]:
    """Machine-readable map of external integration endpoints."""
    return {
        "service": "citec-kb",
        "version": __version__,
        "wiki_qa_compat": {
            "GET /api/health": "lightweight health",
            "GET /api/version": "version string",
            "GET /api/wiki-stats": "corpus counts by source_type",
            "GET /api/wiki/search": "hybrid search (q, section, area, limit) + body_api/web_url",
            "GET /api/wiki/file": "document body by path/external_id (+ access)",
            "POST /api/query": "SSE Q&A (query, template) — MCP wiki_ask",
            "GET /api/synthesis": "insight list (wiki synthesis stand-in)",
            "GET /api/synthesis/{slug}": "insight detail",
            "POST /api/feedback": "rating/verdict feedback",
            "GET /api/recent-questions": "recent audited queries",
        },
        "native_v1": {
            "POST /v1/search": "hybrid search JSON + document access fields",
            "POST /v1/chat": "RAG chat JSON/SSE (citations include web_url/body_api)",
            "POST /v1/query": "multi-intent planner (items/samples include access)",
            "GET /v1/tickets/{external_id}": "full body + access",
            "GET /v1/health": "full dependency health",
            "GET /v1/external/catalog": "this catalog",
        },
        "document_access_fields": [
            "path",
            "body_api",
            "body_api_url",
            "body_api_file",
            "web_path",
            "web_url",
            "access.mcp_tool",
            "access.mcp_args",
        ],
        "section_map": {k: v for k, v in _SECTION_MAP.items() if k},
    }
