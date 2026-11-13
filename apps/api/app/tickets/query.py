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
    "dogether_preventive": frozenset({"게시일시"}),
}
_DEFAULT_DATE_FIELD: dict[str, str] = {
    "support_history": "Created",
    "incident_reports": "발생일시(한국)",
    "dogether_preventive": "게시일시",
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


_CITECTS_ID_RE = re.compile(r"^citects[\s_-]*0*(\d+)$", re.I)


def normalize_ticket_id(raw: str) -> str:
    """`citects-2502`, `CITECTS 2502`, `citects2502` → `CITECTS-2502` (그 외는 strip만)."""
    s = (raw or "").strip()
    m = _CITECTS_ID_RE.match(s)
    return f"CITECTS-{int(m.group(1))}" if m else s


def _ticket_exists(external_id: str, source_type: str) -> bool:
    with session_scope() as session:
        stmt = (
            select(Document.id)
            .where(Document.status == "active")
            .where(Document.source_type == source_type)
            .where(Document.external_id == external_id)
            .limit(1)
        )
        return session.scalars(stmt).first() is not None


def resolve_ticket(
    external_id: str, *, source_type: str = "support_history"
) -> dict[str, Any]:
    """Look up one ticket, tolerating case/format variants of the id.

    - 대소문자·`CITECTS 2502`·`citects2502` 같은 변형은 모호하지 않으므로 자동으로
      `CITECTS-2502`로 해석하고 `normalized_from`에 원래 입력을 남긴다.
    - 숫자만 온 경우(`2502`)는 자동 확정하지 않는다(SWIM 번호처럼 숫자만 쓰는 ID도
      있다). 없으면 `did_you_mean`으로 `CITECTS-2502` 후보만 제안한다.
    반환: {"row": dict|None, "did_you_mean": str|None}
    """
    eid = (external_id or "").strip()
    row = get_ticket_by_external_id(eid, source_type=source_type)
    if row:
        return {"row": row, "did_you_mean": None}
    for cand in dict.fromkeys([normalize_ticket_id(eid), eid.upper()]):
        if cand and cand != eid:
            row = get_ticket_by_external_id(cand, source_type=source_type)
            if row:
                row["normalized_from"] = eid
                return {"row": row, "did_you_mean": None}
    suggestion = None
    if eid.isdigit() and source_type == "support_history":
        cand = f"CITECTS-{int(eid)}"
        if _ticket_exists(cand, "support_history"):
            suggestion = cand
    return {"row": None, "did_you_mean": suggestion}


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

        out = attach_document_access(
            {
                "document_id": d.id,
                "external_id": d.external_id,
                "title": d.title,
                "source_type": d.source_type,
                "body_md": d.body_md or "",
                "source_uri": d.source_uri,
                "status": meta.get("Status"),
                "component": meta.get("Component"),
                "assignee": meta.get("Assignee"),
                "Created": meta.get("Created"),
                "Resolved": meta.get("Resolved"),
                "Updated": meta.get("Updated"),
                "metadata": meta,
            }
        )
        if d.source_type == "incident_reports":
            out["related_prevention_posts"] = related_prevention_posts(d.external_id, session=session)
        return out


def related_prevention_posts(fail_seq: str, *, session: Optional[Session] = None) -> list[dict[str, Any]]:
    """incident_reports 문서(SWIM 번호 fail_seq)를 가리키는 DOgether 장애예방 게시글.

    match 값: swim_ids(본문에 적힌 번호) > candidate(문맥 없는 번호 후보) > manual(사람 확정 내용 매칭)
    > content(내용으로 추정한 자동 매칭, score 포함). 한 글이 여러 경로로 걸리면 앞선 값 하나만 남긴다.
    content/manual은 dogether_swim_matches의 auto/confirmed 행만이며 review·rejected는 나오지 않는다.
    게시일시 내림차순.
    """
    fid = (fail_seq or "").strip()
    if not fid:
        return []
    sql = text(
        """
        SELECT p.external_id, p.title, p.work_type, p.source_uri,
               p.metadata->>'게시일시' AS posted_at, 'swim_ids' AS kind, NULL::float AS score
        FROM documents p
        WHERE p.source_type = 'dogether_preventive' AND p.status = 'active' AND p.metadata->'swim_ids' ? :fid
        UNION ALL
        SELECT p.external_id, p.title, p.work_type, p.source_uri,
               p.metadata->>'게시일시', 'candidate', NULL::float
        FROM documents p
        WHERE p.source_type = 'dogether_preventive' AND p.status = 'active' AND p.metadata->'swim_id_candidates' ? :fid
        UNION ALL
        SELECT p.external_id, p.title, p.work_type, p.source_uri,
               p.metadata->>'게시일시',
               CASE WHEN m.status = 'confirmed' THEN 'manual' ELSE 'content' END, m.score
        FROM dogether_swim_matches m
        JOIN documents p ON p.id = m.post_document_id
        JOIN documents i ON i.id = m.incident_document_id
        WHERE m.status IN ('auto', 'confirmed') AND p.source_type = 'dogether_preventive' AND p.status = 'active'
          AND i.source_type = 'incident_reports' AND i.external_id = :fid
        """
    )
    precedence = {"swim_ids": 0, "candidate": 1, "manual": 2, "content": 3}

    def _run(sess: Session) -> list[dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for r in sess.execute(sql, {"fid": fid}):
            cur = best.get(r.external_id)
            if cur is not None and precedence[cur["match"]] <= precedence[r.kind]:
                continue
            item = {
                "external_id": r.external_id,
                "title": r.title,
                "게시유형": r.work_type,
                "게시일시": r.posted_at,
                "source_uri": r.source_uri,
                "match": r.kind,
            }
            if r.score is not None:
                item["score"] = round(float(r.score), 4)
            best[r.external_id] = item
        return sorted(best.values(), key=lambda x: (x["게시일시"] or "", x["external_id"]), reverse=True)

    if session is not None:
        return _run(session)
    with session_scope() as sess:
        return _run(sess)


def attach_related_prevention_posts(row: dict[str, Any]) -> dict[str, Any]:
    """incident_reports 응답 dict에 related_prevention_posts를 붙인다(다른 자료종류는 그대로)."""
    if row.get("source_type") == "incident_reports" and row.get("external_id"):
        row["related_prevention_posts"] = related_prevention_posts(row["external_id"])
    return row


def list_tickets(
    *,
    source_type: str = "support_history",
    date_field: str = "Created",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    component: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
) -> dict[str, Any]:
    """List documents filtered by metadata date field in [from, to] inclusive.

    component (Jira Component, e.g. "기술지원"/"장애지원") uses the same
    substring-or-exact predicate as analytics.aggregate.aggregate_tickets so
    a list's row count and a COUNT query over the same filters agree —
    see docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §7.
    """
    date_field = resolve_date_field(source_type, date_field)
    component_q = (component or "").strip() or None
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
            if component_q:
                c = str(meta.get("Component") or "").strip()
                if c != component_q and component_q.lower() not in c.lower():
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
            "component": component_q,
            "items": page,
        }
