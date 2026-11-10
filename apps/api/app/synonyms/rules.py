"""Pure rules for synonym candidates (no DB, no LLM)."""

from __future__ import annotations

import re

from app.retrieval.search_synonyms import SYNONYM_GROUPS

MAX_GROUP = 6
MIN_VERIFIED_TICKETS = 2
MAX_TERM_LEN = 64

_HANGUL = re.compile(r"[가-힣]")
_ALLOWED = re.compile(r"^[0-9A-Za-z가-힣][0-9A-Za-z가-힣 ._\-/+]*$")


def norm_term(term: str) -> str:
    return " ".join((term or "").split())


def is_valid_term(term: str) -> bool:
    t = norm_term(term)
    if not t or len(t) > MAX_TERM_LEN or not _ALLOWED.match(t):
        return False
    hangul = len(_HANGUL.findall(t))
    if hangul:
        return hangul >= 3  # no 1–2 character Korean (행→hang false-positive history)
    return len(t) >= 3


def clean_terms(terms: list[str]) -> list[str]:
    """Normalise, drop invalid, dedupe case-insensitively, cap at MAX_GROUP."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms or []:
        t = norm_term(str(raw))
        key = t.lower()
        if key in seen or not is_valid_term(t):
            continue
        seen.add(key)
        out.append(t)
        if len(out) == MAX_GROUP:
            break
    return out


def terms_key(terms: list[str]) -> str:
    return "|".join(sorted({n for t in terms if (n := norm_term(t).lower())}))


def term_present(text: str, term: str) -> bool:
    """Is ``term`` in ``text``? Evidence check, not the search matcher.

    Korean particles glue onto words ("OOM이", "메모리 부족으로"), so the search
    matcher's Hangul word boundary would wrongly reject real evidence. Terms with
    Hangul are matched as plain case-insensitive substrings; pure ASCII terms
    must not be embedded in a longer ASCII/digit run.
    """
    t = norm_term(term)
    if not t:
        return False
    if _HANGUL.search(t):
        return t.lower() in " ".join((text or "").split()).lower()
    pat = r"(?<![0-9A-Za-z])" + r"\s+".join(re.escape(p) for p in t.split()) + r"(?![0-9A-Za-z])"
    return re.search(pat, text or "", re.I) is not None


def _fold(term: str) -> str:
    """Casefolded term without spaces/punctuation: '접속 불가' == '접속불가', 'SCP V2' == 'SCPv2', 'N/W' == 'nw'."""
    return re.sub(r"[^0-9a-z가-힣]", "", norm_term(term).lower())


def is_variant_group(terms: list[str]) -> bool:
    """True when the terms are surface variants of one word, not different concepts.

    Spacing/case/punctuation variants (all terms fold to the same string) or Korean<->Latin
    pairs (reboot/재부팅). Such a pair is a safe synonym however common the word is, so the
    mining DF ceiling does not apply to it.
    """
    ts = [t for t in terms if norm_term(t)]
    if len(ts) < 2:
        return False
    if len({_fold(t) for t in ts}) == 1:
        return True
    has_hangul = any(_HANGUL.search(t) for t in ts)
    has_latin = any(not _HANGUL.search(t) for t in ts)
    return has_hangul and has_latin


def static_covers(terms: list[str]) -> bool:
    """True if every term already sits in one code-defined group."""
    want = {n for t in terms if (n := norm_term(t).lower())}
    return bool(want) and any(want <= {norm_term(x).lower() for x in g} for g in SYNONYM_GROUPS)


def verify_group(
    terms: list[str],
    cited_ids: list[str],
    texts: dict[str, str],
    *,
    min_terms_per_ticket: int = 2,
) -> tuple[list[str], list[str]] | None:
    """Check LLM-cited tickets against the real ticket text.

    ``texts`` holds only the tickets of the batch that was sent to the LLM, so
    ids it invented are ignored. A ticket is *verified* when at least
    ``min_terms_per_ticket`` group terms occur in it (word-bounded). The group
    is kept only with >= MIN_VERIFIED_TICKETS verified tickets; terms that
    never occur in a verified ticket are dropped, and >= 2 terms must remain.
    Returns ``(kept_terms, verified_ticket_ids)`` or None.
    """
    terms = clean_terms(terms)
    verified: list[str] = []
    seen_terms: set[str] = set()
    for tid in dict.fromkeys(cited_ids or []):
        text = texts.get(tid)
        if not text:
            continue
        present = {t for t in terms if term_present(text, t)}
        if len(present) >= max(1, min_terms_per_ticket):
            verified.append(tid)
            seen_terms |= present
    if len(verified) < MIN_VERIFIED_TICKETS:
        return None
    kept = [t for t in terms if t in seen_terms]
    if len(kept) < 2:
        return None
    return kept, verified
