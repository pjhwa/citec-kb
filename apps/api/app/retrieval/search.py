"""Hybrid search over Postgres FTS + pgvector."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Embedding
from app.retrieval.fusion import (
    RankedHit,
    apply_exact_boost,
    extract_exact_tokens,
    merge_to_hits,
    quality_gate,
    reciprocal_rank_fusion,
)

logger = logging.getLogger("citec.search")

# Tokens that hurt plainto_tsquery (AND-all) more than they help recall.
_STOPWORDS = frozenset(
    {
        "관련",
        "대한",
        "있는",
        "하는",
        "등",
        "및",
        "의",
        "를",
        "을",
        "이",
        "가",
        "은",
        "는",
        "와",
        "과",
        "에서",
        "으로",
        "로",
        "좀",
        "해주세요",
        "알려줘",
        "보여줘",
        "검색",
        "찾아",
        "목록",
        "리스트",
        "체크리스트",
        "체크",
        "리스트",
        "checklist",
        "checklists",
        "항목",
        "진단",
        "점검항목",
        "점검",
    }
)

# Phrase / token synonyms (ops + PISA). Keys lowercased for lookup.
_TOKEN_SYNONYMS: dict[str, list[str]] = {
    "리눅스": ["Linux", "linux", "PISAOLNX"],
    "linux": ["Linux", "리눅스", "PISAOLNX"],
    "파일시스템": ["파일 시스템", "filesystem", "Filesystem", "FS"],
    "파일": ["파일"],
    "시스템": ["시스템"],
    "filesystem": ["파일 시스템", "파일시스템"],
    "fs": ["파일 시스템", "파일시스템", "filesystem"],
    "pisa": ["PISA", "PISAOLNX"],
    "체크리스트": ["점검", "진단"],
    "점검항목": ["점검", "PISA"],
}

_PHRASE_SYNONYMS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"파일\s*시스템", re.I), ["파일 시스템", "파일시스템", "filesystem"]),
    (re.compile(r"\bFS\b", re.I), ["파일 시스템", "파일시스템", "filesystem"]),
    (re.compile(r"\bfilesystem\b", re.I), ["파일 시스템", "파일시스템"]),
    (re.compile(r"Linux\s*FS", re.I), ["Linux", "파일 시스템"]),
    (re.compile(r"리눅스\s*파일", re.I), ["Linux", "파일 시스템"]),
    (
        re.compile(r"체크\s*리스트|점검\s*항목|진단\s*항목|check\s*list", re.I),
        [],  # intent only — do not require these tokens in FTS AND
    ),
]


def expand_query(q: str) -> str:
    """Legacy helper: original + space-joined expansions (not used as single AND query)."""
    variants = build_fts_variants(q)
    return " ".join(variants) if variants else (q or "").strip()


_CHECKLIST_INTENT = re.compile(
    r"체크\s*리스트|점검\s*항목|진단\s*항목|check\s*list|checkitem|pisa\b",
    re.I,
)


def is_checklist_intent(q: str) -> bool:
    return bool(_CHECKLIST_INTENT.search(q or ""))


def build_fts_variants(q: str) -> list[str]:
    """Build OR-friendly FTS variants.

    ``plainto_tsquery`` ANDs every token — long Korean questions like
    「리눅스 파일시스템 관련 체크리스트」 become 4-way AND and match nothing.
    We drop stopwords, expand synonyms, and search each phrase separately.
    """
    base = (q or "").strip()
    if not base:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        v = (v or "").strip()
        if not v:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        variants.append(v)

    # Phrase-level expansions first
    for pat, syns in _PHRASE_SYNONYMS:
        if pat.search(base):
            for s in syns:
                add(s)

    # Tokenize: split on whitespace and glued compounds we care about
    raw_tokens = [t for t in re.split(r"\s+", base) if t]
    content: list[str] = []
    for tok in raw_tokens:
        low = tok.lower()
        if low in _STOPWORDS or tok in _STOPWORDS:
            continue
        # Split glued 파일시스템 if present as whole token
        if tok in ("파일시스템", "Filesystem", "filesystem"):
            content.append("파일 시스템")
            add("파일 시스템")
            add("파일시스템")
            continue
        content.append(tok)
        # Avoid bare high-DF English tokens as primary variants (noise in tech_repo)
        if low in {"linux", "리눅스"}:
            continue
        add(tok)
        for syn in _TOKEN_SYNONYMS.get(low, []):
            if syn.lower() in {"linux", "리눅스"}:
                continue  # only via paired expansions below
            add(syn)
        # DB/file lexicon synonyms (GRO, Redis, hang, …)
        try:
            from app.lexicon.seed import load_lexicon_map

            for syn in load_lexicon_map().get(low, []):
                if syn.lower() in {"linux", "리눅스"}:
                    continue
                add(syn)
        except Exception:  # noqa: BLE001
            pass

    # Content-only AND (without 관련/체크리스트) — still useful when 2–3 strong terms
    if len(content) >= 2:
        add(" ".join(content[:3]))

    # Pair Linux/리눅스 + filesystem concepts (high precision for PISA Linux FS)
    has_linux = any(re.search(r"리눅스|linux", t, re.I) for t in raw_tokens)
    has_fs = any(
        re.search(r"파일\s*시스템|파일시스템|filesystem|\bfs\b", t, re.I)
        for t in raw_tokens
    ) or any("파일 시스템" in v or "파일시스템" in v for v in variants)

    if has_fs:
        add("파일 시스템")
        add("파일시스템")
    if has_linux and has_fs:
        add("파일 시스템")
        add("PISAOLNX")  # Linux OS checkitem family
    elif has_linux:
        add("Linux")
        add("PISAOLNX")
    if is_checklist_intent(base) and has_linux:
        add("PISAOLNX")

    # Keep original last as precision path (may return 0 — OK)
    add(base)
    return variants


def _variant_weight(v: str) -> float:
    """Down-weight ultra-common single tokens so they do not drown checkitems."""
    v = v.strip()
    if not v:
        return 0.0
    if re.search(r"PISA[A-Z]{2,}", v):
        return 1.4
    if "파일 시스템" in v or "파일시스템" in v:
        return 1.3
    if len(v.split()) >= 2:
        return 1.0
    if v.lower() in {"linux", "리눅스", "filesystem", "fs"}:
        return 0.25
    return 0.55


@dataclass
class SearchFilters:
    source_type: Optional[str] = None
    domain: Optional[str] = None
    environment: Optional[str] = None
    work_type: Optional[str] = None
    path_l2: Optional[str] = None
    status: str = "active"


@dataclass
class SearchRequest:
    q: str
    top_k: int = 10
    filters: SearchFilters = field(default_factory=SearchFilters)
    fts_limit: int = 40
    vec_limit: int = 40
    rrf_k: int = 60
    min_top_score: float = 0.012
    exact_boost: float = 0.15


@dataclass
class SearchHit:
    rank: int
    score: float
    document_id: str
    chunk_id: str
    title: str
    snippet: str
    source_type: str
    external_id: str
    evidence_grade: str
    domain: Optional[str]
    environment: Optional[str]
    work_type: Optional[str]
    path_l2: Optional[str]
    source_uri: Optional[str]
    fts_rank: Optional[int]
    vec_rank: Optional[int]


@dataclass
class SearchResponse:
    query: str
    exact_tokens: list[str]
    total: int
    gated: bool
    results: list[SearchHit]
    trust_retrieval: str  # strong | medium | weak | empty


def _apply_doc_filters(stmt: Select, filters: SearchFilters) -> Select:
    stmt = stmt.where(Document.status == (filters.status or "active"))
    if filters.source_type:
        stmt = stmt.where(Document.source_type == filters.source_type)
    if filters.domain:
        stmt = stmt.where(Document.domain == filters.domain)
    if filters.environment:
        stmt = stmt.where(Document.environment == filters.environment)
    if filters.work_type:
        stmt = stmt.where(Document.work_type == filters.work_type)
    if filters.path_l2:
        stmt = stmt.where(Document.path_l2 == filters.path_l2)
    return stmt


def fts_search(session: Session, req: SearchRequest) -> list[str]:
    """Return chunk ids ordered by FTS rank (best first).

    Runs synonym / stopword-stripped variants as separate OR branches
    (plainto_tsquery is AND of all tokens — must not use the full sentence alone).
    """
    base = (req.q or "").strip()
    if not base:
        return []
    variants = build_fts_variants(base)

    scored: dict[str, float] = {}
    for v in variants:
        w = _variant_weight(v)
        if w <= 0:
            continue
        tsq = func.plainto_tsquery("simple", v)
        rank_expr = func.ts_rank_cd(Chunk.tsv, tsq)
        stmt = (
            select(Chunk.id, rank_expr)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.is_active.is_(True))
            .where(Chunk.tsv.is_not(None))
            .where(Chunk.tsv.op("@@")(tsq))
        )
        stmt = _apply_doc_filters(stmt, req.filters)
        stmt = stmt.order_by(rank_expr.desc()).limit(req.fts_limit)
        for cid, rk in session.execute(stmt).all():
            contrib = w * float(rk or 0.0)
            # Keep best weighted contribution (avoid DF-sum flooding)
            prev = scored.get(cid, 0.0)
            if contrib > prev:
                scored[cid] = contrib
            else:
                # small multi-variant bonus
                scored[cid] = prev + 0.15 * contrib

    # Title / header ILIKE only for high-signal phrases (not bare Linux).
    like_terms = [
        t
        for t in variants
        if t != base
        and _variant_weight(t) >= 1.0
        and len(t) >= 2
        and t.lower() not in _STOPWORDS
    ][:6]
    for term in like_terms:
        stmt = (
            select(Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.is_active.is_(True))
            .where(
                (Document.title.ilike(f"%{term}%"))
                | (Chunk.header_context.ilike(f"%{term}%"))
                | (Document.external_id.ilike(f"%{term}%"))
            )
        )
        stmt = _apply_doc_filters(stmt, req.filters).limit(40)
        for i, cid in enumerate(session.scalars(stmt).all()):
            scored[cid] = scored.get(cid, 0.0) + 0.08 * (1.0 / (1 + i))

    ordered = sorted(scored.keys(), key=lambda c: scored[c], reverse=True)
    return ordered[: req.fts_limit]


def vector_search(
    session: Session,
    req: SearchRequest,
    query_vector: Optional[list[float]],
) -> list[str]:
    """Return chunk ids ordered by cosine distance (best first).

    When metadata filters are applied, enable pgvector HNSW iterative scan;
    otherwise filtered ANN can return **0 rows** even though matching vectors exist.
    """
    if not query_vector:
        return []

    # pgvector 0.8+: filtered HNSW without iterative_scan often yields empty sets.
    has_meta_filter = any(
        [
            req.filters.source_type,
            req.filters.domain,
            req.filters.environment,
            req.filters.work_type,
            req.filters.path_l2,
        ]
    )
    if has_meta_filter:
        try:
            session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        except Exception:  # noqa: BLE001
            logger.debug("hnsw.iterative_scan not available", exc_info=True)

    dist = Embedding.vector.cosine_distance(query_vector)
    stmt = (
        select(Chunk.id)
        .join(Embedding, Embedding.chunk_id == Chunk.id)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.is_active.is_(True))
    )
    stmt = _apply_doc_filters(stmt, req.filters)
    stmt = stmt.order_by(dist).limit(req.vec_limit)
    return list(session.scalars(stmt).all())


def _snippet(text: str, query: str, width: int = 220) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return ""
    tokens = [w for w in re.split(r"\s+", query) if len(w) >= 2][:5]
    low = t.lower()
    pos = 0
    for tok in tokens:
        i = low.find(tok.lower())
        if i >= 0:
            pos = max(0, i - 40)
            break
    snip = t[pos : pos + width]
    if pos > 0:
        snip = "…" + snip
    if pos + width < len(t):
        snip = snip + "…"
    return snip


def hybrid_search(
    session: Session,
    req: SearchRequest,
    *,
    query_vector: Optional[list[float]] = None,
) -> SearchResponse:
    exact = extract_exact_tokens(req.q)
    fts_ids = fts_search(session, req)
    vec_ids = vector_search(session, req, query_vector)

    # Exact-token pass: prefer indexed/meta fields only (avoid full-text ILIKE scan).
    if exact:
        for tok in exact:
            stmt = (
                select(Chunk.id)
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.is_active.is_(True))
                .where(
                    (Document.external_id == tok)
                    | (Document.external_id.ilike(f"%{tok}%"))
                    | (Document.title.ilike(f"%{tok}%"))
                    | (Chunk.header_context.ilike(f"%{tok}%"))
                )
            )
            stmt = _apply_doc_filters(stmt, req.filters).limit(20)
            for cid in session.scalars(stmt).all():
                if cid not in fts_ids:
                    fts_ids.insert(0, cid)

    # Checklist questions: trust FTS (PISA titles) more than raw vector noise.
    vec_w = 1.0 if vec_ids else 0.0
    fts_w = 1.0
    if is_checklist_intent(req.q):
        fts_w = 1.35
        vec_w = 0.55 if vec_ids else 0.0

    fused = reciprocal_rank_fusion(
        [fts_ids, vec_ids],
        k=req.rrf_k,
        weights=[fts_w, vec_w],
    )
    if not fused and fts_ids:
        fused = {cid: 1.0 / (req.rrf_k + i) for i, cid in enumerate(fts_ids, start=1)}

    # Load texts for boost + meta
    all_ids = list(fused.keys())
    meta_by_id: dict[str, dict] = {}
    text_by_id: dict[str, str] = {}
    if all_ids:
        rows = session.execute(
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.text,
                Chunk.header_context,
                Document.title,
                Document.source_type,
                Document.external_id,
                Document.evidence_grade,
                Document.domain,
                Document.environment,
                Document.work_type,
                Document.path_l2,
                Document.source_uri,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id.in_(all_ids))
        ).all()
        for r in rows:
            meta_by_id[r.id] = {
                "document_id": r.document_id,
                "title": r.title,
                "text": r.text,
                "header_context": r.header_context,
                "source_type": r.source_type,
                "external_id": r.external_id,
                "evidence_grade": r.evidence_grade,
                "domain": r.domain,
                "environment": r.environment,
                "work_type": r.work_type,
                "path_l2": r.path_l2,
                "source_uri": r.source_uri,
            }
            text_by_id[r.id] = f"{r.header_context}\n{r.title}\n{r.external_id}\n{r.text}"

    # Checklist intent: boost checkitem sources so PISA rows surface over generic wiki pages.
    if is_checklist_intent(req.q) and fused:
        for cid, sc in list(fused.items()):
            st = (meta_by_id.get(cid) or {}).get("source_type")
            if st == "checkitem":
                fused[cid] = sc + 0.02
            ext = str((meta_by_id.get(cid) or {}).get("external_id") or "")
            if ext.startswith("PISAOLNX") and re.search(r"리눅스|linux", req.q, re.I):
                fused[cid] = fused[cid] + 0.015

    before_boost = dict(fused)
    fused = apply_exact_boost(
        fused, id_to_text=text_by_id, exact_tokens=exact, boost=req.exact_boost
    )
    exact_boosts = {
        cid: fused[cid] - before_boost.get(cid, 0.0)
        for cid in fused
        if fused[cid] != before_boost.get(cid, 0.0)
    }

    ranked = merge_to_hits(
        fused,
        fts_order=fts_ids,
        vec_order=vec_ids,
        meta_by_id=meta_by_id,
        exact_boosts=exact_boosts,
    )
    gated_list = quality_gate(
        ranked, min_top_score=req.min_top_score, max_results=req.top_k
    )
    gated = len(ranked) > 0 and len(gated_list) == 0

    # Deduplicate by document_id keeping best chunk
    seen_docs: set[str] = set()
    results: list[SearchHit] = []
    for h in gated_list:
        doc_id = h.document_id
        if doc_id in seen_docs:
            continue
        seen_docs.add(doc_id)
        m = h.meta
        results.append(
            SearchHit(
                rank=len(results) + 1,
                score=round(h.score, 6),
                document_id=doc_id,
                chunk_id=h.chunk_id,
                title=str(m.get("title") or ""),
                snippet=_snippet(str(m.get("text") or ""), req.q),
                source_type=str(m.get("source_type") or ""),
                external_id=str(m.get("external_id") or ""),
                evidence_grade=str(m.get("evidence_grade") or ""),
                domain=m.get("domain"),
                environment=m.get("environment"),
                work_type=m.get("work_type"),
                path_l2=m.get("path_l2"),
                source_uri=m.get("source_uri"),
                fts_rank=h.fts_rank,
                vec_rank=h.vec_rank,
            )
        )
        if len(results) >= req.top_k:
            break

    if not results:
        trust = "empty"
    elif results[0].score >= 0.05 or results[0].fts_rank == 1:
        trust = "strong"
    elif results[0].score >= 0.02:
        trust = "medium"
    else:
        trust = "weak"

    return SearchResponse(
        query=req.q,
        exact_tokens=exact,
        total=len(results),
        gated=gated,
        results=results,
        trust_retrieval=trust,
    )
