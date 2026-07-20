"""Checkitem (PISA) list / filter API — Phase 1 G1 table response."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.db.models import Checkitem
from app.db.session import session_scope

router = APIRouter(prefix="/v1", tags=["checkitems"])


@router.get("/checkitems")
def list_checkitems(
    q: Optional[str] = Query(None, description="subject/code/area substring"),
    area: Optional[str] = Query(None, description="e.g. Linux"),
    category_1: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return normalized checkitem rows (table-friendly)."""
    with session_scope() as session:
        stmt = select(Checkitem)
        if area:
            stmt = stmt.where(Checkitem.area.ilike(area.strip()))
        if category_1:
            stmt = stmt.where(Checkitem.category_1.ilike(f"%{category_1.strip()}%"))
        if q and q.strip():
            term = f"%{q.strip()}%"
            # Expand short English aliases for FS-style queries
            extras = _expand_terms(q.strip())
            clauses = [
                Checkitem.subject.ilike(term),
                Checkitem.code.ilike(term),
                Checkitem.area.ilike(term),
                Checkitem.category_1.ilike(term),
                Checkitem.subcategory.ilike(term),
            ]
            for e in extras:
                like = f"%{e}%"
                clauses.extend(
                    [
                        Checkitem.subject.ilike(like),
                        Checkitem.category_1.ilike(like),
                        Checkitem.subcategory.ilike(like),
                    ]
                )
            stmt = stmt.where(or_(*clauses))

        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(
            stmt.order_by(Checkitem.area, Checkitem.code).offset(offset).limit(limit)
        ).all()

        items = [
            {
                "id": r.id,
                "code": r.code,
                "lang": r.lang,
                "area": r.area,
                "category": r.category,
                "category_1": r.category_1,
                "subcategory": r.subcategory,
                "subject": r.subject,
                "check_method": r.check_method,
                "check_criteria": r.check_criteria,
                "risk_if_vulnerable": r.risk_if_vulnerable,
                "remediation": r.remediation,
                "document_id": r.document_id,
            }
            for r in rows
        ]

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def _expand_terms(q: str) -> list[str]:
    low = q.lower()
    out: list[str] = []
    if "fs" in low.split() or low.strip() in {"fs", "linux fs", "linux filesystem"}:
        out.extend(["파일 시스템", "파일시스템", "filesystem", "Filesystem"])
    if "linux" in low and "파일" not in q:
        out.append("Linux")
    # de-dupe
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq
