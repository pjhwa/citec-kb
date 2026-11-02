"""Which documents get an expansion: low-information titles (rule-based).

The rules never look at queries or the eval set.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

MIN_BODY_CHARS = 200
SOURCE_TYPE = "confluence_map"

_MEETING = re.compile(
    r"(미팅|회의(?!실)|주간|\d+\s*주차|월간|scrum|스크럼|\bmeeting\b)", re.I
)
# HTTP method is case-sensitive ("Patch 적용 가이드" is not an API); bare paths only for API-ish prefixes.
_API = re.compile(r"^\s*(?:(?-i:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/|/(?:v\d+|api|console)\b)")
_DATE = re.compile(
    r"^\W*(\d{4}[-./]\d{1,2}|\d{2,4}년|\d{1,2}월|\d{2}-\d{2}-\d{2}|\d{6,8})"
)
BATCH = 200


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


def _candidates_stmt() -> Any:
    from sqlalchemy import func, select

    from app.db.models import Document

    # Same tech_relevant=irrelevant exclusion the default search applies to confluence_map.
    tech = Document.metadata_["tech_relevant"].astext
    return (
        select(Document.id, Document.external_id, Document.title, Document.content_hash)
        .where(Document.source_type == SOURCE_TYPE, Document.status == "active")
        .where(func.length(Document.body_md) >= MIN_BODY_CHARS)
        .where(tech.is_(None) | (tech != "irrelevant"))
        .order_by(Document.external_id)
    )


def _list_candidates() -> list[tuple[Any, str, str, str | None]]:
    """(id, external_id, title, content_hash) of active, non-irrelevant confluence_map rows with enough body; short session."""
    from app.db.session import session_scope

    with session_scope() as session:
        return [(r[0], r[1], r[2], r[3]) for r in session.execute(_candidates_stmt()).all()]


def _fetch_bodies(ids: list[Any]) -> dict[Any, str]:
    from sqlalchemy import select

    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        rows = session.execute(select(Document.id, Document.body_md).where(Document.id.in_(ids))).all()
    return {r[0]: r[1] or "" for r in rows}


def select_documents(*, limit: int | None = None, seed: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield {id, external_id, title, body_md, rule, content_hash} for active confluence_map rows.

    Ordered by external_id (stable). With ``seed`` the order is a deterministic
    hash order instead, so ``limit`` yields a reproducible random sample.
    Candidates are classified from titles alone; bodies are fetched lazily in
    small batches so no DB cursor/session stays open while the caller works.
    """
    matched = []
    for did, ext, title, chash in _list_candidates():
        rule = classify_title(title)
        if rule is not None:
            matched.append({"id": did, "external_id": ext, "title": title, "rule": rule, "content_hash": chash})
    if seed is not None:
        matched.sort(key=lambda m: hashlib.md5(f"{m['external_id']}{int(seed)}".encode()).hexdigest())
    else:
        matched.sort(key=lambda m: m["external_id"])
    if limit is not None:
        matched = matched[: max(int(limit), 0)]
    for i in range(0, len(matched), BATCH):
        chunk = matched[i : i + BATCH]
        bodies = _fetch_bodies([m["id"] for m in chunk])
        for m in chunk:
            yield {**m, "body_md": bodies.get(m["id"], "")}
