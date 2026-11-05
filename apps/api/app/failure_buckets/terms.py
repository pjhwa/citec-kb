"""Curated Korean/English term groups for failure-bucket matching, plus the canonicalizer.

Scope is deliberately narrow (insight-ops request 2026-11-05): ONLY listed terms are treated as the
same word (쿼럼 = quorum). No semantic similarity — a pair is added here only when a human
agrees it is the same word. A group is bidirectional; every member maps to one canonical token.
Signals are still judged one by one (see match.py); this only decides what counts as the same word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# First ASCII member = canonical token (lower-cased, alnum only). 1–2 character Korean terms are
# excluded on purpose (false-positive history, see retrieval/search_synonyms.py).
FB_TERM_GROUPS: list[list[str]] = [
    ["quorum", "쿼럼", "정족수"],
    ["fencing", "펜싱", "STONITH"],
    ["split-brain", "스플릿 브레인", "스플릿브레인"],
    ["failover", "페일오버", "장애조치"],
    ["failback", "페일백"],
    ["link flapping", "링크 플래핑", "링크 플랩"],
    ["link down", "링크 다운"],
    ["heartbeat", "하트비트"],
    ["timeout", "타임아웃"],
    ["reboot", "재부팅"],
    ["deadlock", "데드락"],
    ["split brain resolver", "SBR"],
    ["bonding", "본딩"],
    ["multipath", "멀티패스"],
    ["no-quorum-policy", "no quorum policy"],
    ["failure domain", "장애 도메인"],
    ["retransmission", "재전송"],
    ["connection reset", "커넥션 리셋", "연결 리셋"],
]

_PARTICLES = "으로부터|에서의|으로|에서|까지|부터|이다|이|가|은|는|을|를|의|에|로|와|과|도|만"


@dataclass
class Normalized:
    text: str
    # canonical token -> surface forms found in the input (in order, de-duplicated)
    hits: dict[str, list[str]]


def _canon(term: str) -> str:
    return re.sub(r"[^0-9a-z]", "", term.lower())


def _term_pattern(term: str) -> str:
    words = [re.escape(w) for w in re.split(r"[\s\-_]+", term.strip()) if w]
    body = r"[\s\-_]*".join(words) if len(words) > 1 else words[0]
    return rf"(?<![0-9A-Za-z가-힣]){body}(?:{_PARTICLES})?(?![0-9A-Za-z가-힣])"


class Canonicalizer:
    def __init__(self, groups: Iterable[Iterable[str]]):
        entries: list[tuple[str, str]] = []  # (term, canon)
        for g in groups:
            members = [t.strip() for t in g if t and t.strip()]
            ascii_first = next((m for m in members if re.search(r"[A-Za-z]", m) and not re.search(r"[가-힣]", m)), None)
            canon = _canon(ascii_first) if ascii_first else ""
            if len(members) < 2 or len(canon) < 2:
                continue
            entries += [(m, canon) for m in members]
        entries.sort(key=lambda e: -len(e[0]))
        self._canon_of: dict[str, str] = {}
        parts = []
        for i, (term, canon) in enumerate(entries):
            self._canon_of[f"t{i}"] = canon
            parts.append(f"(?P<t{i}>{_term_pattern(term)})")
        self._re = re.compile("|".join(parts), re.I) if parts else None
        self.size = len(entries)

    def normalize(self, text: str) -> Normalized:
        hits: dict[str, list[str]] = {}
        if not self._re or not text:
            return Normalized(text or "", hits)

        def repl(m: re.Match) -> str:
            canon = self._canon_of[m.lastgroup]
            surface = m.group(0)
            lst = hits.setdefault(canon, [])
            if surface not in lst:
                lst.append(surface)
            return f" {canon} "

        return Normalized(self._re.sub(repl, text), hits)


_cache: tuple[int, Canonicalizer] | None = None


def get_canonicalizer() -> Canonicalizer:
    """Code groups + curated search synonyms + human-approved runtime groups (best effort)."""
    global _cache
    from app.retrieval.search_synonyms import SYNONYM_GROUPS

    runtime: list[list[str]] = []
    try:
        from app.synonyms.runtime import runtime_groups

        runtime = runtime_groups()
    except Exception:  # noqa: BLE001 — matching must work without the DB
        pass
    key = hash((len(FB_TERM_GROUPS), len(SYNONYM_GROUPS), tuple(tuple(g) for g in runtime)))
    if _cache is None or _cache[0] != key:
        _cache = (key, Canonicalizer(FB_TERM_GROUPS + SYNONYM_GROUPS + runtime))
    return _cache[1]
