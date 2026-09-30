"""Shared Jira Component-axis extraction (기술지원/장애지원/진단컨설팅).

Split out of analytics_intent.py so every intent path that can carry a
Component constraint (analytics *and* time_scoped_list) uses one predicate
source instead of drifting apart. Do not confuse this axis with the SWIM
metadata axes (장애유형/진행상태 등) — see the _SWIM_HINT comment in
analytics_intent.py for why the two must never be blended.
"""

from __future__ import annotations

import re
from typing import Optional

# Order matters: more specific literal phrases first.
COMP_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"장애\s*지원|장애지원", re.I), "장애지원"),
    (re.compile(r"기술\s*지원|기술지원", re.I), "기술지원"),
    (re.compile(r"진단\s*컨설팅|진단컨설팅", re.I), "진단컨설팅"),
]


def extract_component(text: str, *, swim: bool = False) -> Optional[str]:
    """Return the Jira Component name explicit in `text`, or None.

    A bare "지원건"/"지원 이력" (no 기술/장애 prefix) intentionally does not
    match — that phrasing means "all support work", not one Component. This
    is what distinguishes 사용자 표현 "지원건 전체" from "기술지원 이력" per
    docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §7.

    swim=True (incident_reports/SWIM queries) never returns a Component —
    SWIM documents carry no Jira Component metadata key.
    """
    if swim:
        return None
    t = text or ""
    for pat, name in COMP_MAP:
        if pat.search(t):
            return name
    return None
