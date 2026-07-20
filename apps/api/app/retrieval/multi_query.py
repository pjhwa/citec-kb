"""Production multi-query hybrid expansion + merge.

Expansions use only the user query (+ optional entity/FAQ hints). No gold labels.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.retrieval.search import (
    SearchHit,
    SearchRequest,
    SearchResponse,
    build_fts_variants,
    hybrid_search,
)

_CITECTS = re.compile(r"\bCITECTS-\d+\b", re.I)
_PISA = re.compile(r"\bPISA[A-Z0-9_]{2,}\b", re.I)
_FAQ_HINT = re.compile(
    r"FAQ|M\s*/\s*M|공수|단가|1안|서비스\s*base|PISA\s*방법론|진단\s*범위|Lookin\s*진단",
    re.I,
)
_FAQ_DOC = "QRB_품질_인프라구축검증_PISA_FAQ"


def expand_queries(
    q: str,
    *,
    extra: Optional[list[str]] = None,
    max_queries: int = 6,
) -> list[str]:
    """Build ordered query variants for multi-hybrid merge."""
    q = (q or "").strip()
    if not q:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        v = (v or "").strip()
        if not v:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(v)

    # High-precision tokens first
    for m in _CITECTS.finditer(q):
        add(m.group(0).upper().replace("CITECTS", "CITECTS") if False else m.group(0))
        # normalize CITECTS-#### casing
        tok = m.group(0)
        if tok.upper().startswith("CITECTS-"):
            add("CITECTS-" + tok.split("-", 1)[1])
        else:
            add(tok)

    for m in _PISA.finditer(q):
        add(m.group(0))

    if _FAQ_HINT.search(q):
        add(_FAQ_DOC)

    # Entity aliases that appear in the query (if table seeded)
    try:
        from sqlalchemy import select

        from app.db.models import Entity
        from app.db.session import session_scope

        with session_scope() as session:
            ents = list(session.scalars(select(Entity)).all())
            ql = q.lower()
            for e in ents:
                names = [e.canonical_name or ""] + list(e.aliases or [])
                for name in names:
                    if name and len(name) >= 2 and name.lower() in ql:
                        add(name)
                        break
    except Exception:  # noqa: BLE001
        pass

    # Short FTS variants — multi-token / synonym phrases only (avoid single junk tokens)
    _skip = {"vs", "the", "and", "or", "a", "an", "is", "to", "of", "for", "in", "on"}
    for v in build_fts_variants(q):
        if len(v) < 4 or len(v) > 48:
            continue
        if " " not in v and v.lower() in _skip:
            continue
        # prefer phrases; single tokens must be long enough / technical
        if " " in v:
            add(v)
        elif len(v) >= 6 or re.search(r"\d", v) or re.match(r"(?i)citects-|pisa", v):
            add(v)
        if len(out) >= max_queries - 1:
            break

    # Original query always included
    add(q)

    for e in extra or []:
        add(str(e))

    return out[:max_queries]



def multi_hybrid_search(
    session: Session,
    req: SearchRequest,
    *,
    query_vector: Optional[list[float]] = None,
    embed_fn: Optional[Callable[[str], list[float]]] = None,
    multi_query: bool = True,
    extra_queries: Optional[list[str]] = None,
) -> tuple[SearchResponse, dict[str, Any]]:
    """Run one or more hybrid searches and merge by max score per external_id.

    Returns (response, meta) where meta includes expanded queries.
    """
    if not multi_query:
        resp = hybrid_search(session, req, query_vector=query_vector)
        return resp, {"multi_query": False, "queries": [req.q], "n_queries": 1}

    queries = expand_queries(req.q, extra=extra_queries)
    if not queries:
        queries = [req.q]

    best: dict[str, SearchHit] = {}
    vector_used = False
    first_resp: Optional[SearchResponse] = None

    for i, qq in enumerate(queries):
        qvec = None
        if i == 0 and query_vector is not None and qq == req.q:
            qvec = query_vector
            vector_used = True
        elif embed_fn is not None:
            try:
                qvec = embed_fn(qq)
                vector_used = vector_used or qvec is not None
            except Exception:  # noqa: BLE001
                qvec = None

        sub = replace(req, q=qq)
        resp = hybrid_search(session, sub, query_vector=qvec)
        if first_resp is None:
            first_resp = resp
        for hit in resp.results:
            eid = hit.external_id or hit.document_id
            if not eid:
                continue
            prev = best.get(eid)
            if prev is None or float(hit.score or 0) > float(prev.score or 0):
                best[eid] = hit

    merged = sorted(best.values(), key=lambda h: float(h.score or 0), reverse=True)[: req.top_k]
    # re-rank display ranks
    ranked: list[SearchHit] = []
    for i, h in enumerate(merged, 1):
        ranked.append(
            SearchHit(
                rank=i,
                score=h.score,
                document_id=h.document_id,
                chunk_id=h.chunk_id,
                title=h.title,
                snippet=h.snippet,
                source_type=h.source_type,
                external_id=h.external_id,
                evidence_grade=h.evidence_grade,
                domain=h.domain,
                environment=h.environment,
                work_type=h.work_type,
                path_l2=h.path_l2,
                source_uri=h.source_uri,
                fts_rank=h.fts_rank,
                vec_rank=h.vec_rank,
            )
        )

    base = first_resp or hybrid_search(session, req, query_vector=query_vector)
    out = SearchResponse(
        query=req.q,
        exact_tokens=base.exact_tokens,
        total=len(ranked),
        gated=base.gated if ranked else True,
        trust_retrieval=base.trust_retrieval if ranked else "low",
        results=ranked,
    )
    meta = {
        "multi_query": True,
        "queries": queries,
        "n_queries": len(queries),
        "vector_used": vector_used,
    }
    return out, meta
