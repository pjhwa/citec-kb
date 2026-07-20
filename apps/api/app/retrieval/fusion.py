"""Ranking fusion, exact boost, and quality gate — pure functions for unit tests."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence


# Issue keys, page IDs, PISA codes, sysctl-like params
_EXACT_PATTERNS = [
    re.compile(r"\bCITECTS-\d+\b", re.I),
    re.compile(r"\bISS-\d{8}-\d+\b", re.I),
    re.compile(r"\bPISA[A-Z0-9_]+_\d+(?:\.\d+)+\b", re.I),
    re.compile(r"\bvm\.[a-z0-9_.]+\b", re.I),
    re.compile(r"\brx-gro-hw\b", re.I),
    re.compile(r"\bconfluence_\d+\b", re.I),
    re.compile(r"\b\d{6,}\b"),  # long numeric page ids
]


@dataclass
class RankedHit:
    chunk_id: str
    document_id: str
    score: float
    fts_rank: Optional[int] = None
    vec_rank: Optional[int] = None
    exact_boost: float = 0.0
    meta: dict = field(default_factory=dict)


def extract_exact_tokens(query: str) -> list[str]:
    found: list[str] = []
    for pat in _EXACT_PATTERNS:
        for m in pat.finditer(query or ""):
            tok = m.group(0)
            if tok not in found:
                found.append(tok)
    return found


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = 60,
    weights: Optional[Sequence[float]] = None,
) -> dict[str, float]:
    """RRF over lists of ids (best rank first). Returns id -> fused score."""
    scores: dict[str, float] = {}
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    for w, lst in zip(weights, ranked_lists):
        for rank, item_id in enumerate(lst, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + w * (1.0 / (k + rank))
    return scores


def apply_exact_boost(
    scores: dict[str, float],
    *,
    id_to_text: dict[str, str],
    exact_tokens: Sequence[str],
    boost: float = 0.15,
) -> dict[str, float]:
    """Add boost when chunk/document text contains exact technical tokens."""
    if not exact_tokens:
        return dict(scores)
    out = dict(scores)
    for cid, base in scores.items():
        blob = id_to_text.get(cid, "")
        hit = 0
        for tok in exact_tokens:
            if tok.lower() in blob.lower():
                hit += 1
        if hit:
            out[cid] = base + boost * hit
    return out


def quality_gate(
    hits: Sequence[RankedHit],
    *,
    min_score: float = 0.0,
    min_top_score: float = 0.012,
    max_results: int = 20,
) -> list[RankedHit]:
    """Drop weak lists: if top score below threshold, return empty."""
    if not hits:
        return []
    ordered = sorted(hits, key=lambda h: h.score, reverse=True)
    if ordered[0].score < min_top_score:
        return []
    filtered = [h for h in ordered if h.score >= min_score]
    return filtered[:max_results]


def merge_to_hits(
    fused: dict[str, float],
    *,
    fts_order: Sequence[str],
    vec_order: Sequence[str],
    meta_by_id: dict[str, dict],
    exact_boosts: Optional[dict[str, float]] = None,
) -> list[RankedHit]:
    fts_rank = {cid: i + 1 for i, cid in enumerate(fts_order)}
    vec_rank = {cid: i + 1 for i, cid in enumerate(vec_order)}
    exact_boosts = exact_boosts or {}
    hits: list[RankedHit] = []
    for cid, score in fused.items():
        meta = meta_by_id.get(cid, {})
        hits.append(
            RankedHit(
                chunk_id=cid,
                document_id=str(meta.get("document_id") or ""),
                score=score,
                fts_rank=fts_rank.get(cid),
                vec_rank=vec_rank.get(cid),
                exact_boost=exact_boosts.get(cid, 0.0),
                meta=meta,
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits
