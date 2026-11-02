"""Search-only synonym groups (runtime, no graph/embedding impact).

Kept separate from ``lexicon/`` on purpose: lexicon_version feeds the graph
batch fingerprint, so editing it forces a full graph re-backfill. These groups
only widen FTS variants. Every term was checked against the corpus FTS before
being added; no 1–2 character Korean terms (false-positive history: 행→hang), so 펜싱/넷앱 pairs are deliberately absent.

A group is bidirectional: any member in the query adds the other members.
Multi-word members match as phrases.
"""
from __future__ import annotations

import re

SYNONYM_GROUPS: list[list[str]] = [
    ["OOM", "Out of Memory", "메모리 부족", "oom-killer"],
    ["kernel panic", "커널 패닉"],
    ["failover", "페일오버"],
    ["deadlock", "데드락"],
    ["memory leak", "메모리 누수"],
    ["k8s", "Kubernetes", "쿠버네티스"],
]


def compile_term(term: str) -> re.Pattern[str]:
    esc = r"\s+".join(re.escape(p) for p in term.split())
    return re.compile(rf"(?<![0-9A-Za-z가-힣]){esc}(?![0-9A-Za-z가-힣])", re.I)


_compile = compile_term


_PAT_CACHE: dict[str, re.Pattern[str]] = {}


def _pat(term: str) -> re.Pattern[str]:
    p = _PAT_CACHE.get(term)
    if p is None:
        if len(_PAT_CACHE) > 4096:
            _PAT_CACHE.clear()
        p = _PAT_CACHE[term] = compile_term(term)
    return p


def _all_groups() -> list[list[str]]:
    from app.synonyms.runtime import runtime_groups

    return SYNONYM_GROUPS + runtime_groups()


def search_synonyms(q: str) -> list[str]:
    """Other members of every group (code + approved DB) that has a member in ``q``."""
    text = q or ""
    groups = _all_groups()
    hit = [gi for gi, g in enumerate(groups) if any(_pat(t).search(text) for t in g)]
    out: list[str] = []
    for gi in hit:
        for t in groups[gi]:
            if not _pat(t).search(text) and t not in out:
                out.append(t)
    return out
