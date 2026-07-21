"""Checkitem (PISA) list / filter API — Phase 1 G1 table response."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.db.models import Checkitem
from app.db.session import session_scope
from app.doc_access import attach_document_access

router = APIRouter(prefix="/v1", tags=["checkitems"])


# PISA JSON field order (source: checkitem_list_KO_*.json).
# All non-empty raw keys are emitted; known keys get Korean labels.
_SECTION_SPEC: list[tuple[str, str]] = [
    ("Code", "코드"),
    ("Area", "Area"),
    ("LANG", "언어"),
    ("Cloud", "Cloud"),
    ("Category", "Category (PISA)"),
    ("Category_1", "Category"),
    ("Subcategory", "Subcategory"),
    ("Subject", "점검항목"),
    ("중요도", "중요도"),
    ("배점", "배점"),
    ("점검방법", "점검방법"),
    ("점검방법_1", "점검방법 유형"),
    ("점검기준", "점검기준"),
    ("점검결과", "점검결과"),
    ("취약시 문제점", "취약 시 문제점"),
    ("개선방안", "개선방안"),
    ("개선시기", "개선시기"),
    ("참고", "참고"),
    ("장애사례", "장애사례"),
    ("Killer Contents", "Killer Contents"),
    ("Lookin Killer", "Lookin Killer"),
    ("Lookin Service", "Lookin Service"),
    ("상호연계", "상호연계"),
    ("업무특성", "업무특성"),
    ("자체개발여부", "자체개발여부"),
]

_RAW_FALLBACK: dict[str, str] = {
    "Code": "code",
    "Area": "area",
    "LANG": "lang",
    "Category": "category",
    "Category_1": "category_1",
    "Subcategory": "subcategory",
    "Subject": "subject",
    "점검방법": "check_method",
    "점검기준": "check_criteria",
    "점검결과": "check_result",
    "취약시 문제점": "risk_if_vulnerable",
    "개선방안": "remediation",
}


def _nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and not str(v).strip():
        return False
    return True


def _build_sections(r: Checkitem) -> list[dict[str, Any]]:
    """Emit every non-empty field from raw JSON (+ column fallbacks)."""
    raw = r.raw if isinstance(r.raw, dict) else {}
    sections: list[dict[str, Any]] = []
    used: set[str] = set()

    def add(key: str, label: str, value: Any) -> None:
        if key in used or not _nonempty(value):
            return
        used.add(key)
        sections.append({"key": key, "label": label, "value": value})

    for key, label in _SECTION_SPEC:
        val = raw.get(key)
        if not _nonempty(val):
            col = _RAW_FALLBACK.get(key)
            if col:
                val = getattr(r, col, None)
        add(key, label, val)

    # remaining raw keys in source order
    for key, val in raw.items():
        if key in used:
            continue
        add(key, str(key), val)

    return sections


def _checkitem_to_dict(r: Checkitem) -> dict[str, Any]:
    """Structured checkitem row + full raw fields as sections + access URLs."""
    raw = r.raw if isinstance(r.raw, dict) else {}
    sections = _build_sections(r)
    item: dict[str, Any] = {
        "id": r.id,
        "code": r.code,
        "external_id": r.code,
        "title": f"[{r.code}] {r.subject}" if r.subject else r.code,
        "lang": r.lang,
        "area": r.area,
        "category": r.category,
        "category_1": r.category_1,
        "subcategory": r.subcategory,
        "subject": r.subject,
        "check_method": r.check_method,
        "check_criteria": r.check_criteria,
        "check_result": r.check_result,
        "risk_if_vulnerable": r.risk_if_vulnerable,
        "remediation": r.remediation,
        "importance": raw.get("중요도"),
        "score_point": raw.get("배점"),
        "cloud": raw.get("Cloud"),
        "reference": raw.get("참고"),
        "remediation_timing": raw.get("개선시기"),
        "lookin_service": raw.get("Lookin Service"),
        "document_id": r.document_id,
        "source_type": "checkitem",
        "sections": sections,
        "snippet": _snippet(r),
        "raw": raw,
    }
    return attach_document_access(item)


def _snippet(r: Checkitem) -> str:
    parts = [
        r.subject or "",
        (r.check_method or "")[:120],
        (r.check_criteria or "")[:120],
    ]
    return " · ".join(p for p in parts if p)[:400]


def list_checkitems_core(
    *,
    q: Optional[str] = None,
    area: Optional[str] = None,
    category_1: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Callable from planner (no FastAPI Query defaults)."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with session_scope() as session:
        stmt = select(Checkitem)
        if area and isinstance(area, str) and area.strip():
            stmt = stmt.where(Checkitem.area.ilike(area.strip()))
        if category_1 and isinstance(category_1, str) and category_1.strip():
            stmt = stmt.where(Checkitem.category_1.ilike(f"%{category_1.strip()}%"))
        if q and isinstance(q, str) and q.strip():
            terms = [q.strip()] + _expand_terms(q.strip())
            # de-dupe
            seen: set[str] = set()
            uniq_terms: list[str] = []
            for t in terms:
                tl = t.lower()
                if tl not in seen:
                    seen.add(tl)
                    uniq_terms.append(t)
            clauses = []
            for t in uniq_terms:
                like = f"%{t}%"
                clauses.extend(
                    [
                        Checkitem.subject.ilike(like),
                        Checkitem.code.ilike(like),
                        Checkitem.area.ilike(like),
                        Checkitem.category_1.ilike(like),
                        Checkitem.subcategory.ilike(like),
                        Checkitem.check_method.ilike(like),
                        Checkitem.check_criteria.ilike(like),
                        Checkitem.risk_if_vulnerable.ilike(like),
                        Checkitem.remediation.ilike(like),
                    ]
                )
            stmt = stmt.where(or_(*clauses))

        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(
            stmt.order_by(Checkitem.area, Checkitem.code).offset(offset).limit(limit)
        ).all()
        items = [_checkitem_to_dict(r) for r in rows]

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": items,
        "kind": "checkitems",
        "q": q,
        "area": area,
    }


@router.get("/checkitems")
def list_checkitems(
    q: Optional[str] = Query(None, description="subject/code/area substring"),
    area: Optional[str] = Query(None, description="e.g. Linux"),
    category_1: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return normalized checkitem rows with body access URLs + sections."""
    return list_checkitems_core(
        q=q, area=area, category_1=category_1, limit=limit, offset=offset
    )


