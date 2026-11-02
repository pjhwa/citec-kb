"""doc_expansion persistence. Each call opens its own short session."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import DocExpansion
from app.db.session import session_scope

_ID_CHUNK = 1000


def upsert(document_id: str, rec: dict[str, Any], *, rule: str | None, model: str | None, run_id: str | None) -> None:
    summary = rec.get("summary") or ""
    keywords = list(rec.get("keywords") or [])
    grounded = list(rec.get("grounded") or [])
    all_text = " ".join([summary, *keywords]).strip()
    grounded_text = " ".join(grounded)
    values = {
        "document_id": document_id,
        "summary": summary,
        "keywords": keywords,
        "grounded": grounded,
        "tsv_all": func.to_tsvector("simple", all_text),
        "tsv_grounded": func.to_tsvector("simple", grounded_text),
        "rule": rule,
        "model": model,
        "run_id": run_id,
        "created_at": func.now(),
    }
    stmt = pg_insert(DocExpansion).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DocExpansion.document_id],
        set_={k: v for k, v in values.items() if k != "document_id"},
    )
    with session_scope() as session:
        session.execute(stmt)


def get(document_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(DocExpansion, document_id)
        if row is None:
            return None
        return {
            "document_id": row.document_id,
            "summary": row.summary,
            "keywords": list(row.keywords or []),
            "grounded": list(row.grounded or []),
            "rule": row.rule,
            "model": row.model,
            "run_id": row.run_id,
            "created_at": row.created_at,
        }


def count() -> int:
    with session_scope() as session:
        return int(session.execute(select(func.count()).select_from(DocExpansion)).scalar_one())


def existing_ids(ids: Iterable[str]) -> set[str]:
    wanted = list(ids)
    found: set[str] = set()
    with session_scope() as session:
        for i in range(0, len(wanted), _ID_CHUNK):
            part = wanted[i : i + _ID_CHUNK]
            found.update(
                r[0] for r in session.execute(select(DocExpansion.document_id).where(DocExpansion.document_id.in_(part)))
            )
    return found


def matches(document_id: str, query: str, *, column: str = "tsv_all") -> bool:
    if column not in ("tsv_all", "tsv_grounded"):
        raise ValueError(column)
    col = getattr(DocExpansion, column)
    stmt = select(func.count()).select_from(DocExpansion).where(
        DocExpansion.document_id == document_id, col.op("@@")(func.plainto_tsquery("simple", query))
    )
    with session_scope() as session:
        return session.execute(stmt).scalar_one() > 0


def stats() -> dict[str, Any]:
    """Totals for the CLI: count, per-rule counts, grounded-ratio buckets."""
    with session_scope() as session:
        rows = session.execute(
            select(DocExpansion.rule, DocExpansion.keywords, DocExpansion.grounded)
        ).all()
    by_rule: dict[str, int] = {}
    buckets = {"0": 0, "0-0.5": 0, "0.5-1": 0, "1": 0}
    for rule, kws, gr in rows:
        by_rule[rule or ""] = by_rule.get(rule or "", 0) + 1
        ratio = (len(gr or []) / len(kws)) if kws else 0.0
        if ratio == 0:
            buckets["0"] += 1
        elif ratio < 0.5:
            buckets["0-0.5"] += 1
        elif ratio < 1:
            buckets["0.5-1"] += 1
        else:
            buckets["1"] += 1
    return {"count": len(rows), "by_rule": by_rule, "grounded_ratio": buckets}
