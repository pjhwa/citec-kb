"""DOgether 장애사례 본문에서 장애 발생 날짜를 뽑는다.

1순위는 장애 개요의 `시간`·`일시`·`발생`·`Time` 줄, 없으면 본문 전체. 둘째 줄의 게시일
(`< ‘26. 11. 05 BCS그룹 … >`)은 장애 날짜가 아니므로 '<'로 시작하는 줄은 뺀다.
게시일 이후 날짜는 장애 발생일일 수 없으므로 버린다.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

_APOS = "‘’'´`ʼ"
_ISO = re.compile(r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)")
_KOR = re.compile(r"(?<!\d)(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SHORT = re.compile(rf"[{_APOS}]\s*(\d{{2}})\s*[-./]\s*(\d{{1,2}})\s*[-./]\s*(\d{{1,2}})(?!\d)")
_OVERVIEW = re.compile(
    r"^[\s\-*•·▪□■○◦>]*(?:시간|일시|발생\s*(?:시간|일시|시각)?|Time|장애\s*(?:시간|일시|발생))\s*[:：]",
    re.I,
)


def _mk(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def dates_in(text: str) -> list[date]:
    found: list[date] = []
    for rx, fix in ((_ISO, 0), (_KOR, 0), (_SHORT, 2000)):
        for m in rx.finditer(text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = _mk(y + fix, mo, d)
            if dt:
                found.append(dt)
    return found


def _is_post_date_line(line: str) -> bool:
    return line.strip().startswith(("<", "＜", "《", "〈"))


def extract_incident_dates(body: str, posted: Optional[date] = None) -> tuple[list[date], str]:
    """(정렬된 고유 날짜, 출처 'overview'|'body'|'none')."""
    lines = [ln for ln in (body or "").splitlines() if not _is_post_date_line(ln)]

    def _clean(ds: list[date]) -> list[date]:
        ds = [d for d in ds if 2000 <= d.year <= 2100 and (posted is None or d < posted)]
        return sorted(set(ds))

    ov: list[date] = []
    for ln in lines:
        if _OVERVIEW.match(ln):
            ov.extend(dates_in(ln))
    ov = _clean(ov)
    if ov:
        return ov, "overview"
    rest = _clean(dates_in("\n".join(lines)))
    return (rest, "body") if rest else ([], "none")
