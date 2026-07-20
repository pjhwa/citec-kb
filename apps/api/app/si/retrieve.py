"""Similar incident dual retrieve: hybrid search + issue frame quality.

Returns a 4-slot briefing style payload for War-room MVP.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import or_, select

from app.bundles.match import match_bundles
from app.db.models import Document, IssueFrame
from app.db.session import session_scope
from app.embed.model import embed_query
from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]{3,}")


def _query_tokens(q: str) -> list[str]:
    toks = [t.lower() for t in _TOKEN_RE.findall(q or "")]
    # drop ultra-common noise
    stop = {"장애", "이슈", "관련", "문제", "support", "the", "and", "for"}
    return [t for t in toks if t not in stop]


def _frame_blob(fr: IssueFrame, doc: Document, hit_snippet: str = "") -> str:
    parts = [
        doc.title or "",
        doc.external_id or "",
        fr.symptom or "",
        fr.root_cause or "",
        fr.resolution or "",
        hit_snippet or "",
        " ".join(fr.components or []),
    ]
    return " ".join(parts).lower()


def _text_match_boost(query: str, blob: str) -> float:
    tokens = _query_tokens(query)
    if not tokens or not blob:
        return 0.0
    hits = sum(1 for t in tokens if t in blob)
    # strong multi-token match (e.g. gpu+fabric+spine)
    if hits >= 3:
        return 0.55 + 0.05 * min(hits - 3, 4)
    return 0.12 * hits


def _applicability(
    *,
    query: str,
    components: list[str],
    environment: Optional[str],
    quality: float,
    has_resolution: bool,
    text_overlap: int = 0,
) -> dict[str, Any]:
    """Heuristic applicability label (Phase 2 v1 — not ML)."""
    q_low = (query or "").lower()
    overlap = sum(1 for c in components if c.lower() in q_low or c in query)
    # also count free-text token overlap from frames
    overlap = max(overlap, min(text_overlap, 3))
    score = 0.2 + 0.15 * min(overlap, 3) + 0.25 * min(quality, 1.0)
    if has_resolution:
        score += 0.2
    if environment and environment.lower() in q_low:
        score += 0.1
    if text_overlap >= 3:
        score += 0.1
    score = min(1.0, score)

    if not has_resolution or quality < 0.25:
        label = "기권"
        note = "원인·조치 프레임이 불충분해 적용을 권고하지 않습니다."
    elif score >= 0.7 and (overlap >= 1 or text_overlap >= 2):
        label = "가능"
        note = "증상·구성 요소가 유사하고 조치가 문서화되어 있습니다. 환경 차이를 확인하세요."
    elif score >= 0.45:
        label = "조건부"
        note = "부분 유사합니다. 동일 제품/환경인지 확인 후 조건부 적용하세요."
    else:
        label = "비권고"
        note = "유사도가 낮거나 조치 근거가 약합니다."

    return {
        "label": label,
        "score": round(score, 3),
        "note": note,
        "component_overlap": overlap,
        "text_token_hits": text_overlap,
    }


def similar_incidents(
    symptom: str,
    *,
    top_k: int = 3,
    environment: Optional[str] = None,
    product: Optional[str] = None,
    service: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve similar past incidents with frames and applicability."""
    q_parts = [symptom]
    if product:
        q_parts.append(product)
    if service:
        q_parts.append(service)
    if environment:
        q_parts.append(environment)
    q = " ".join(p for p in q_parts if p).strip()
    tokens = _query_tokens(q)

    qvec = None
    try:
        qvec = embed_query(q)
    except Exception:  # noqa: BLE001
        qvec = None

    filters = SearchFilters(source_type="support_history", status="active")
    if environment:
        filters.environment = environment

    with session_scope() as session:
        # Broader hybrid pool so high-quality frames can still surface
        resp = hybrid_search(
            session,
            SearchRequest(q=q, top_k=max(top_k * 8, 40), filters=filters),
            query_vector=qvec,
        )

        seen: set[str] = set()
        ordered_docs: list[str] = []
        hit_by_doc: dict[str, Any] = {}
        for h in resp.results:
            if h.document_id in seen:
                continue
            seen.add(h.document_id)
            ordered_docs.append(h.document_id)
            hit_by_doc[h.document_id] = h

        frames: dict[str, tuple[IssueFrame, Document]] = {}
        if ordered_docs:
            rows = session.execute(
                select(IssueFrame, Document)
                .join(Document, Document.id == IssueFrame.document_id)
                .where(IssueFrame.document_id.in_(ordered_docs))
            ).all()
            for fr, doc in rows:
                frames[fr.document_id] = (fr, doc)

        # Inject high-quality frames that match query tokens even if search rank is low
        # (fixes cases like GPU/fabric/spine → CITECTS-2502).
        if tokens:
            like_clauses = []
            for t in tokens[:8]:
                like = f"%{t}%"
                like_clauses.extend(
                    [
                        IssueFrame.symptom.ilike(like),
                        IssueFrame.root_cause.ilike(like),
                        IssueFrame.resolution.ilike(like),
                        Document.title.ilike(like),
                        Document.external_id.ilike(like),
                    ]
                )
            inj = session.execute(
                select(IssueFrame, Document)
                .join(Document, Document.id == IssueFrame.document_id)
                .where(Document.source_type == "support_history")
                .where(Document.status == "active")
                .where(IssueFrame.quality >= 0.45)
                .where(or_(*like_clauses))
                .order_by(IssueFrame.quality.desc())
                .limit(25)
            ).all()
            for fr, doc in inj:
                frames[fr.document_id] = (fr, doc)
                if doc.id not in seen:
                    seen.add(doc.id)
                    ordered_docs.append(doc.id)

        # Rank: search order + frame quality + free-text token match
        scored: list[tuple[float, str]] = []
        for i, did in enumerate(ordered_docs):
            # search rank base; injected-only docs get a modest base
            base = 1.0 / (1 + i) if did in hit_by_doc else 0.05
            fr_doc = frames.get(did)
            qboost = 0.0
            tboost = 0.0
            if fr_doc:
                fr, doc = fr_doc
                hit = hit_by_doc.get(did)
                blob = _frame_blob(fr, doc, hit.snippet if hit else "")
                tboost = _text_match_boost(q, blob)
                qboost = 0.35 * float(fr.quality or 0.0)
                if fr.resolution and fr.root_cause:
                    qboost += 0.2
            elif did in hit_by_doc:
                hit = hit_by_doc[did]
                blob = f"{hit.title or ''} {hit.snippet or ''}".lower()
                tboost = _text_match_boost(q, blob)
            scored.append((base + qboost + tboost, did))
        scored.sort(key=lambda x: x[0], reverse=True)

        cases = []
        for score, did in scored[:top_k]:
            hit = hit_by_doc.get(did)
            fr_doc = frames.get(did)
            if fr_doc:
                fr, doc = fr_doc
                components = list(fr.components or [])
                blob = _frame_blob(fr, doc, hit.snippet if hit else "")
                token_hits = sum(1 for t in tokens if t in blob)
                app = _applicability(
                    query=q,
                    components=components,
                    environment=fr.environment or doc.environment,
                    quality=float(fr.quality or 0.0),
                    has_resolution=bool(fr.resolution),
                    text_overlap=token_hits,
                )
                cases.append(
                    {
                        "document_id": doc.id,
                        "external_id": doc.external_id,
                        "title": doc.title,
                        "source_uri": doc.source_uri,
                        "rank_score": round(score, 4),
                        "what": fr.symptom or (hit.snippet if hit else doc.title),
                        "cause": fr.root_cause,
                        "resolution": fr.resolution,
                        "workaround": fr.workaround,
                        "components": components,
                        "environment": fr.environment or doc.environment,
                        "frame_quality": float(fr.quality or 0.0),
                        "applicability": app,
                        "search_score": hit.score if hit else None,
                    }
                )
            elif hit:
                blob = f"{hit.title or ''} {hit.snippet or ''}".lower()
                token_hits = sum(1 for t in tokens if t in blob)
                app = _applicability(
                    query=q,
                    components=[],
                    environment=hit.environment,
                    quality=0.1,
                    has_resolution=False,
                    text_overlap=token_hits,
                )
                cases.append(
                    {
                        "document_id": hit.document_id,
                        "external_id": hit.external_id,
                        "title": hit.title,
                        "source_uri": hit.source_uri,
                        "rank_score": round(score, 4),
                        "what": hit.snippet or hit.title,
                        "cause": None,
                        "resolution": None,
                        "workaround": None,
                        "components": [],
                        "environment": hit.environment,
                        "frame_quality": 0.0,
                        "applicability": app,
                        "search_score": hit.score,
                        "note": "issue_frame 없음 — 검색 스니펫만 제공",
                    }
                )

    brief = _build_brief(symptom, cases)
    actions = _suggested_actions(cases)
    questions = [
        "현재 환경(CSP/온프레/MSP)과 제품 버전이 유사 사례와 동일한가?",
        "조치 적용 전 롤백 방법이 준비되어 있는가?",
        "업무 시간 외 적용이 필요한가?",
    ]
    if cases and (cases[0].get("applicability") or {}).get("label") == "가능":
        questions.insert(0, "1순위 사례의 조치 전제(네트워크 구간·버전)가 현재와 같은가?")

    bundles = match_bundles(q, top_k=2)

    return {
        "query": {
            "symptom": symptom,
            "environment": environment,
            "product": product,
            "service": service,
            "q": q,
        },
        "brief": brief,
        "cases": cases,
        "actions": actions,
        "questions": questions,
        "bundles": bundles,
        "retrieval": {
            "vector_used": qvec is not None,
            "trust_retrieval": resp.trust_retrieval,
            "candidates": len(ordered_docs),
        },
    }


