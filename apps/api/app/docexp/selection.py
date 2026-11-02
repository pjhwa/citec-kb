"""Which documents get an expansion: low-information titles (rule-based).

The rules never look at queries or the eval set.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

MIN_BODY_CHARS = 200
SOURCE_TYPE = "confluence_map"

_MEETING = re.compile(r"(미팅|회의|주간|주차|월간|scrum|스크럼|meeting)", re.I)
_API = re.compile(r"^\s*(GET|POST|PUT|DELETE|PATCH)\s|^\s*/", re.I)
_DATE = re.compile(r"^\W*(\d{4}[-./]\d{1,2}|\d{1,2}월|\d{2}-\d{2}-\d{2}|\d{6,8})")


def classify_title(title: str | None) -> str | None:
    """Return the rule name ('api' | 'meeting' | 'date') or None."""
    t = (title or "").strip()
    if not t:
        return None
    if _API.search(t):
        return "api"
    if _MEETING.search(t):
        return "meeting"
    if _DATE.search(t):
        return "date"
    return None


def select_documents(*, limit: int | None = None, seed: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield {id, external_id, title, body_md, rule} for active confluence_map rows.

    Ordered by external_id (stable). With ``seed`` the order is a deterministic
    hash order instead, so ``limit`` yields a reproducible random sample.
    """
    from sqlalchemy import func, select

    from app.db.models import Document
    from app.db.session import session_scope

    order = Document.external_id if seed is None else func.md5(func.concat(Document.external_id, str(int(seed))))
    stmt = (
        select(Document.id, Document.external_id, Document.title, Document.body_md)
        .where(Document.source_type == SOURCE_TYPE, Document.status == "active")
        .where(func.length(Document.body_md) >= MIN_BODY_CHARS)
        .order_by(order)
    )
    n = 0
    with session_scope() as session:
        for did, ext, title, body in session.execute(stmt.execution_options(yield_per=500)):
            rule = classify_title(title)
            if rule is None:
                continue
            yield {"id": did, "external_id": ext, "title": title, "body_md": body or "", "rule": rule}
            n += 1
            if limit and n >= limit:
                return
