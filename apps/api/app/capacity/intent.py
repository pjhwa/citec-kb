"""Detect capacity / pricing intent (Phase 3). No LLM."""

from __future__ import annotations

import re
from typing import Optional

from app.capacity.calculator import normalize_field

# Pure calc / pricing — avoid "진단 가능 여부/분야" factoid false positives
_CAPACITY = re.compile(
    r"가능\s*대수|분야별\s*(대수|단가|공수)|"
    r"0\.25\s*M|0\.25\s*M\s*/\s*M|1안|"
    r"분야별\s*단가|단가\s*기준|"
    r"(\d+)\s*주\s*.{0,12}(대수|M\s*/\s*M|M/M|공수)|"
    r"(대수|M\s*/\s*M|M/M|공수).{0,12}(\d+)\s*주|"
    r"투입\s*M\s*/\s*M|소요\s*M\s*/\s*M|용량\s*산정|"
    r"진단\s*(수량|공수)|공수\s*산정",
    re.I,
)
# "사례/계획/계약 문서 검색"은 hybrid
_CASE_LOOKUP = re.compile(
    r"사례|계획\s*사례|제안한\s*사례|선정\s*논리|계약|입장|기조|정책|여부는",
    re.I,
)
_RULES_FORCE = re.compile(
    r"0\.25|1안|단가\s*기준|가능\s*대수|분야별\s*단가|산정\s*1안|전체\s*분야\s*목록|"
    r"분야별\s*(가능\s*)?대수",
    re.I,
)

_PERIOD_WEEKS = re.compile(r"(\d+)\s*주")
_PERIOD_DAYS = re.compile(r"(\d+)\s*일")
_ONE_WEEK = re.compile(r"1\s*주|한\s*주|일주일", re.I)
_TWO_WEEKS = re.compile(r"2\s*주|두\s*주", re.I)

_FIELD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bAIX\b", re.I), "AIX"),
    (re.compile(r"HP-?UX", re.I), "HP-UX"),
    (re.compile(r"Solaris", re.I), "Solaris"),
    (re.compile(r"Linux|리눅스", re.I), "Linux"),
    (re.compile(r"Windows|윈도우", re.I), "Windows"),
    (re.compile(r"Web\s*/?\s*WAS|WAS", re.I), "Web/WAS"),
    (re.compile(r"\bDBMS\b|\bDB\b|데이터베이스", re.I), "DBMS"),
    (re.compile(r"가상화|Virtualization", re.I), "가상화"),
    (re.compile(r"스토리지|Storage", re.I), "스토리지"),
    (re.compile(r"네트워크|Network|\bNW\b", re.I), "네트워크"),
]


def parse_period_days(text: str) -> int:
    t = text or ""
    if _TWO_WEEKS.search(t):
        return 14
    if _ONE_WEEK.search(t):
        return 7
    m = _PERIOD_WEEKS.search(t)
    if m:
        return max(1, min(int(m.group(1)), 52)) * 7
    m = _PERIOD_DAYS.search(t)
    if m:
        return max(1, min(int(m.group(1)), 365))
    return 7  # default 1-week standard


def extract_fields(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pat, name in _FIELD_PATTERNS:
        if pat.search(text or ""):
            n = normalize_field(name) or name
            if n not in seen:
                seen.add(n)
                found.append(n)
    return found


def detect_capacity_intent(text: str) -> Optional[dict]:
    t = (text or "").strip()
    if not t:
        return None
    if not _CAPACITY.search(t):
        return None
    # 문서 사례 조회는 hybrid 검색으로 넘김 (규칙 계산 아님)
    if _CASE_LOOKUP.search(t) and not _RULES_FORCE.search(t):
        return None
    fields = extract_fields(t)
    period_days = parse_period_days(t)
    want_price = bool(re.search(r"단가|가격|비용|price", t, re.I))
    want_mm = bool(re.search(r"M\s*/\s*M|M/M|공수|0\.25|mm\b", t, re.I))
    want_units = bool(re.search(r"대수|수량|units?", t, re.I))
    # default: full estimate
    return {
        "intent": "capacity",
        "basis": "1안",
        "period_days": period_days,
        "fields": fields or None,  # None = all fields
        "include_pricing": True if want_price or not (want_mm and not want_price) else want_price,
        "focus": (
            "price" if want_price and not want_units and not want_mm
            else "mm" if want_mm and not want_units and not want_price
            else "units" if want_units and not want_mm and not want_price
            else "all"
        ),
    }
