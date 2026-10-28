"""Search filter argument normalisation (A4).

`section`/`source_type`와 `area`/`domain`은 예전에 문자열 그대로 SQL 동등 비교에
들어가서, 알려진 별칭(`checkitems`)이나 대소문자 차이(`Linux`)가 오류 없이 0건이
됐다. 여기서는

- source_type: 별칭을 정규화하고, 모르는 값은 허용 목록과 함께 ValueError로 거부한다.
- domain(area): 대소문자만 실제 저장된 값에 맞추고, 어떤 문서에도 없는 값은 거부하지
  않고 경고 문구를 돌려준다(domain 은 데이터에서 자라는 열린 집합이라서).
"""

from __future__ import annotations

import time
from typing import Iterable, Optional

from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope

# 2026-10-28 운영 distinct source_type + 호환 엔드포인트가 받는 값.
KNOWN_SOURCE_TYPES: tuple[str, ...] = (
    "confluence_map",
    "confluence_docs",
    "tech_repo",
    "support_history",
    "incident_reports",
    "checkitem",
    "dept_archive",
    "tuning_ai",
    "failure_bucket",
    "insight",
    "vendor_docs",
)

# 별칭(소문자, `-`는 `_`로 바꾼 뒤 비교) → 실제 source_type.
SOURCE_TYPE_ALIASES: dict[str, str] = {
    "checkitems": "checkitem",
    "pisa": "checkitem",
    "failure_buckets": "failure_bucket",
    "insights": "insight",
    "synthesis": "insight",
    "sql_tuning": "tuning_ai",
    "incident": "incident_reports",
    "incident_report": "incident_reports",
    "swim": "incident_reports",
    "support": "support_history",
    "map": "confluence_map",
    "dept_archives": "dept_archive",
}

_CACHE_TTL = 300.0
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, loader):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    _cache.clear()


def _db_source_types() -> frozenset[str]:
    def load():
        try:
            with session_scope() as s:
                rows = s.execute(select(Document.source_type).distinct()).all()
            return frozenset(r[0] for r in rows if r[0])
        except Exception:  # noqa: BLE001 — 허용 목록은 정적 목록으로도 충분히 동작
            return frozenset()

    return _cached("source_types", load)  # type: ignore[return-value]


def allowed_source_types() -> list[str]:
    return sorted(set(KNOWN_SOURCE_TYPES) | _db_source_types())


def normalize_source_type(value: Optional[str], *, field: str = "section") -> Optional[str]:
    """None/빈 값은 None. 별칭·대소문자를 정규화. 모르는 값은 ValueError(허용 목록 포함)."""
    raw = (value or "").strip()
    if not raw:
        return None
    key = raw.lower().replace("-", "_")
    allowed = allowed_source_types()
    if key in allowed:
        return key
    if key in SOURCE_TYPE_ALIASES:
        return SOURCE_TYPE_ALIASES[key]
    raise ValueError(f"invalid {field} {raw!r} — allowed: {allowed}")


def normalize_source_types(values: Optional[Iterable[str]], *, field: str) -> Optional[list[str]]:
    if not values:
        return None
    out: list[str] = []
    for v in values:
        n = normalize_source_type(v, field=field)
        if n and n not in out:
            out.append(n)
    return out or None


def _db_domains() -> dict[str, str]:
    def load():
        with session_scope() as s:
            rows = s.execute(
                select(Document.domain).where(Document.status == "active").distinct()
            ).all()
        # 대소문자만 다른 값이 둘이면 사전순 첫 번째를 정본으로(결정적)
        out: dict[str, str] = {}
        for (d,) in sorted(r for r in rows if r[0]):
            out.setdefault(d.lower(), d)
        return out

    return _cached("domains", load)  # type: ignore[return-value]


def canonical_domain(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(정규화된 domain, 경고). 저장된 값과 대소문자만 다르면 저장된 값으로 바꾼다.

    어떤 문서에도 없는 값은 그대로 두고(결과 0건이 정상일 수 있다) 경고만 돌려준다.
    """
    raw = (value or "").strip()
    if not raw:
        return None, None
    try:
        known = _db_domains()
    except Exception:  # noqa: BLE001
        return raw, None
    hit = known.get(raw.lower())
    if hit:
        return hit, None
    return raw, (
        f"area/domain {raw!r}에 해당하는 문서가 없습니다(대소문자 무시). "
        "kb_citec_domain_catalog·kb_stats 등으로 실제 값을 확인하세요."
    )
