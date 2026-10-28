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


def _compile(term: str) -> re.Pattern[str]:
    esc = r"\s+".join(re.escape(p) for p in term.split())
    return re.compile(rf"(?<![0-9A-Za-z가-힣]){esc}(?![0-9A-Za-z가-힣])", re.I)


_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (_compile(t), gi) for gi, g in enumerate(SYNONYM_GROUPS) for t in g
]


def search_synonyms(q: str) -> list[str]:
    """Other members of every group that has a member in ``q`` (query order)."""
    hit: list[int] = []
    for pat, gi in _PATTERNS:
        if gi not in hit and pat.search(q or ""):
            hit.append(gi)
    out: list[str] = []
    for gi in hit:
        for t in SYNONYM_GROUPS[gi]:
            if not _PATTERNS_BY_TERM[t].search(q) and t not in out:
                out.append(t)
    return out


_PATTERNS_BY_TERM = {t: _compile(t) for g in SYNONYM_GROUPS for t in g}
