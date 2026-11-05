"""Pure rules for decisions (no DB): option lint, quote matching, conflict, queue score."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Optional

RESERVED_KEYS = ("NEI", "OTHER")
MIN_OPTIONS, MAX_OPTIONS = 2, 8
STANCES = ("supports", "refutes", "context")
STAKES = ("low", "mid", "high")
STATUSES = ("proposed", "confirmed", "needs_recheck", "superseded", "retired")

# 질문에 둘 이상의 판정이 섞였을 가능성이 있는 접속 표현(경고만, 차단하지 않는다).
_COMPOUND = re.compile(r"(\s및\s|\s그리고\s|\band\b|\s와\s.+\s여부|\s이고\s)", re.I)

DEFAULT_QUEUE_WEIGHTS = {"conflict": 1.0, "stakes": 1.0, "age": 0.5, "changed": 0.5}
_STAKES_W = {"low": 0.0, "mid": 0.5, "high": 1.0}


def norm_text(s: Optional[str]) -> str:
    """NFC + 공백 런 1칸 + trim. 인용 검증과 라벨 중복 판정에 같이 쓴다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def quote_in_body(quote: str, body: str) -> bool:
    q = norm_text(quote)
    return bool(q) and q in norm_text(body)


def lint_options(question: str, options: Iterable[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    """Return (options incl. reserved NEI/OTHER, warnings). Raise ValueError on hard failures."""
    q = norm_text(question)
    if not q or len(q) > 300:
        raise ValueError("question must be 1..300 chars")
    out: list[dict[str, str]] = []
    keys: set[str] = set()
    labels: set[str] = set()
    for o in options:
        key = str(o.get("key") or "").strip()
        label = norm_text(o.get("label"))
        if not key or not label:
            raise ValueError("each option needs key and label")
        if key.upper() in RESERVED_KEYS:
            raise ValueError(f"option key {key!r} is reserved (NEI/OTHER are added by the server)")
        if key in keys:
            raise ValueError(f"duplicate option key: {key}")
        if label.casefold() in labels:
            raise ValueError(f"duplicate option label: {label}")
        keys.add(key)
        labels.add(label.casefold())
        out.append({"key": key, "label": label, "summary": norm_text(o.get("summary"))})
    if not (MIN_OPTIONS <= len(out) <= MAX_OPTIONS):
        raise ValueError(f"options must be {MIN_OPTIONS}..{MAX_OPTIONS} (excluding NEI/OTHER)")
    warnings = []
    if _COMPOUND.search(f" {q} "):
        warnings.append("질문에 복합 표현이 있습니다. 판정 하나에 한 가지만 묻도록 분할을 검토하세요.")
    out.append({"key": "NEI", "label": "판단 불가", "summary": "근거 부족"})
    out.append({"key": "OTHER", "label": "직접 입력", "summary": "수정·직접 서술"})
    return out, warnings


def conflict_flag(evidence: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """충돌 = (a) 서로 다른 두 선택지에 supports 근거가 각각 ≥1건,
    또는 (b) supports가 가장 많은 선택지에 refutes가 ≥1건. retracted 근거는 제외."""
    sup: dict[str, int] = {}
    ref: dict[str, int] = {}
    for e in evidence:
        if e.get("retracted_at") or not e.get("option_key"):
            continue
        k = e["option_key"]
        if e.get("stance") == "supports":
            sup[k] = sup.get(k, 0) + 1
        elif e.get("stance") == "refutes":
            ref[k] = ref.get(k, 0) + 1
    reasons = []
    if len(sup) >= 2:
        reasons.append("서로 다른 선택지를 지지하는 근거가 있음: " + ", ".join(sorted(sup)))
    if sup:
        top = max(sup.values())
        for k in sorted(k for k, v in sup.items() if v == top):
            if ref.get(k):
                reasons.append(f"가장 많이 지지된 선택지 {k}에 반박 근거 {ref[k]}건")
    return {"conflict": bool(reasons), "reasons": reasons}


def queue_score(
    *, conflict: bool, stakes: str, age_days: float, review_interval_days: Optional[int],
    changed_pins: int, weights: Optional[dict[str, float]] = None,
) -> tuple[float, str]:
    w = {**DEFAULT_QUEUE_WEIGHTS, **(weights or {})}
    age_ratio = min(age_days / review_interval_days, 2.0) if review_interval_days else 0.0
    changed = min(max(changed_pins, 0), 3)
    score = (
        w["conflict"] * (1.0 if conflict else 0.0)
        + w["stakes"] * _STAKES_W.get(stakes, 0.5)
        + w["age"] * age_ratio
        + w["changed"] * changed / 3.0
    )
    why = []
    if conflict:
        why.append("충돌")
    if stakes == "high":
        why.append("위험 높음")
    elif stakes == "mid":
        why.append("위험 중")
    if changed:
        why.append(f"변경 {changed}건")
    if age_ratio >= 1.0:
        why.append(f"마지막 판정 {int(age_days)}일 경과")
    return round(score, 4), (" + ".join(why) or "특이 사항 없음")
