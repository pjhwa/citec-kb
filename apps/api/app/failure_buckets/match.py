"""Pure signal-matching scorer for failure buckets (no DB).

Judgement is per discriminating signal. Only listed ko/en term groups (terms.py) count as the
same word — no semantic similarity. Each result says which words overlapped (match_evidence)
and why the label came out as it did (label_basis).
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from app.failure_buckets.terms import Canonicalizer, get_canonicalizer

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]{2,}")

# Caps signal_ratio's denominator so a well-refined bucket (many
# discriminating_signals accumulated via refine_bucket) doesn't get penalized
# for partial matches relative to a sparse, newer bucket. See design doc §3.
_SIGNAL_RATIO_CAP = 4

W_SIGNAL, W_CONF, W_CONTRA = 0.6, 0.4, 0.5
T_POSSIBLE, T_CONDITIONAL = 0.7, 0.4

LABEL_RULES = {
    "score": f"{W_SIGNAL}*signal_ratio + {W_CONF}*bucket_confidence - {W_CONTRA}*(반증 신호 수), 0~1로 자름",
    "signal_ratio": f"일치한 판별 신호 수 / min(판별 신호 수, {_SIGNAL_RATIO_CAP})  (분자도 {_SIGNAL_RATIO_CAP}로 자름)",
    "signal_hit": "저장된 신호의 단어 중 절반 이상(올림)이 관찰 단어에 있으면 그 신호가 일치(어순 무관). 같은 말로 보는 것은 등록된 한/영 용어 그룹뿐이다.",
    "labels": {
        "가능": f"score >= {T_POSSIBLE} 이고 일치 신호 1개 이상, 반증 없음",
        "조건부": f"score >= {T_CONDITIONAL}, 반증 없음",
        "비권고": f"score < {T_CONDITIONAL} 이거나 반증 신호가 1개라도 일치",
    },
    "note": "버킷 confidence가 기본값 0.5(미검증)이면 '가능'은 계산 대상 신호가 모두 일치해야 하고, 1개만 일치하면 비권고다. 이는 의도된 보수성이며 버킷이 refine으로 검증돼 confidence가 오르면 같은 일치 수로도 라벨이 올라간다.",
}


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


def _needed_matches(threshold: float, conf: float, denom: int) -> Optional[int]:
    """Smallest matched count reaching `threshold` (no contradiction), None if unreachable."""
    need_ratio = (threshold - W_CONF * conf) / W_SIGNAL
    n = max(1, math.ceil(need_ratio * denom - 1e-9))
    return n if n <= denom else None


def _evidence(signal: str, obs_tokens: set[str], obs_hits: dict[str, list[str]],
              canon: Canonicalizer) -> dict[str, Any]:
    sig = canon.normalize(signal)
    overlap = []
    for t in sorted(_tokens(sig.text) & obs_tokens):
        sig_forms = sig.hits.get(t) or [t]
        obs_forms = obs_hits.get(t) or [t]
        item: dict[str, Any] = {"token": t, "observed_as": obs_forms, "signal_as": sig_forms}
        if {f.lower() for f in obs_forms} != {f.lower() for f in sig_forms}:
            item["via_term_group"] = True  # 서로 다른 표기를 용어 그룹으로 같은 말로 본 경우
        overlap.append(item)
    return {"signal": signal, "overlap": overlap}


def match_bucket(
    observed_signals: list[str],
    symptom: str,
    bucket: dict[str, Any],
    *,
    canon: Optional[Canonicalizer] = None,
) -> dict[str, Any]:
    """Score a single bucket against observed signals + symptom text."""
    canon = canon or get_canonicalizer()
    observed_tokens: set[str] = set()
    obs_hits: dict[str, list[str]] = {}
    for s in list(observed_signals or []) + [symptom or ""]:
        n = canon.normalize(s)
        observed_tokens |= _tokens(n.text)
        for k, forms in n.hits.items():
            lst = obs_hits.setdefault(k, [])
            lst += [f for f in forms if f not in lst]

    def hit(signal: str) -> bool:
        return _signal_hit(observed_tokens, canon.normalize(signal).text)

    discriminating = list(bucket.get("discriminating_signals") or [])
    counter = list(bucket.get("counter_signals") or [])

    matched = [s for s in discriminating if hit(s)]
    contradicted = [s for s in counter if hit(s)]
    unmatched = [s for s in discriminating if s not in matched]

    total = len(discriminating) or 1
    denom = min(total, _SIGNAL_RATIO_CAP)
    signal_ratio = min(len(matched), _SIGNAL_RATIO_CAP) / denom
    conf = float(bucket.get("confidence") or 0.5)
    score = W_SIGNAL * signal_ratio + W_CONF * conf
    if contradicted:
        score -= W_CONTRA * len(contradicted)
    score = max(0.0, min(1.0, score))

    if contradicted:
        label = "비권고"
    elif score >= T_POSSIBLE and matched:
        label = "가능"
    elif score >= T_CONDITIONAL:
        label = "조건부"
    else:
        label = "비권고"

    basis = {
        "matched": len(matched),
        "signals_total": len(discriminating),
        "ratio_denominator": denom,
        "signal_ratio": round(signal_ratio, 3),
        "bucket_confidence": round(conf, 3),
        "contradicted": len(contradicted),
        "matched_needed_for_조건부": _needed_matches(T_CONDITIONAL, conf, denom),
        "matched_needed_for_가능": _needed_matches(T_POSSIBLE, conf, denom),
        "unmatched_signals": unmatched,
    }
    return {
        "bucket_id": bucket.get("id"),
        "bucket_name": bucket.get("bucket_name"),
        "matched_signals": matched,
        "contradicted": contradicted,
        "confidence": round(score, 3),
        "label": label,
        "match_evidence": [_evidence(s, observed_tokens, obs_hits, canon) for s in matched],
        "contradicted_evidence": [_evidence(s, observed_tokens, obs_hits, canon) for s in contradicted],
        "label_basis": basis,
    }


def rank_buckets(
    observed_signals: list[str],
    symptom: str,
    buckets: list[dict[str, Any]],
    *,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    canon = get_canonicalizer()
    scored = [match_bucket(observed_signals, symptom, b, canon=canon) for b in buckets]
    scored.sort(key=lambda r: r["confidence"], reverse=True)
    return scored[:top_k]
