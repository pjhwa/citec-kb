"""Metadata analytics — SQL/Python counts only (no LLM)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any, Optional

from sqlalchemy import select

from app.analytics.issue_type import classify_issue_type
from app.db.models import Document
from app.db.session import session_scope
from app.doc_access import attach_document_access
from app.tickets.query import parse_meta_date, resolve_date_field

_GROUP_BY = frozenset(
    {"year", "month", "component", "status", "assignee", "total", "issue_type"}
)


def _bucket_key(
    group_by: str,
    meta: dict[str, Any],
    dt: Optional[date],
    *,
    title: str = "",
    body_md: str = "",
) -> str:
    if group_by == "year":
        return str(dt.year) if dt else "(unknown)"
    if group_by == "month":
        return f"{dt.year}-{dt.month:02d}" if dt else "(unknown)"
    if group_by == "component":
        v = meta.get("Component")
        return str(v).strip() if v not in (None, "", "-") else "(empty)"
    if group_by == "status":
        v = meta.get("Status")
        return str(v).strip() if v else "(empty)"
    if group_by == "assignee":
        v = meta.get("Assignee")
        return str(v).strip() if v else "(empty)"
    if group_by == "issue_type":
        return classify_issue_type(title, body_md)
    return "total"


def _sample_row(
    *,
    document_id: str,
    external_id: str,
    title: str,
    body_md: str,
    meta: dict[str, Any],
    dt: Optional[date],
    source_type: str = "support_history",
) -> dict[str, Any]:
    body = (body_md or "").strip()
    row = {
        "document_id": document_id,
        "external_id": external_id,
        "title": title or "",
        "component": str(meta.get("Component") or "").strip() or None,
        "source_type": source_type or "support_history",
        "issue_type": classify_issue_type(title, body_md),
        "status": str(meta.get("Status") or "").strip() or None,
        "assignee": str(meta.get("Assignee") or "").strip() or None,
        "Created": meta.get("Created"),
        "Resolved": meta.get("Resolved"),
        "date": dt.isoformat() if dt else None,
        "body_preview": body[:1200] + ("…" if len(body) > 1200 else ""),
    }
    return attach_document_access(row)


def aggregate_tickets(
    *,
    source_type: str = "support_history",
    group_by: str = "year",
    date_field: str = "Created",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    component: Optional[str] = None,
    entity: Optional[str] = None,
    top_k: int = 50,
    include_samples: bool = False,
    sample_limit: int = 8,
) -> dict[str, Any]:
    """Aggregate support tickets by metadata field. Numbers are SQL/Python only."""
    gb = (group_by or "year").lower().strip()
    if gb not in _GROUP_BY:
        gb = "year"
    date_field = resolve_date_field(source_type, date_field)
    top_k = max(1, min(int(top_k), 200))
    sample_limit = max(1, min(int(sample_limit), 30))
    entity_q = (entity or "").strip().lower() or None
    component_q = (component or "").strip() or None

    # Materialize fields inside the session to avoid DetachedInstanceError.
    rows: list[tuple[dict[str, Any], Optional[str], Optional[str], str, str]] = []
    with session_scope() as session:
        stmt = (
            select(Document)
            .where(Document.status == "active")
            .where(Document.source_type == source_type)
        )
        for d in session.scalars(stmt).all():
            meta = d.metadata_ if isinstance(d.metadata_, dict) else {}
            rows.append(
                (dict(meta), d.title, d.external_id, d.id, d.body_md or "")
            )

    counter: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    for meta, title, external_id, doc_id, body_md in rows:
        raw = meta.get(date_field)
        dt = parse_meta_date(raw if isinstance(raw, str) else None)
        # Date filter only when range given; undated rows excluded from ranged queries
        if date_from or date_to:
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
        if entity_q:
            blob = f"{title or ''} {external_id or ''} {(body_md or '')[:1500]}".lower()
            if entity_q not in blob:
                continue
        total += 1
        if gb == "total":
            if include_samples and len(samples["total"]) < sample_limit:
                samples["total"].append(
                    _sample_row(
                        document_id=doc_id,
                        external_id=external_id or "",
                        title=title or "",
                        body_md=body_md,
                        meta=meta,
                        dt=dt,
                        source_type=source_type,
                    )
                )
            continue
        key = _bucket_key(
            gb, meta, dt, title=title or "", body_md=body_md or ""
        )
        counter[key] += 1
        if include_samples and len(samples[key]) < sample_limit:
            samples[key].append(
                _sample_row(
                    document_id=doc_id,
                    external_id=external_id or "",
                    title=title or "",
                    body_md=body_md,
                    meta=meta,
                    dt=dt,
                    source_type=source_type,
                )
            )

    if gb == "total":
        buckets = [
            {
                "key": "total",
                "count": total,
                "share": 1.0 if total else 0.0,
                "samples": samples.get("total", []) if include_samples else [],
            }
        ]
    else:
        items = counter.most_common(top_k)
        buckets = [
            {
                "key": k,
                "count": n,
                "share": round(n / total, 4) if total else 0.0,
                "samples": samples.get(k, []) if include_samples else [],
            }
            for k, n in items
        ]

    return {
        "source_type": source_type,
        "group_by": gb,
        "date_field": date_field,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "component": component_q,
        "entity": entity_q,
        "total": total,
        "buckets": buckets,
        "include_samples": include_samples,
        "method": (
            "issue_type_rules"
            if gb == "issue_type"
            else "metadata_aggregate"
        ),
        "llm_used": False,
    }


def entity_share(
    *,
    entity: str,
    source_type: str = "support_history",
    date_field: str = "Created",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict[str, Any]:
    """Share of tickets mentioning an entity within the same date range.

    분자(matched)와 분모(total)는 둘 다 ``aggregate_tickets``로 센다 — 같은 소스
    타입·같은 날짜 범위·``entity`` 부분일치(제목·external_id·본문 앞 1500자)라서
    ``matched <= total``이 항상 성립하고 ``kb_analytics(entity=…)``와 건수가 같다.

    예전에는 ``document_entities`` 링크 수(기간 필터 없는 전체 활성 문서)를 분자로
    쓰고 분모만 기간으로 좁혀 점유율이 1을 넘었다(2026-10-28 SCP 올해 425/278).
    링크 기반 수는 날짜 메타와 정의가 달라 같은 엔티티가 도구마다 다른 건수를
    냈으므로 쓰지 않는다(``_entity_share_from_links``는 참고용으로만 남김).
    """
    entity = (entity or "").strip()
    if not entity:
        return {
            "entity": entity,
            "matched": 0,
            "total": 0,
            "share": 0.0,
            "llm_used": False,
        }

    scope = dict(
        source_type=source_type,
        group_by="total",
        date_field=date_field,
        date_from=date_from,
        date_to=date_to,
    )
    base = aggregate_tickets(**scope)
    matched = aggregate_tickets(**scope, entity=entity)
    tot = int(base["total"])
    m = int(matched["total"])
    if m > tot:  # 같은 범위의 부분집합이므로 불가능 — 깨지면 조용히 넘기지 않는다
        raise RuntimeError(f"entity_share invariant violated: matched={m} > total={tot}")
    return {
        "entity": entity,
        "matched": m,
        "total": tot,
        "share": round(m / tot, 4) if tot else 0.0,
        "source_type": source_type,
        "date_field": matched.get("date_field") or date_field,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "method": "title_entity_scan",
        "definition": (
            "matched = 같은 기간·소스의 문서 중 제목/ID/본문 앞 1500자에 엔티티 문자열이 "
            "포함된 건수(kb_analytics entity=와 동일), total = 같은 기간·소스의 전체 문서 수"
        ),
        "llm_used": False,
    }


def _entity_share_from_links(
    entity: str,
    *,
    source_type: str = "support_history",
) -> Optional[dict[str, Any]]:
    """Return matched count via entities/document_entities if seed present."""
    from sqlalchemy import func, select

    from app.db.models import Document, DocumentEntity, Entity

    needle = entity.strip().lower()
    with session_scope() as session:
        ent_count = session.scalar(select(func.count()).select_from(Entity)) or 0
        if int(ent_count) == 0:
            return None
        # match id, canonical, or alias
        ents = list(session.scalars(select(Entity)).all())
        eid = None
        for e in ents:
            aliases = [a.lower() for a in (e.aliases or [])]
            if (
                e.id.lower() == needle
                or e.id.lower().endswith(":" + needle)
                or (e.canonical_name or "").lower() == needle
                or needle in aliases
            ):
                eid = e.id
                break
        if not eid:
            return None
        stmt = (
            select(func.count())
            .select_from(DocumentEntity)
            .join(Document, Document.id == DocumentEntity.document_id)
            .where(DocumentEntity.entity_id == eid)
            .where(Document.status == "active")
        )
        if source_type:
            stmt = stmt.where(Document.source_type == source_type)
        m = int(session.scalar(stmt) or 0)
        return {"entity_id": eid, "matched": m}

