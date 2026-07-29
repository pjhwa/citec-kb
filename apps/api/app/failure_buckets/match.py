"""Pure signal-matching scorer for failure buckets (no DB)."""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]{2,}")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _signal_hit(observed_tokens: set[str], signal: str) -> bool:
    """A stored signal is 'hit' when at least half its tokens appear in the
    observed token set (order-insensitive; handles partial phrasing)."""
    sig_tokens = _tokens(signal)
    if not sig_tokens:
        return False
    hits = sum(1 for t in sig_tokens if t in observed_tokens)
    return hits >= max(1, (len(sig_tokens) + 1) // 2)


def match_bucket(
    observed_signals: list[str],
    symptom: str,
    bucket: dict[str, Any],
) -> dict[str, Any]:
    """Score a single bucket against observed signals + symptom text."""
    observed_tokens: set[str] = set()
    for s in observed_signals or []:
        observed_tokens |= _tokens(s)
    observed_tokens |= _tokens(symptom)

    discriminating = list(bucket.get("discriminating_signals") or [])
    counter = list(bucket.get("counter_signals") or [])

    matched = [s for s in discriminating if _signal_hit(observed_tokens, s)]
    contradicted = [s for s in counter if _signal_hit(observed_tokens, s)]

    total = len(discriminating) or 1
    signal_ratio = len(matched) / total
    score = 0.6 * signal_ratio + 0.4 * float(bucket.get("confidence") or 0.5)
    if contradicted:
        score -= 0.5 * len(contradicted)
    score = max(0.0, min(1.0, score))

    if contradicted:
        label = "비권고"
    elif score >= 0.7 and matched:
        label = "가능"
    elif score >= 0.4:
        label = "조건부"
    else:
        label = "비권고"

    return {
        "bucket_id": bucket.get("id"),
        "bucket_name": bucket.get("bucket_name"),
        "matched_signals": matched,
        "contradicted": contradicted,
        "confidence": round(score, 3),
        "label": label,
    }


def rank_buckets(
    observed_signals: list[str],
    symptom: str,
    buckets: list[dict[str, Any]],
    *,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    scored = [match_bucket(observed_signals, symptom, b) for b in buckets]
    scored.sort(key=lambda r: r["confidence"], reverse=True)
    return scored[:top_k]