@router.get("/checkitems/{code}")
def get_checkitem(code: str, lang: str = Query("KO")) -> dict[str, Any]:
    """Single checkitem by PISA code — structured fields for 원문 UI."""
    code = (code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code required")
    with session_scope() as session:
        row = session.scalars(
            select(Checkitem)
            .where(Checkitem.code == code)
            .where(Checkitem.lang == (lang or "KO").upper())
            .limit(1)
        ).first()
        if not row:
            row = session.scalars(
                select(Checkitem).where(Checkitem.code == code).limit(1)
            ).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"checkitem not found: {code}")
        return _checkitem_to_dict(row)


def _expand_terms(q: str) -> list[str]:
    low = q.lower()
    out: list[str] = []
    if "fs" in low.split() or low.strip() in {"fs", "linux fs", "linux filesystem"}:
        out.extend(["파일 시스템", "파일시스템", "filesystem", "Filesystem"])
    if "linux" in low and "파일" not in q:
        out.append("Linux")
    # OOM: keep focused — avoid bare "메모리" (too broad)
    if "oom" in low or "out of memory" in low:
        out.extend(
            [
                "OOM",
                "Out of Memory",
                "Heap Dump",
                "HeapDump",
                "kdump",
                "CommitLimit",
                "Memory Leak",
                "메모리 과다",
                "메모리 과다 사용",
                "crashkernel",
                "java.lang.OutOfMemoryError",
            ]
        )
    if "memory leak" in low or "메모리 누수" in q:
        out.extend(["Memory Leak", "메모리 누수", "leak"])
    if "kdump" in low:
        out.extend(["kdump", "crashkernel", "kexec"])
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq
