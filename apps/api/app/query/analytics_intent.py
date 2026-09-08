"""Detect analytics / count intents (Phase 3). No LLM."""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from app.query.time_range import parse_relative_range
from app.tickets.query import resolve_date_field

_ANALYTICS = re.compile(
    r"건수|몇\s*건|비중|통계|집계|연도별|월별|추이|규모\s*추이|티켓\s*규모|"
    r"컴포넌트\s*별|Component\s*별|Component\s*\(|"
    r"상태\s*별|담당\s*별|분포|"
    r"차지하는.{0,8}(비중|규모)|코퍼스\s*(기준|에서)|"
    # type / category breakdown of support work
    r"유형|종류|유형별|종류별|어떤\s*유형|어떤\s*종류|"
    r"유형\s*의\s*지원|지원\s*유형|지원\s*종류|분야별|카테고리별|"
    r"어떤\s*지원이\s*진행|지원이\s*진행된|이슈\s*유형|이슈\s*종류|"
    r"분류|분류해|분류하|분류까|어떻게\s*분류|카테고리\s*화|그룹핑",
    re.I,
)
# Issue-kind breakdown (성능이슈·설정오류…) — NOT Jira Component work categories
_ISSUE_TYPE_BREAKDOWN = re.compile(
    r"유형|종류|유형별|종류별|어떤\s*유형|어떤\s*종류|"
    r"유형\s*의\s*지원|지원\s*유형|지원\s*종류|분야별|카테고리별|"
    r"어떤\s*지원이\s*진행|지원이\s*진행된|이슈\s*유형|이슈\s*종류|"
    r"어떤\s*(이슈|문제|장애\s*유형)|실제\s*유형|"
    r"분류|분류해|분류하|분류까|어떻게\s*분류|분류\s*까지|"
    r"카테고리\s*화|묶어서\s*보|그룹핑|그룹\s*핑",
    re.I,
)
# Explicit Jira Component axis only
_COMPONENT_AXIS = re.compile(
    r"컴포넌트\s*별|Component\s*별|Component\s*비중|업무\s*유형|"
    r"기술지원/?장애지원|장애지원/?기술지원|Component\s*\(",
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
# Calendar year: 2026년 / 26년 / '26년
_CALENDAR_YEAR = re.compile(
    r"(?:(?:19|20)?(\d{2})|((?:19|20)\d{2}))\s*년",
    re.I,
)

# Known entity aliases → search needle
_ENTITIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"모니모|monimo", re.I), "모니모"),
    # Avoid \b with Korean: "SCP와" has no ASCII word boundary after SCP
    (re.compile(r"(?<![A-Za-z0-9_])SCP(?![A-Za-z0-9_])|에스씨피", re.I), "SCP"),
    (re.compile(r"오라클|Oracle", re.I), "Oracle"),
    (re.compile(r"(?<![A-Za-z0-9_])Redis(?![A-Za-z0-9_])|레디스", re.I), "Redis"),
]

_COMP_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"장애\s*지원|장애지원", re.I), "장애지원"),
    (re.compile(r"기술\s*지원|기술지원", re.I), "기술지원"),
    (re.compile(r"진단\s*컨설팅|진단컨설팅", re.I), "진단컨설팅"),
]

# SWIM(전사 장애관리 시스템) incident_reports 질의 감지. 매치 시 source_type을
# support_history 대신 incident_reports로 라우팅한다. _COMP_MAP(Jira Component:
# 장애지원/기술지원/진단컨설팅)과 SWIM의 metadata 키(장애유형/진행상태 등)는 서로
# 다른 축이므로 절대 혼용하지 않는다 — component/status/assignee group_by는
# aggregate_tickets._bucket_key가 Jira 전용 키(Component/Status/Assignee)를
# 그대로 읽기 때문에 SWIM 문서에서는 전부 "(empty)" 버킷이 된다. 그래서 SWIM
# 질의는 이 세 축을 쓰지 않고 total로 낮춘다(아래 detect_analytics_intent 참고).
_SWIM_HINT = re.compile(r"SWIM|전사\s*장애|장애\s*보고서", re.I)


