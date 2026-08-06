"""Failure-bucket flywheel: register → index → refine (self-improving)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import func, select

from app.db.models import FailureBucket
from app.db.session import session_scope
from app.failure_buckets.draft import bucket_draft, compute_confidence
from app.failure_buckets.match import rank_buckets
from app.taxonomy import _FB_DOMAIN_TO_CORPUS_DOMAIN

logger = logging.getLogger("citec.failure_buckets")

# Safety cap on match_buckets()'s SQL fetch — Python ranking runs over the
# fetched rows, so this bounds worst-case latency as bucket volume grows
# across plugins (see design doc §9).
_MATCH_FETCH_LIMIT = 500


def _known_fb_domains() -> frozenset[str]:
    """fb_domain values documented in references/failure-bucket-domains.md.

    references/ isn't copied into the API container image (see apps/api/Dockerfile),
    so this can't read that file at runtime — instead it uses
    app.taxonomy._FB_DOMAIN_TO_CORPUS_DOMAIN's keys, which both docs already
    require to be updated in lockstep with failure-bucket-domains.md whenever a
    new fb_domain is added (design doc §2/§5). Soft convention only: an
    unlisted fb_domain is still accepted, just flagged with a warning.
    """
    return frozenset(_FB_DOMAIN_TO_CORPUS_DOMAIN.keys())


def _to_dict(row: FailureBucket) -> dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "bucket_name": row.bucket_name,
        "fb_domain": row.fb_domain,
        "protocol": row.protocol,
        "evidence_ref": row.evidence_ref,
        "symptom": row.symptom,
        "discriminating_signals": list(row.discriminating_signals or []),
        "counter_signals": list(row.counter_signals or []),
        "root_cause": row.root_cause,
        "recommended_action": row.recommended_action,
        "confidence": row.confidence,
        "support_count": row.support_count,
        "counter_count": row.counter_count,
        "evidence_grade": row.evidence_grade,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _index_bucket(bucket_id: str) -> dict[str, Any]:
    """(Re)build the searchable Document mirror + embed pending chunks.

    Idempotent: upsert_document_from_draft() skips re-chunking when the
    draft's content_hash is unchanged (see app/ingest/pipeline.py), so a
    refine call that only bumps confidence/support_count is cheap.
    """
    from app.embed.job import embed_pending_chunks
    from app.ingest.pipeline import upsert_document_from_draft

    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if not row:
            raise KeyError(bucket_id)
        draft = bucket_draft(row)

    upsert = upsert_document_from_draft(draft)
    doc_id = upsert["document_id"]

    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if row and row.document_id != doc_id:
            row.document_id = doc_id
            session.flush()

    try:
        emb = embed_pending_chunks(document_id=doc_id, batch_size=16)
    except Exception as exc:  # noqa: BLE001
        logger.exception("embed failed for failure_bucket=%s doc=%s", bucket_id, doc_id)
        emb = {"error": str(exc), "embedded": 0}

    return {
        "document_id": doc_id,
        "action": upsert.get("action"),
        "embedded": emb.get("embedded", 0),
    }


_DUPLICATE_SCORE_THRESHOLD = 0.75


def create_bucket(
    *,
    bucket_name: str,
    fb_domain: str,
    symptom: str,
    discriminating_signals: list[str],
    root_cause: str,
    recommended_action: str,
    evidence_ref: str,
    counter_signals: Optional[list[str]] = None,
    protocol: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    fb_domain = fb_domain.strip().lower()
    dup_candidates = match_buckets(
        observed_signals=discriminating_signals,
        symptom=symptom,
        fb_domain=fb_domain,
        top_k=1,
    )

    with session_scope() as session:
        row = FailureBucket(
            bucket_name=bucket_name.strip(),
            fb_domain=fb_domain,
            protocol=(protocol or "").strip() or None,
            evidence_ref=evidence_ref.strip(),
            symptom=symptom or "",
            discriminating_signals=list(discriminating_signals or []),
            counter_signals=list(counter_signals or []),
            root_cause=root_cause or "",
            recommended_action=recommended_action or "",
            created_by=created_by,
        )
        session.add(row)
        session.flush()
        bucket_id = row.id

    index = _index_bucket(bucket_id)
    result = get_bucket(bucket_id)
    if result is None:
        raise RuntimeError(f"failure_bucket {bucket_id} vanished after create")
    result["index"] = index

    top_dup = dup_candidates[0] if dup_candidates else None
    if top_dup and top_dup.get("confidence", 0.0) >= _DUPLICATE_SCORE_THRESHOLD:
        result["possible_duplicate_of"] = {
            "bucket_id": top_dup.get("bucket_id"),
            "bucket_name": top_dup.get("bucket_name"),
            "confidence": top_dup.get("confidence"),
        }

    if fb_domain not in _known_fb_domains():
        result["fb_domain_warning"] = (
            f"fb_domain='{fb_domain}'는 references/failure-bucket-domains.md에 없는 새 값입니다. "
            "오타가 아니라면 문서에 추가해 주세요."
        )

    return result


def get_bucket(bucket_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        return _to_dict(row) if row else None


def list_buckets(
    *,
    fb_domain: Optional[str] = None,
    protocol: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with session_scope() as session:
        stmt = select(FailureBucket).order_by(FailureBucket.created_at.desc())
        if fb_domain:
            stmt = stmt.where(FailureBucket.fb_domain == fb_domain)
        if protocol:
            stmt = stmt.where(FailureBucket.protocol == protocol)
        if min_confidence:
            stmt = stmt.where(FailureBucket.confidence >= min_confidence)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(session.scalars(stmt.offset(offset).limit(limit)).all())
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [_to_dict(r) for r in rows],
        }


def refine_bucket(
    bucket_id: str,
    *,
    add_signal: Optional[str] = None,
    add_counter_signal: Optional[str] = None,
    confirm: bool = True,
) -> dict[str, Any]:
    signals_changed = False
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if not row:
            raise KeyError(bucket_id)

        if add_signal and add_signal not in (row.discriminating_signals or []):
            row.discriminating_signals = [*(row.discriminating_signals or []), add_signal]
            signals_changed = True
        if add_counter_signal and add_counter_signal not in (row.counter_signals or []):
            row.counter_signals = [*(row.counter_signals or []), add_counter_signal]
            signals_changed = True

        if confirm:
            row.support_count = int(row.support_count or 0) + 1
        else:
            row.counter_count = int(row.counter_count or 0) + 1
        row.confidence = compute_confidence(row.support_count, row.counter_count)
        session.flush()

    index = _index_bucket(bucket_id) if signals_changed else None
    result = get_bucket(bucket_id)
    if result is None:
        raise RuntimeError(f"failure_bucket {bucket_id} vanished after refine")
    if index:
        result["index"] = index
    return result


def match_buckets(
    *,
    observed_signals: list[str],
    symptom: str = "",
    fb_domain: Optional[str] = None,
    protocol: Optional[str] = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(FailureBucket)
        if fb_domain:
            stmt = stmt.where(FailureBucket.fb_domain == fb_domain)
        if protocol:
            stmt = stmt.where(FailureBucket.protocol == protocol)
        stmt = stmt.limit(_MATCH_FETCH_LIMIT)
        rows = list(session.scalars(stmt).all())
        dicts = [_to_dict(r) for r in rows]
    return rank_buckets(observed_signals, symptom, dicts, top_k=top_k)
