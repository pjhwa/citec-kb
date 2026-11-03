"""doc_expansion persistence. Each call opens its own short session."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, literal_column, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import DocExpansion, Document
from app.db.session import session_scope

_ID_CHUNK = 1000
_W_A = literal_column("'A'::\"char\"")
_W_C = literal_column("'C'::\"char\"")


def upsert(
    document_id: str,
    rec: dict[str, Any],
    *,
    rule: str | None,
    model: str | None,
    run_id: str | None,
    content_hash: str | None = None,
) -> None:
    summary = rec.get("summary") or ""
    keywords = list(rec.get("keywords") or [])
    grounded = list(rec.get("grounded") or [])
    # Keywords are the high-signal part (weight A, like a title); the summary is
    # context (C). Without setweight ts_rank_cd bottoms out near 0.1 and the
    # branch can never compete with body hits.
    kw_text = " ".join(keywords)
    grounded_text = " ".join(grounded)
    values = {
        "document_id": document_id,
        "summary": summary,
        "keywords": keywords,
        "grounded": grounded,
        "tsv_all": func.setweight(func.to_tsvector("simple", kw_text), _W_A).op("||")(
            func.setweight(func.to_tsvector("simple", summary), _W_C)
        ),
        "tsv_grounded": func.setweight(func.to_tsvector("simple", grounded_text), _W_A),
        "source_content_hash": content_hash,
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
            "source_content_hash": row.source_content_hash,
            "created_at": row.created_at,
        }


def count() -> int:
    with session_scope() as session:
        return int(session.execute(select(func.count()).select_from(DocExpansion)).scalar_one())


def existing_ids(ids: Iterable[str], hashes: dict[str, str | None] | None = None) -> set[str]:
    """Ids that already have an up-to-date expansion.

    With ``hashes`` ({document_id: current content_hash}) a row whose
    source_content_hash differs counts as missing so the run regenerates it.
    """
    wanted = list(ids)
    found: set[str] = set()
    with session_scope() as session:
        for i in range(0, len(wanted), _ID_CHUNK):
            part = wanted[i : i + _ID_CHUNK]
            for did, h in session.execute(
                select(DocExpansion.document_id, DocExpansion.source_content_hash).where(
                    DocExpansion.document_id.in_(part)
                )
            ):
                if hashes is None or h == hashes.get(did):
                    found.add(did)
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


def iter_export(batch: int = 500) -> Iterable[dict[str, Any]]:
    """Every expansion joined with its document, keyset-paged by document_id (one short session per batch)."""
    last = ""
    while True:
        with session_scope() as session:
            rows = session.execute(
                select(
                    DocExpansion.document_id, Document.external_id, Document.source_type, Document.title,
                    Document.content_hash, DocExpansion.summary, DocExpansion.keywords,
                    DocExpansion.grounded, DocExpansion.rule, DocExpansion.model, DocExpansion.run_id,
                )
                .join(Document, Document.id == DocExpansion.document_id)
                .where(DocExpansion.document_id > last)
                .order_by(DocExpansion.document_id)
                .limit(batch)
            ).all()
        if not rows:
            return
        for r in rows:
            yield {
                "external_id": r.external_id, "source_type": r.source_type, "title": r.title,
                "content_hash": r.content_hash, "summary": r.summary, "keywords": list(r.keywords or []),
                "grounded": list(r.grounded or []), "rule": r.rule, "model": r.model, "run_id": r.run_id,
            }
        last = rows[-1].document_id


def find_active_documents(keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """{(source_type, external_id): {id, content_hash}} for active documents."""
    wanted = list(keys)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with session_scope() as session:
        for i in range(0, len(wanted), _ID_CHUNK):
            part = wanted[i : i + _ID_CHUNK]
            for r in session.execute(
                select(Document.id, Document.source_type, Document.external_id, Document.content_hash).where(
                    tuple_(Document.source_type, Document.external_id).in_(part), Document.status == "active"
                )
            ):
                out[(r.source_type, r.external_id)] = {"id": r.id, "content_hash": r.content_hash}
    return out
