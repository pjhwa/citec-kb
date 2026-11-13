"""DOgether 장애사례 본문에서 장애 발생 날짜를 뽑는다.

1순위는 장애 개요의 `시간`·`일시`·`발생`·`Time` 줄, 없으면 본문 전체. 둘째 줄의 게시일
(`< ‘26. 11. 05 BCS그룹 … >`)은 장애 날짜가 아니므로 '<'로 시작하는 줄은 뺀다.
게시일 이후 날짜는 장애 발생일일 수 없으므로 버린다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

# 게시일보다 1년 넘게 앞선 날짜는 본문 속 과거 사례·연도 오타(2026 → 2024)로 보고 버린다.
MAX_LAG_DAYS = 366
_APOS = "‘’'´`ʼ"
_ISO = re.compile(r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)")
_KOR = re.compile(r"(?<!\d)(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SHORT = re.compile(rf"[{_APOS}]\s*(\d{{2}})\s*[-./]\s*(\d{{1,2}})\s*[-./]\s*(\d{{1,2}})(?!\d)")
_OVERVIEW = re.compile(
    r"^[\s\-*•·▪□■○◦>]*(?:시간|일시|발생\s*(?:시간|일시|시각)?|Time|장애\s*(?:시간|일시|발생))\s*[:：]",
    re.I,
)


_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d"), "-")
# 개요 줄의 2자리 연도 표기(`26/04/14(수)`, `26.6.17(화)`) - 아포스트로피 없이 쓰므로 개요 줄에서만 인정
_YY = re.compile(r"(?<![\d.])(2\d)\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)")


def _norm(text: str) -> str:
    """마크다운 이스케이프(`\\-`, `\\~`)를 풀고 비분리·유니코드 하이픈을 `-`로 통일한다."""
    return text.replace("\\", "").translate(_DASHES)


def _mk(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def dates_in(text: str, *, allow_yy: bool = False) -> list[date]:
    text = _norm(text)
    found: list[date] = []
    rxs = [(_ISO, 0), (_KOR, 0), (_SHORT, 2000)] + ([(_YY, 2000)] if allow_yy else [])
    for rx, fix in rxs:
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
        ds = [d for d in ds if 2000 <= d.year <= 2100 and (posted is None or posted - timedelta(days=MAX_LAG_DAYS) <= d < posted)]
        return sorted(set(ds))

    ov: list[date] = []
    for ln in lines:
        if _OVERVIEW.match(_norm(ln)):
            ov.extend(dates_in(ln, allow_yy=True))
    ov = _clean(ov)
    if ov:
        return ov, "overview"
    rest = _clean(dates_in("\n".join(lines)))
    return (rest, "body") if rest else ([], "none")
