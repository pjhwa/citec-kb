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
        # existence / interrogative shells (dilute AND FTS)
        "있는가",
        "있나요",
        "있습니까",
        "있나",
        "있는지",
        "유무",
        "여부",
        "존재",
        "알려줘",
        "뭐야",
        "무엇",
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
    # SCP topology / availability phrasing (hyphen vs space variants)
    (
        re.compile(r"Multi[\s\-]?AZ|멀티\s*AZ|\bMAZ\b", re.I),
        ["Multi-AZ", "Multi AZ", "멀티AZ"],
    ),
    (
        re.compile(r"SCP\s*v?\s*2|SCPv2|SCP\s*V2", re.I),
        ["SCP v2", "SCPv2", "SCP V2"],
    ),
    (
        re.compile(r"가용성\s*테스트|가용성\s*점검", re.I),
        ["가용성 테스트", "가용성 점검"],
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


def _contains_pattern(term: str) -> str:
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def content_tokens(q: str) -> list[str]:
    """Query tokens worth matching in a title. Drops stopwords and 1-char noise."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"\s+", (q or "").strip()):
        if len(raw) < 2 or raw.lower() in _STOPWORDS or raw in _STOPWORDS:
            continue
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


def lexical_supported(
    *,
    fts_rank: Optional[int],
    title: str,
    external_id: str,
    snippet: str,
    tokens: list[str],
) -> bool:
    """True when the hit is backed by FTS or actually contains a query token.

    A pure ANN neighbor of a nonsense string has neither, and must not be
    returned as a result (precision over coverage).
    """
    if fts_rank is not None:
        return True
    if not tokens:
        return True
    blob = f"{title or ''}\n{external_id or ''}\n{snippet or ''}".lower()
    return any(tok.lower() in blob for tok in tokens)


def retrieval_trust(results: list[SearchHit]) -> str:
    if not results:
        return "empty"
    top = results[0]
    if top.score >= 0.05 or top.fts_rank == 1:
        return "strong"
    if top.score >= 0.02:
        return "medium"
    return "weak"


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
    # Equality filters stay singular. Exclusions are separate lists so a
    # caller can drop known answer pages or a source without changing
    # source_type's type (that change was reverted once already).
    exclude_page_ids: Optional[list[str]] = None
    exclude_source_types: Optional[list[str]] = None
    # False drops confluence_map rows tagged tech_relevant=irrelevant.
    # Explicit source_type=confluence_map sets this True.
    include_irrelevant_maps: bool = False


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
    # evidence_eligible=False marks a hit as a pointer, not verified
    # evidence — currently true only for evidence_grade="C" confluence_map
    # rows. Callers (incl. the AI answer path) must read the current
    # Confluence page before treating a False-flagged hit as an answer
    # basis, per the handoff design's evidence_eligible/requires_source_read
    # contract (§7 of the 2026-09-16 design doc).
    evidence_eligible: bool = True
    # Document.updated_at for confluence_map hits — an approximation of
    # "last confirmed synced", not "last confirmed still live": it only
    # advances when map_sync's crawl actually rewrites this page's
    # frontmatter (title/breadcrumb/version changed), not on every crawl
    # that merely re-touches an unchanged page. None for non-confluence_map
    # hits and for confluence_map hits never re-touched since ingest.
    map_synced_at: Optional[str] = None


@dataclass
class SearchResponse:
    query: str
    exact_tokens: list[str]
    total: int  # deprecated alias of returned_count (this page, not the corpus)
    gated: bool
    results: list[SearchHit]
    trust_retrieval: str  # strong | medium | weak | empty
    returned_count: int = 0
    total_candidates: int = 0


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
    if filters.exclude_page_ids:
        stmt = stmt.where(Document.external_id.notin_(list(filters.exclude_page_ids)))
    if filters.exclude_source_types:
        stmt = stmt.where(Document.source_type.notin_(list(filters.exclude_source_types)))
    if not filters.include_irrelevant_maps:
        tech = Document.metadata_["tech_relevant"].astext
        stmt = stmt.where(
            (Document.source_type != "confluence_map")
            | tech.is_(None)
            | (tech != "irrelevant")
        )
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

    # pgvector 0.8+: HNSW post-filtering (is_active, and any metadata filter)
    # returns the unfiltered neighbors and then drops them, so a corpus with
    # many inactive chunk vectors comes back empty. iterative_scan keeps
    # scanning until the filter has enough survivors.
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
    if (req.filters.source_type or "") == "confluence_map":
        req.filters.include_irrelevant_maps = True
    exact = extract_exact_tokens(req.q)
    fts_ids = fts_search(session, req)
    vec_ids = vector_search(session, req, query_vector)
    tokens = content_tokens(req.q)

    # Title coverage: a document whose title contains every content token
    # must enter the pool even when FTS rank is buried by noisy variants
    # (verified: "Exadata 노드 Down" matched CITECTS-2637 in tsv but not top 10).
    if len(tokens) >= 2:
        stmt = (
            select(Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.is_active.is_(True))
        )
        for tok in tokens:
            stmt = stmt.where(Document.title.ilike(_contains_pattern(tok), escape="\\"))
        stmt = _apply_doc_filters(stmt, req.filters).limit(20)
        for cid in session.scalars(stmt).all():
            if cid not in fts_ids:
                fts_ids.insert(0, cid)

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
                Document.updated_at,
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
                "updated_at": r.updated_at,
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

    if len(tokens) >= 2 and fused and meta_by_id:
        for cid in list(fused):
            title = str((meta_by_id.get(cid) or {}).get("title") or "").lower()
            if title and all(tok.lower() in title for tok in tokens):
                fused[cid] = fused[cid] + 0.25

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
    # Keep extra chunks so document-level dedupe can still fill top_k.
    gated_list = quality_gate(
        ranked, min_top_score=req.min_top_score, max_results=max(req.top_k * 5, req.top_k)
    )
    gated = len(ranked) > 0 and len(gated_list) == 0

    # Deduplicate by document_id before the page cut. Cutting first dropped
    # unique documents that sat just outside a chunk-duplicated top_k.
    seen_docs: set[str] = set()
    unique_hits: list[RankedHit] = []
    for h in gated_list:
        if h.document_id in seen_docs:
            continue
        seen_docs.add(h.document_id)
        unique_hits.append(h)
    total_candidates = len(unique_hits)
    results: list[SearchHit] = []
    for h in unique_hits[: req.top_k]:
        m = h.meta
        results.append(
            SearchHit(
                rank=len(results) + 1,
                score=round(h.score, 6),
                document_id=h.document_id,
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
                evidence_eligible=str(m.get("source_type") or "") != "confluence_map",
                map_synced_at=(
                    m["updated_at"].isoformat()
                    if m.get("source_type") == "confluence_map" and m.get("updated_at")
                    else None
                ),
            )
        )
    if results and not any(
        lexical_supported(
            fts_rank=h.fts_rank,
            title=h.title,
            external_id=h.external_id,
            snippet=h.snippet,
            tokens=tokens,
        )
        for h in results
    ):
        results = []
        total_candidates = 0
        gated = True

    trust = retrieval_trust(results)
    returned = len(results)

    return SearchResponse(
        query=req.q,
        exact_tokens=exact,
        total=returned,
        gated=gated,
        results=results,
        trust_retrieval=trust,
        returned_count=returned,
        total_candidates=total_candidates,
    )