def detect_analytics_intent(text: str) -> Optional[dict]:
    """Return analytics intent params, or None if not analytics-like."""
    t = (text or "").strip()
    if not t:
        return None
    if not _ANALYTICS.search(t) and not _TITLE_TOKENS.search(t):
        return None
    if _ANALYTICS_EXCLUDE.search(t) and not re.search(r"건수|비중|티켓\s*규모", t):
        return None

    swim = bool(_SWIM_HINT.search(t))

    # title token analytics (I23 등)
    if _TITLE_TOKENS.search(t) and not re.search(r"건수|비중|연도별", t):
        component = None
        if not swim:
            for pat, name in _COMP_MAP:
                if pat.search(t):
                    component = name
                    break
            if component is None and re.search(r"장애", t) and not re.search(r"기술", t):
                component = "장애지원"
        source_type = "incident_reports" if swim else "support_history"
        return {
            "intent": "analytics",
            "mode": "title_tokens",
            "group_by": "token",
            "source_type": source_type,
            "date_field": resolve_date_field(source_type, None),
            "component": component,
            "entity": None,
        }

    group_by = "total"
    if _YEAR.search(t):
        group_by = "year"
    elif _MONTH.search(t):
        group_by = "month"
    elif _COMPONENT_AXIS.search(t) or (
        _COMPONENT.search(t) and not _ISSUE_TYPE_BREAKDOWN.search(t)
    ):
        # Explicit Component axis only
        group_by = "component"
    elif _ISSUE_TYPE_BREAKDOWN.search(t):
        # 「지원 유형」「어떤 유형의 이슈」→ issue-kind (성능이슈 등)
        group_by = "issue_type"
    elif _STATUS.search(t):
        group_by = "status"
    elif _ASSIGNEE.search(t):
        group_by = "assignee"
    elif _SHARE.search(t) and not any(p.search(t) for p, _ in _ENTITIES):
        # bare 비중 without entity → Component share is still useful
        group_by = "component"
    # 「최근 기술지원 건 … 유형」 등 — total only is useless
    if group_by == "total" and (
        _ISSUE_TYPE_BREAKDOWN.search(t)
        or re.search(r"기술\s*지원\s*건|지원\s*건들", t)
    ):
        group_by = "issue_type"

    if swim and group_by in {"component", "status", "assignee"}:
        # aggregate_tickets._bucket_key reads Jira-only metadata keys
        # (Component/Status/Assignee) for these axes — SWIM docs have none of
        # those, so every row would collapse into a meaningless "(empty)"
        # bucket. Downgrade to total rather than emit a misleading breakdown.
        group_by = "total"

    component = None
    if not swim:
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
    # 「기술지원의 유형」= whole support corpus issue-kind, not Component filter
    if group_by == "issue_type" and component == "기술지원":
        component = None
    if group_by == "component" and component == "기술지원" and _ISSUE_TYPE_BREAKDOWN.search(t):
        component = None

    out_source_type = "incident_reports" if swim else "support_history"
    out: dict = {
        "intent": "analytics",
        "mode": mode,
        "group_by": group_by if mode == "aggregate" else "total",
        "source_type": out_source_type,
        "date_field": resolve_date_field(out_source_type, None),
        "component": component,
        "entity": entity,
        "include_samples": True
        if group_by in {"component", "issue_type"}
        else False,
        "sample_limit": 8,
    }
    if dr:
        out["date_from"] = dr.date_from.isoformat()
        out["date_to"] = dr.date_to.isoformat()
        out["range_label"] = dr.label
    else:
        cy = _parse_calendar_year(t)
        if cy is not None:
            out["date_from"] = date(cy, 1, 1).isoformat()
            out["date_to"] = date(cy, 12, 31).isoformat()
            out["range_label"] = f"{cy}년"
        elif re.search(r"최근", t) and mode == "aggregate":
            from datetime import timedelta

            today = date.today()
            try:
                from app.query.time_range import _today_kst

                today = _today_kst()
            except Exception:
                pass
            start = today - timedelta(days=89)
            out["date_from"] = start.isoformat()
            out["date_to"] = today.isoformat()
            out["range_label"] = "최근 90일"
    return out


def _parse_calendar_year(text: str) -> Optional[int]:
    """Parse 2026년 / 26년 / '26년 / 올해 → full year. Prefer 4-digit when present."""
    t = text or ""
    # Prefer explicit 4-digit years first
    m4 = re.search(r"(?<!\d)((?:19|20)\d{2})\s*년", t)
    if m4:
        y = int(m4.group(1))
        if 1990 <= y <= 2100:
            return y
    m2 = re.search(r"(?<!\d)'?(\d{2})\s*년", t)
    if m2:
        yy = int(m2.group(1))
        # 00–89 → 2000s, 90–99 → 1900s (pilot corpus is 2020s)
        y = 2000 + yy if yy < 90 else 1900 + yy
        if 1990 <= y <= 2100:
            return y
    if re.search(r"올해|금년|금\s*년|이번\s*해|당해", t):
        try:
            from app.query.time_range import _today_kst

            return _today_kst().year
        except Exception:
            from datetime import date as _date

            return _date.today().year
    return None
