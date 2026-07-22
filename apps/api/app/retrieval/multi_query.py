"""Production multi-query hybrid expansion + merge.

Expansions use only the user query (+ optional entity/FAQ hints). No gold labels.

Design notes (retrieval quality):
- Original (and stripped) question always run first with full weight.
- Single high-DF tokens (SCP, Multi-AZ, 2026년, product aliases) must NOT
  run as standalone multi-query heads — they drown specific tickets in RRF/max merge.
- Prefer multi-token technical phrases (SCP v2 Multi-AZ, 가용성 테스트, …).
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

# Existence / list shell — keep content for retrieval, drop interrogative tail
_QUESTION_SHELL = re.compile(
    r"(이\s*)?(있|없)(었)?(나요|습니까|는가|나|음|는지)\s*\??\s*$|"
    r"(인가|일까|인가\s*\?)\s*$|"
    r"(에\s*대한\s*)?(유무|여부|존재\s*여부)\s*\??\s*$",
    re.I,
)
_YEAR_ONLY = re.compile(r"^['’]?\d{2,4}\s*년?$")
# High document-frequency product / topology tokens — never alone as multi-query head
_HIGH_DF_SINGLE = frozenset(
    {
        "scp",
        "scpv1",
        "scpv2",
        "v1",
        "v2",
        "multi-az",
        "multi",
        "maz",
        "az",
        "vpc",
        "bm",
        "vm",
        "db",
        "obs",
        "삼성클라우드",
        "samsung",
        "cloud",
        "platform",
        "samsung cloud platform",
        "삼성 클라우드",
        "테스트",
        "test",
        "성능",
        "가용성",
        "이중화",
        "네트워크",
    }
)

# Technical multi-token phrases extracted when present (boost precision)
_PHRASE_EXTRACT: list[tuple[re.Pattern[str], list[str]]] = [
    (
        re.compile(r"SCP\s*v?\s*2|SCPv2|SCP\s*V2", re.I),
        ["SCP v2", "SCPv2", "SCP V2"],
    ),
    (
        re.compile(r"SCP\s*v?\s*1|SCPv1|SCP\s*V1", re.I),
        ["SCP v1", "SCPv1"],
    ),
    (
        re.compile(r"Multi[\s\-]?AZ|멀티\s*AZ|M\s*AZ|MAZ", re.I),
        ["Multi-AZ", "Multi AZ", "멀티AZ", "MAZ"],
    ),
    (
        re.compile(r"가용성\s*테스트|가용성\s*점검|availability\s*test", re.I),
        ["가용성 테스트", "가용성 점검"],
    ),
    (
        re.compile(r"성능\s*/\s*가용성|성능\s*가용성|성능\s*·\s*가용성", re.I),
        ["성능 가용성 테스트", "성능/가용성 테스트"],
    ),
    (
        re.compile(r"이중화\s*테스트", re.I),
        ["이중화 테스트"],
    ),
    (
        re.compile(r"그룹\s*26\s*[\-–]?\s*5|그룹26-5", re.I),
        ["그룹26-5"],
    ),
]


def strip_question_shell(q: str) -> str:
    """Remove 「…가 있는가?」 style shells so FTS/vector focus on topic tokens."""
    s = (q or "").strip()
    s = re.sub(r"[?？]+$", "", s).strip()
    s = _QUESTION_SHELL.sub("", s).strip()
    # trailing 가/이 after shell strip: 「테스트가」→「테스트」
    s = re.sub(r"(이|가)\s*$", "", s).strip()
    return s or (q or "").strip()


def _is_high_df_singleton(v: str) -> bool:
    t = re.sub(r"\s+", " ", (v or "").strip().lower())
    if not t:
        return True
    if _YEAR_ONLY.match(t):
        return True
    if t in _HIGH_DF_SINGLE:
        return True
    # bare year+년 already covered; bare product codes length <= 3
    if " " not in t and len(t) <= 3:
        return True
    return False


def _query_weight(qq: str, original: str, stripped: str) -> float:
    """Weight multi-query heads: original highest, phrases medium, singles low (usually filtered)."""
    qn = (qq or "").strip()
    if not qn:
        return 0.0
    if qn == original or qn == stripped:
        return 1.0
    if _CITECTS.search(qn) or _PISA.search(qn):
        return 1.15
    if qn == _FAQ_DOC or "QRB" in qn:
        return 1.1
    n_tok = len(re.split(r"\s+", qn))
    if n_tok >= 3:
        return 0.95
    if n_tok == 2:
        return 0.85
    if _is_high_df_singleton(qn):
        return 0.25
    return 0.55


def expand_queries(
    q: str,
    *,
    extra: Optional[list[str]] = None,
    max_queries: int = 6,
) -> list[str]:
    """Build ordered query variants for multi-hybrid merge.

    Order: original → stripped → CITECTS/PISA → technical phrases → safe FTS variants.
    """
    q = (q or "").strip()
    if not q:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(v: str, *, allow_singleton: bool = False) -> None:
        v = (v or "").strip()
        if not v:
            return
        if not allow_singleton and _is_high_df_singleton(v) and v != q and v != strip_question_shell(q):
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(v)

    stripped = strip_question_shell(q)

    # 1) Original + shell-stripped always first (precision path)
    add(q, allow_singleton=True)
    if stripped and stripped.lower() != q.lower():
        add(stripped, allow_singleton=True)

    # 2) High-precision ids
    for m in _CITECTS.finditer(q):
        tok = m.group(0)
        if tok.upper().startswith("CITECTS-"):
            add("CITECTS-" + tok.split("-", 1)[1], allow_singleton=True)
        else:
            add(tok, allow_singleton=True)

    for m in _PISA.finditer(q):
        add(m.group(0), allow_singleton=True)

    if _FAQ_HINT.search(q):
        add(_FAQ_DOC, allow_singleton=True)

    # 3) Technical multi-token / synonym phrases present in the query
    for pat, phrases in _PHRASE_EXTRACT:
        if pat.search(q) or (stripped and pat.search(stripped)):
            for p in phrases:
                add(p)

    # Combined high-value phrases when parts co-occur
    has_scp_v2 = bool(re.search(r"SCP\s*v?\s*2|SCPv2", q, re.I))
    has_maz = bool(re.search(r"Multi[\s\-]?AZ|멀티\s*AZ|MAZ", q, re.I))
    has_avail = bool(re.search(r"가용성|성능", q, re.I))
    if has_scp_v2 and has_maz:
        add("SCP v2 Multi-AZ")
        add("SCPv2 Multi-AZ")
    if has_scp_v2 and has_avail:
        add("SCP v2 가용성 테스트")
        add("SCP 성능 가용성 테스트")
    if has_maz and has_avail:
        add("Multi-AZ 가용성 테스트")
        add("Multi-AZ 이중화 테스트")

    # 4) Entity aliases — only multi-word or long names (never bare SCP)
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
                    if not name or len(name) < 4:
                        continue
                    if name.lower() not in ql:
                        continue
                    # multi-word or long; skip high-DF singles
                    if " " in name or len(name) >= 6:
                        if not _is_high_df_singleton(name):
                            add(name)
                    break
    except Exception:  # noqa: BLE001
        pass

    # 5) FTS variants — phrases only for multi-query heads
    for v in build_fts_variants(stripped or q):
        if len(v) < 4 or len(v) > 64:
            continue
        if v == q or v == stripped:
            continue
        # multi-token always ok; single only if CITECTS/PISA-like or long non-DF
        if " " in v or "/" in v or "·" in v:
            add(v)
        elif re.match(r"(?i)citects-|pisa", v):
            add(v, allow_singleton=True)
        elif len(v) >= 8 and not _is_high_df_singleton(v):
            add(v)
        if len(out) >= max_queries:
            break

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
    """Run one or more hybrid searches and merge by weighted max score per external_id.

    Returns (response, meta) where meta includes expanded queries.
    """
    if not multi_query:
        resp = hybrid_search(session, req, query_vector=query_vector)
        return resp, {"multi_query": False, "queries": [req.q], "n_queries": 1}

    queries = expand_queries(req.q, extra=extra_queries)
    if not queries:
        queries = [req.q]

    stripped = strip_question_shell(req.q)
    best: dict[str, SearchHit] = {}
    best_score: dict[str, float] = {}
    vector_used = False
    first_resp: Optional[SearchResponse] = None

    for i, qq in enumerate(queries):
        w = _query_weight(qq, req.q, stripped)
        qvec = None
        # Prefer embedding the original question (index 0 after reorder)
        if i == 0 and query_vector is not None:
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
            score = float(hit.score or 0) * w
            # Title/body soft boost when multi-token query terms co-occur in title
            title = (hit.title or "").lower()
            if qq == req.q or qq == stripped:
                if "multi-az" in title.replace(" ", "") or "multi az" in title:
                    if "scp" in title and ("가용" in title or "성능" in title or "테스트" in title):
                        score *= 1.15
            prev = best_score.get(eid)
            if prev is None or score > prev:
                best_score[eid] = score
                # store hit with adjusted score for ranking
                best[eid] = SearchHit(
                    rank=hit.rank,
                    score=score,
                    document_id=hit.document_id,
                    chunk_id=hit.chunk_id,
                    title=hit.title,
                    snippet=hit.snippet,
                    source_type=hit.source_type,
                    external_id=hit.external_id,
                    evidence_grade=hit.evidence_grade,
                    domain=hit.domain,
                    environment=hit.environment,
                    work_type=hit.work_type,
                    path_l2=hit.path_l2,
                    source_uri=hit.source_uri,
                    fts_rank=hit.fts_rank,
                    vec_rank=hit.vec_rank,
                )

    merged = sorted(best.values(), key=lambda h: float(h.score or 0), reverse=True)[: req.top_k]
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
        "stripped_q": stripped if stripped != req.q else None,
    }
    return out, meta
