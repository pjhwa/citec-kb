"""Ticket list queries using Jira metadata Created/Resolved (not ingest timestamps)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.session import session_scope


_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")

# Per-source_type metadata key(s) that carry a usable date. support_history
# comes from Jira exports (Created/Resolved/Updated); incident_reports (SWIM)
# has none of those — its date lives under the raw SWIM header key 발생일시(한국)
# instead (see app/ingest/adapters.py:parse_incident_report_file).
_VALID_DATE_FIELDS: dict[str, frozenset[str]] = {
    "support_history": frozenset({"Created", "Resolved", "Updated"}),
    "incident_reports": frozenset({"발생일시(한국)"}),
}
_DEFAULT_DATE_FIELD: dict[str, str] = {
    "support_history": "Created",
    "incident_reports": "발생일시(한국)",
}


def resolve_date_field(source_type: str, date_field: Optional[str]) -> str:
    """Pick a metadata date key valid for source_type, or its default.

    A caller-supplied date_field that isn't valid for this source_type (e.g.
    the Jira default "Created" passed in for incident_reports) falls back to
    that source_type's own default rather than silently filtering everything
    out downstream.
    """
    allowed = _VALID_DATE_FIELDS.get(source_type, _VALID_DATE_FIELDS["support_history"])
    if date_field in allowed:
        return date_field
    return _DEFAULT_DATE_FIELD.get(source_type, "Created")


def parse_meta_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    s = str(value).strip()
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def get_ticket_by_external_id(
    external_id: str,
    *,
    source_type: str = "support_history",
) -> Optional[dict[str, Any]]:
    """Return one support ticket document with body for detail view."""
    eid = (external_id or "").strip()
    if not eid:
        return None
    with session_scope() as session:
        stmt = (
            select(Document)
            .where(Document.status == "active")
            .where(Document.source_type == source_type)
            .where(Document.external_id == eid)
            .limit(1)
        )
        d = session.scalars(stmt).first()
        if not d:
            # try without source filter
            stmt2 = (
                select(Document)
                .where(Document.status == "active")
                .where(Document.external_id == eid)
                .limit(1)
            )
            d = session.scalars(stmt2).first()
        if not d:
            return None
        meta = d.metadata_ if isinstance(d.metadata_, dict) else {}
        from app.doc_access import attach_document_access

        return attach_document_access(
            {
                "document_id": d.id,
                "external_id": d.external_id,
                "title": d.title,
                "source_type": d.source_type,
                "body_md": d.body_md or "",
                "status": meta.get("Status"),
                "component": meta.get("Component"),
                "assignee": meta.get("Assignee"),
                "Created": meta.get("Created"),
                "Resolved": meta.get("Resolved"),
                "Updated": meta.get("Updated"),
                "metadata": meta,
            }
        )


def list_tickets(
    *,
    source_type: str = "support_history",
    date_field: str = "Created",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
) -> dict[str, Any]:
    """List documents filtered by metadata date field in [from, to] inclusive."""
    date_field = resolve_date_field(source_type, date_field)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    descending = (order or "desc").lower() != "asc"

    with session_scope() as session:
        # Fetch candidates with metadata key present; filter in Python for robust date formats.
        # Scale: ~2k support_history is fine; for larger sets add generated columns later.
        stmt = (
            select(Document)
            .where(Document.status == "active")
            .where(Document.source_type == source_type)
            .where(Document.metadata_.has_key(date_field))  # type: ignore[attr-defined]
        )
        docs = list(session.scalars(stmt).all())

        rows: list[dict[str, Any]] = []
        for d in docs:
            meta = d.metadata_ or {}
            raw = meta.get(date_field)
            dt = parse_meta_date(raw if isinstance(raw, str) else None)
            if dt is None:
                continue
            if date_from and dt < date_from:
                continue
            if date_to and dt > date_to:
                continue
            rows.append(
                {
                    "document_id": d.id,
                    "external_id": d.external_id,
                    "title": d.title,
                    "source_type": d.source_type,
                    "source_uri": d.source_uri,
                    "status": (meta.get("Status") if isinstance(meta, dict) else None),
                    "component": (meta.get("Component") if isinstance(meta, dict) else None),
                    "assignee": (meta.get("Assignee") if isinstance(meta, dict) else None),
                    "Created": meta.get("Created") if isinstance(meta, dict) else None,
                    "Resolved": meta.get("Resolved") if isinstance(meta, dict) else None,
                    "Updated": meta.get("Updated") if isinstance(meta, dict) else None,
                    "_sort": dt,
                }
            )

        rows.sort(key=lambda r: r["_sort"], reverse=descending)
        total = len(rows)
        page = rows[offset : offset + limit]
        from app.doc_access import attach_document_access

        for r in page:
            r.pop("_sort", None)
            attach_document_access(r)

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "order": "desc" if descending else "asc",
            "source_type": source_type,
            "date_field": date_field,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "items": page,
        }
