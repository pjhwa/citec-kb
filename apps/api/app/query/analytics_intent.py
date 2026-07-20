"""Detect analytics / count intents (Phase 3). No LLM."""

from __future__ import annotations

import re
from typing import Optional

from app.query.time_range import parse_relative_range

_ANALYTICS = re.compile(
    r"건수|몇\s*건|비중|통계|집계|연도별|월별|추이|규모\s*추이|티켓\s*규모|"
    r"컴포넌트\s*별|Component\s*별|Component\s*\(|"
    r"상태\s*별|담당\s*별|분포|"
    r"차지하는.{0,8}(비중|규모)|코퍼스\s*(기준|에서)",
    re.I,
)
# 문서 탐색은 hybrid; 제목 패턴/키워드는 title_tokens 모드
_ANALYTICS_EXCLUDE = re.compile(r"문서|공지", re.I)
_TITLE_TOKENS = re.compile(r"제목\s*패턴|상위\s*키워드|키워드", re.I)
_YEAR = re.compile(r"연도별|연도\s*추이|년도별|year", re.I)
_MONTH = re.compile(r"월별|month", re.I)
_COMPONENT = re.compile(r"컴포넌트|Component", re.I)
_STATUS = re.compile(r"상태\s*별|Status", re.I)
_ASSIGNEE = re.compile(r"담당|Assignee", re.I)
_SHARE = re.compile(r"비중|비율|share", re.I)

# Known entity aliases → search needle
_ENTITIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"모니모|monimo", re.I), "모니모"),
    (re.compile(r"\bSCP\b|에스씨피", re.I), "SCP"),
    (re.compile(r"오라클|Oracle", re.I), "Oracle"),
    (re.compile(r"Redis|레디스", re.I), "Redis"),
]

_COMP_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"장애\s*지원|장애지원", re.I), "장애지원"),
    (re.compile(r"기술\s*지원|기술지원", re.I), "기술지원"),
    (re.compile(r"진단\s*컨설팅|진단컨설팅", re.I), "진단컨설팅"),
]


def detect_analytics_intent(text: str) -> Optional[dict]:
    """Return analytics intent params, or None if not analytics-like."""
    t = (text or "").strip()
    if not t:
        return None
    if not _ANALYTICS.search(t) and not _TITLE_TOKENS.search(t):
        return None
    if _ANALYTICS_EXCLUDE.search(t) and not re.search(r"건수|비중|티켓\s*규모", t):
        return None

    # title token analytics (I23 등)
    if _TITLE_TOKENS.search(t) and not re.search(r"건수|비중|연도별", t):
        component = None
        for pat, name in _COMP_MAP:
            if pat.search(t):
                component = name
                break
        if component is None and re.search(r"장애", t) and not re.search(r"기술", t):
            component = "장애지원"
        return {
            "intent": "analytics",
            "mode": "title_tokens",
            "group_by": "token",
            "source_type": "support_history",
            "date_field": "Created",
            "component": component,
            "entity": None,
        }

    group_by = "total"
    if _YEAR.search(t):
        group_by = "year"
    elif _MONTH.search(t):
        group_by = "month"
    elif _COMPONENT.search(t):
        group_by = "component"
    elif _STATUS.search(t):
        group_by = "status"
    elif _ASSIGNEE.search(t):
        group_by = "assignee"
    elif _SHARE.search(t) and not any(p.search(t) for p, _ in _ENTITIES):
        # bare 비중 without entity → component share is most useful
        group_by = "component"

    component = None
    for pat, name in _COMP_MAP:
        if pat.search(t):
            component = name
            break
    # "장애 건수" without full 장애지원 → treat as 장애지원 component
    if component is None and re.search(r"장애", t) and not re.search(r"기술", t):
        if re.search(r"건수|몇\s*건|비중", t):
            component = "장애지원"

    entity = None
    for pat, needle in _ENTITIES:
        if pat.search(t):
            entity = needle
            break

    # entity 비중/규모 → entity_share mode
    mode = "aggregate"
    share_like = bool(_SHARE.search(t) or re.search(r"규모|시그널|차지", t))
    if entity and (share_like or re.search(r"건수|몇\s*건", t)):
        mode = "entity_share"
    if entity and group_by == "total" and mode == "aggregate" and share_like:
        mode = "entity_share"


    dr = parse_relative_range(t)
    out: dict = {
        "intent": "analytics",
        "mode": mode,
        "group_by": group_by if mode == "aggregate" else "total",
        "source_type": "support_history",
        "date_field": "Created",
        "component": component,
        "entity": entity,
    }
    if dr:
        out["date_from"] = dr.date_from.isoformat()
        out["date_to"] = dr.date_to.isoformat()
        out["range_label"] = dr.label
    return out
