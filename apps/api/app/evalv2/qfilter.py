"""Pure query hygiene helpers for evalset v2 (no DB, no network).

Known, accepted limitations:
- extract_q cuts a query at the first " <key>=" of the other-keys list, so a query that itself
  contains e.g. " path=" loses its tail.
- _ID_ONLY also excludes things like ORA-12154 / ERP2024 (identifier lookups are a separate
  gate group).
- sensitive_hints are advisory only: human review of the sheet is mandatory; personal names
  are undetectable by regex.
- _tok_match's prefix rule over-flags short Hangul tokens (서버/서버실, 로그/로그인), which makes
  too_easy conservative.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from typing import Any

MAX_SUMMARY_LEN = 500  # app.audit.mcp_access._MAX_SUMMARY_LEN: longer queries are cut off
MIN_QUERY_LEN = 5
MAX_QUERY_LEN = 300

_OTHER_KEYS = ("query", "anchor_value", "external_id", "bucket_id", "path", "slug", "code", "symptom_text")
_OTHER_KEY_RE = re.compile(r"\s(?:%s)=" % "|".join(_OTHER_KEYS))
_ID_ONLY = re.compile(r"^(?:[A-Za-z]+-\d+|[A-Z]{3,}[A-Z0-9_.]*\d[\w.]*|\d{5,})$")
_HINTS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "ip": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    "phone": re.compile(r"\b0\d{1,2}-?\d{3,4}-?\d{4}\b"),
    "phone_sep": re.compile(r"\b0\d{1,2}[ .]\d{3,4}[ .]\d{4}\b"),
    "phone_intl": re.compile(r"\+82[- ]?\d{1,2}[- ]?\d{3,4}[- ]?\d{4}"),
    "phone_rep": re.compile(r"\b1[5-9]\d{2}-\d{4}\b"),
    "rrn": re.compile(r"\b\d{6}-[1-4]\d{6}\b"),
    "url": re.compile(r"https?://"),
    "long_digits": re.compile(r"\d{8,}"),
}
_TOK = re.compile(r"[0-9A-Za-z가-힣]+")
_HANGUL = re.compile(r"[가-힣]")


def norm(q: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", q or "").split()).lower()


def extract_q(summary: str) -> tuple[str, bool]:
    """('query', truncated) from an mcp_access_log.query_summary like 'q=<text>[ key=value…]'."""
    s = (summary or "").strip()
    if not s.startswith("q="):
        return "", False
    truncated = len(s) >= MAX_SUMMARY_LEN
    s = s[2:]
    m = _OTHER_KEY_RE.search(s)
    if m:
        s = s[: m.start()]
    return s.strip(), truncated


def parse_log_tsv(text: str) -> list[dict[str, Any]]:
    """Rows of `date TAB tool TAB count TAB query_summary` (psql \\copy … csv, tab delimiter)."""
    out: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        if len(row) < 4:
            continue
        d, tool, n = row[0], row[1], row[2]
        q, truncated = extract_q("\t".join(row[3:]))
        if not q:
            continue
        try:
            cnt = int(n)
        except ValueError:
            cnt = 1
        out.append({"date": d, "tool": tool, "n": cnt, "q": q, "truncated": truncated})
    return out


def exclusion_reason(q: str, truncated: bool, known: set[str]) -> str | None:
    if truncated:
        return "truncated"
    if len(q) < MIN_QUERY_LEN:
        return "too_short"
    if len(q) > MAX_QUERY_LEN:
        return "too_long"
    if _ID_ONLY.match(q.strip()):
        return "identifier_only"
    if norm(q) in known:
        return "known_case"
    return None


def sensitive_hints(q: str) -> list[str]:
    found: list[str] = []
    for name, pat in _HINTS.items():
        if pat.search(q or ""):
            name = "phone" if name.startswith("phone") else name
            if name not in found:
                found.append(name)
    return found


def prepare_real(rows: list[dict[str, Any]], known: set[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats: dict[str, int] = {"truncated": 0, "too_short": 0, "too_long": 0, "identifier_only": 0, "known_case": 0}
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        why = exclusion_reason(r["q"], r["truncated"], known)
        if why:
            stats[why] += 1
            continue
        key = norm(r["q"])
        if key in merged:
            merged[key]["count"] += int(r["n"])
        else:
            merged[key] = {"q": " ".join(r["q"].split()), "count": int(r["n"])}
    ordered = sorted(merged.values(), key=lambda x: (-x["count"], x["q"]))
    items = [
        {"id": f"r-{i:03d}", "q": m["q"], "slice": "real", "source": "real",
         "count": m["count"], "hints": sensitive_hints(m["q"])}
        for i, m in enumerate(ordered, 1)
    ]
    stats["kept"] = len(items)
    return items, stats


def tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOK.findall(unicodedata.normalize("NFKC", s or "")) if len(t) >= 2}


def _tok_match(a: str, b: str) -> bool:
    if a == b:
        return True
    return bool(_HANGUL.search(a + b)) and min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a))


def title_overlap(query: str, title: str) -> float:
    """Share of query tokens that also occur (Korean-particle tolerant) in the title."""
    qt, tt = tokens(query), tokens(title)
    if not qt:
        return 0.0
    return sum(1 for a in qt if any(_tok_match(a, b) for b in tt)) / len(qt)


def too_easy(query: str, title: str, *, max_ratio: float = 0.5) -> bool:
    return title_overlap(query, title) > max_ratio


def load_known_questions(holdout_path: str | None, evalset_paths: list[str]) -> set[str]:
    known: set[str] = set()
    if holdout_path:
        for it in json.load(open(holdout_path, encoding="utf-8"))["items"]:
            known.add(norm(it["question"]))
    for p in evalset_paths:
        for c in json.load(open(p, encoding="utf-8"))["cases"]:
            if c.get("q"):
                known.add(norm(c["q"]))
    return known
