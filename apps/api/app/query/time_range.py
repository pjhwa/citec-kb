"""Relative date range parsing for time-scoped list queries (KST-aware)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass
class DateRange:
    date_from: date
    date_to: date  # inclusive end date
    label: str
    source_type_hint: Optional[str] = None


_LIST_INTENT = re.compile(
    r"지원\s*건|지원\s*이력|티켓|이슈\s*목록|목록|리스트|조회",
    re.I,
)
# 건수/몇 건 alone → analytics (see detect_analytics_intent); keep with list words for list path
_COUNT_AS_LIST = re.compile(r"건수|몇\s*건", re.I)
_SUPPORT_HINT = re.compile(r"지원|티켓|CITECTS|이슈\s*이력|장애\s*이력", re.I)

# Order matters: more specific first
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"지난\s*주|저번\s*주|지난주", re.I), "last_week"),
    (re.compile(r"이번\s*주|금주", re.I), "this_week"),
    (re.compile(r"지난\s*달|저번\s*달|전월", re.I), "last_month"),
    (re.compile(r"이번\s*달|금월", re.I), "this_month"),
    (re.compile(r"올해|금년|금\s*년|이번\s*해|당해", re.I), "this_year"),
    (re.compile(r"작년|지난해|전년", re.I), "last_year"),
    (re.compile(r"어제", re.I), "yesterday"),
    (re.compile(r"오늘", re.I), "today"),
    (re.compile(r"최근\s*(\d+)\s*일", re.I), "last_n_days"),
    (re.compile(r"지난\s*(\d+)\s*일", re.I), "last_n_days"),
    (re.compile(r"최근\s*(\d+)\s*주", re.I), "last_n_weeks"),
    (re.compile(r"최근\s*한\s*달|최근\s*1\s*달|지난\s*한\s*달", re.I), "last_30"),
    # bare "최근" (after n-day/week patterns) → default 90 days for support review
    (re.compile(r"최근", re.I), "last_90"),
]


def _today_kst(now: Optional[datetime] = None) -> date:
    if now is None:
        now = datetime.now(tz=KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(KST)
    else:
        now = now.astimezone(KST)
    return now.date()


def parse_relative_range(
    text: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[DateRange]:
    """Parse Korean relative time expressions into an inclusive date range."""
    t = (text or "").strip()
    if not t:
        return None
    today = _today_kst(now)

    for pat, kind in _PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        if kind == "today":
            return DateRange(today, today, "오늘")
        if kind == "yesterday":
            d = today - timedelta(days=1)
            return DateRange(d, d, "어제")
        if kind == "this_week":
            start = today - timedelta(days=today.weekday())  # Mon
            return DateRange(start, today, "이번 주")
        if kind == "last_week":
            this_mon = today - timedelta(days=today.weekday())
            start = this_mon - timedelta(days=7)
            end = this_mon - timedelta(days=1)
            return DateRange(start, end, "지난 주")
        if kind == "this_month":
            start = today.replace(day=1)
            return DateRange(start, today, "이번 달")
        if kind == "last_month":
            first = today.replace(day=1)
            end = first - timedelta(days=1)
            start = end.replace(day=1)
            return DateRange(start, end, "지난 달")
        if kind == "this_year":
            start = today.replace(month=1, day=1)
            return DateRange(start, today, f"{today.year}년(올해)")
        if kind == "last_year":
            y = today.year - 1
            return DateRange(date(y, 1, 1), date(y, 12, 31), f"{y}년(작년)")
        if kind == "last_30":
            start = today - timedelta(days=29)
            return DateRange(start, today, "최근 30일")
        if kind == "last_90":
            start = today - timedelta(days=89)
            return DateRange(start, today, "최근 90일")
        if kind == "last_n_days":
            n = int(m.group(1))
            n = max(1, min(n, 366))
            start = today - timedelta(days=n - 1)
            return DateRange(start, today, f"최근 {n}일")
        if kind == "last_n_weeks":
            n = int(m.group(1))
            n = max(1, min(n, 52))
            start = today - timedelta(days=7 * n - 1)
            return DateRange(start, today, f"최근 {n}주")
    return None


def detect_time_scoped_list(text: str) -> Optional[dict]:
    """If query is a time-scoped list/count intent, return structured intent.

    Example: 「지난 주 지원건」 → list support_history for last week.
    """
    t = (text or "").strip()
    if not t:
        return None
    dr = parse_relative_range(t)
    if not dr:
        return None
    # Require list-ish intent OR explicit support wording with a range
    listish = bool(_LIST_INTENT.search(t)) or bool(_COUNT_AS_LIST.search(t))
    supportish = bool(_SUPPORT_HINT.search(t))
    if not (listish or supportish):
        # bare "지난 주" alone is ambiguous
        return None
    source = "support_history" if supportish or "지원" in t else None
    if re.search(r"지원", t):
        source = "support_history"
    return {
        "intent": "time_scoped_list",
        "date_from": dr.date_from.isoformat(),
        "date_to": dr.date_to.isoformat(),
        "range_label": dr.label,
        "source_type": source or "support_history",
        "date_field": "Created",  # default Jira Created
    }