def _build_brief(symptom: str, cases: list[dict[str, Any]]) -> str:
    if not cases:
        return (
            f"증상「{symptom[:80]}」에 대한 유사 지원이력을 찾지 못했습니다. "
            "키워드를 구체화하거나 검색 UI에서 직접 조회하세요."
        )
    top = cases[0]
    app = (top.get("applicability") or {}).get("label", "?")
    lines = [
        f"유사 과거 사례 {len(cases)}건을 찾았습니다. 1순위: {top.get('external_id')} — {top.get('title', '')[:80]}",
        f"적용성(휴리스틱): {app}.",
    ]
    if top.get("cause"):
        lines.append(f"추정 공통 원인 힌트: {str(top['cause'])[:160]}")
    if top.get("resolution"):
        lines.append(f"참고 조치: {str(top['resolution'])[:160]}")
    lines.append("단정 적용 금지 — 환경 차이와 원문을 확인하세요.")
    return " ".join(lines)


def _suggested_actions(cases: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for c in cases[:2]:
        if c.get("resolution"):
            actions.append(f"{c.get('external_id')}: {str(c['resolution'])[:200]}")
    if not actions:
        actions.append("유사 사례 원문(지원이력)을 열어 조치 섹션을 확인하세요.")
    actions.append("변경 전 영향 범위·롤백 플랜을 문서화하세요.")
    return actions
