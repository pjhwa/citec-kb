"""DB access for search_synonym_candidate."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import SearchSynonymCandidate
from app.db.session import session_scope
from app.synonyms.rules import clean_terms, terms_key

# action -> (allowed source statuses, new status)
_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "approve": (frozenset({"proposed", "disabled"}), "approved"),
    "reject": (frozenset({"proposed"}), "rejected"),
    "disable": (frozenset({"approved"}), "disabled"),
}


class NotFound(LookupError):
    pass


class TransitionError(ValueError):
    pass


def _row(r: SearchSynonymCandidate) -> dict[str, Any]:
    return {
        "id": r.id,
        "terms": list(r.terms or []),
        "status": r.status,
        "evidence": r.evidence or {},
        "run_id": r.run_id,
        "model": r.model,
        "decided_by": r.decided_by,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def upsert_candidates(
    items: list[dict[str, Any]], *, run_id: str | None, model: str | None
) -> dict[str, int]:
    """Insert new proposed candidates; existing terms_key rows are left untouched."""
    inserted = skipped = invalid = 0
    with session_scope() as session:
        for it in items:
            terms = clean_terms(list(it.get("terms") or []))
            if len(terms) < 2:
                invalid += 1
                continue
            stmt = (
                pg_insert(SearchSynonymCandidate)
                .values(
                    terms=terms,
                    terms_key=terms_key(terms),
                    status="proposed",
                    evidence=it.get("evidence") or {},
                    run_id=run_id,
                    model=model,
                )
                .on_conflict_do_nothing(constraint="uq_search_synonym_candidate_terms_key")
                .returning(SearchSynonymCandidate.id)
            )
            # rowcount is unreliable for ORM-enabled ON CONFLICT DO NOTHING; RETURNING
            # yields a row only when the insert actually happened.
            if session.execute(stmt).scalar_one_or_none() is not None:
                inserted += 1
            else:
                skipped += 1
    return {"inserted": inserted, "skipped": skipped, "invalid": invalid}


def list_candidates(
    *, status: str | None = None, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    with session_scope() as session:
        q = select(SearchSynonymCandidate)
        cq = select(func.count()).select_from(SearchSynonymCandidate)
        if status:
            q = q.where(SearchSynonymCandidate.status == status)
            cq = cq.where(SearchSynonymCandidate.status == status)
        total = int(session.scalar(cq) or 0)
        rows = session.scalars(
            q.order_by(SearchSynonymCandidate.id).limit(limit).offset(offset)
        ).all()
        return {"total": total, "items": [_row(r) for r in rows]}


def set_status(cid: int, action: str, *, decided_by: str, note: str | None = None) -> dict[str, Any]:
    allowed, new_status = _TRANSITIONS[action]
    with session_scope() as session:
        r = session.get(SearchSynonymCandidate, cid)
        if r is None:
            raise NotFound(f"candidate {cid} not found")
        if r.status not in allowed:
            raise TransitionError(f"cannot {action} a {r.status} candidate")
        r.status = new_status
        r.decided_by = decided_by
        r.decided_at = datetime.now(timezone.utc)
        if note is not None:
            r.note = note
        session.flush()
        return _row(r)


def load_groups(*, include_proposed: bool = False) -> list[list[str]]:
    statuses = ["approved"] + (["proposed"] if include_proposed else [])
    with session_scope() as session:
        rows = session.scalars(
            select(SearchSynonymCandidate)
            .where(SearchSynonymCandidate.status.in_(statuses))
            .order_by(SearchSynonymCandidate.id)
        ).all()
        return [list(r.terms) for r in rows]


def existing_keys() -> set[str]:
    with session_scope() as session:
        return set(session.scalars(select(SearchSynonymCandidate.terms_key)).all())
