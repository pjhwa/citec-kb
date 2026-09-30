"""Batch extract issue frames for support_history documents."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.db.models import Document, IssueFrame
from app.db.session import session_scope
from app.frames.citec_taxonomy import classify_severity_tier, tag_citec_domains
from app.frames.extract import EXTRACTOR_VERSION, body_hash, extract_frame_from_markdown

logger = logging.getLogger("citec.frames.job")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def extract_frames(
    *,
    source_type: str = "support_history",
    limit: Optional[int] = None,
    min_quality: float = 0.0,
    force: bool = False,
) -> dict[str, Any]:
    """Idempotent upsert of issue_frames for documents.

    P0-C (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §6):
    when force=False this used to skip any document that already had a frame
    row, full stop — so a frame extracted once (e.g. from a "검토 중" summary)
    was never revisited even after body_md changed to a resolved state
    (frames/job.py:64-66 in the review's line numbering). It now skips a
    document only when its existing frame's body_hash AND extractor_version
    still match the current body_md/extractor — i.e. "already have a frame"
    is replaced by "already have a frame derived from this exact content".
    force=True still unconditionally regenerates every matched document
    regardless of hash/version, as before.

    Cost note: detecting a body_md content change requires computing
    body_hash() for every candidate row, which requires fetching its
    body_md — there is no way to push that comparison into SQL without a
    DB-maintained content hash column on Document itself (out of scope for
    this pass). So this now fetches body_md for every active
    source_type-matched document on every call, then applies `limit` in
    Python (previously `limit` was a SQL LIMIT applied to the
    frame-doesn't-exist-yet subset, which was cheap but is no longer
    correct — see the module's P0-C history). A caller doing many small
    `limit=N` batches to sweep the whole corpus therefore re-fetches the
    same full body_md set on every batch (O(passes × corpus) row reads,
    not O(corpus)); prefer a single `limit=None` pass over repeated small
    batches when running a full sweep.
    """
    stats: dict[str, Any] = {
        "processed": 0,
        "upserted": 0,
        "regenerated_stale": 0,
        "skipped_fresh": 0,
        "skipped_low_quality": 0,
        "errors": 0,
        "source_type": source_type,
        "extractor_version": EXTRACTOR_VERSION,
    }

    with session_scope() as session:
        stmt = (
            select(
                Document.id,
                Document.external_id,
                Document.title,
                Document.body_md,
                Document.environment,
                IssueFrame.id.label("frame_id"),
                IssueFrame.body_hash.label("frame_body_hash"),
                IssueFrame.extractor_version.label("frame_extractor_version"),
            )
            .outerjoin(IssueFrame, IssueFrame.document_id == Document.id)
            .where(Document.source_type == source_type)
            .where(Document.status == "active")
            .order_by(Document.external_id)
        )
        if source_type == "support_history":
            # Jira export raw/ mixes in non-ticket markdown (e.g. 부서 소개) that
            # doesn't have a CITECTS-nnnn key — skip it. Other source_types (e.g.
            # incident_reports/SWIM) are uniformly ticket-shaped already, so this
            # filter would otherwise wrongly exclude every one of their documents
            # (their external_id is a numeric failSeq, never "CITECTS-...").
            stmt = stmt.where(Document.external_id.like("CITECTS-%"))
        rows = list(session.execute(stmt).all())

    docs = []
    for row in rows:
        if not force and row.frame_id is not None:
            current_hash = body_hash(row.body_md or "")
            if (
                row.frame_body_hash == current_hash
                and row.frame_extractor_version == EXTRACTOR_VERSION
            ):
                stats["skipped_fresh"] += 1
                continue
        docs.append(row)
    if limit:
        docs = docs[: int(limit)]

    for doc in docs:
        stats["processed"] += 1
        try:
            extracted = extract_frame_from_markdown(
                doc.body_md or "",
                title=doc.title or "",
                environment=doc.environment,
            )
            q = float(extracted.get("quality") or 0.0)
            if q < min_quality:
                stats["skipped_low_quality"] += 1
                continue
            with session_scope() as session:
                frame = session.scalar(
                    select(IssueFrame).where(IssueFrame.document_id == doc.id)
                )
                if frame is None:
                    frame = IssueFrame(id=str(uuid.uuid4()), document_id=doc.id)
                    session.add(frame)
                else:
                    stats["regenerated_stale"] += 1
                frame.symptom = extracted.get("symptom")
                frame.root_cause = extracted.get("root_cause")
                frame.resolution = extracted.get("resolution")
                frame.workaround = extracted.get("workaround")
                frame.components = list(extracted.get("components") or [])
                frame.environment = extracted.get("environment")
                frame.commands = list(extracted.get("commands") or [])
                frame.quality = q
                frame.raw_extract = extracted.get("raw_extract") or {}
                frame.body_hash = extracted.get("body_hash")
                frame.extractor_version = extracted.get("extractor_version")
                frame.updated_at = _now()
                stats["upserted"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("frame extract failed doc=%s", doc.external_id)
            stats["errors"] += 1

    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(IssueFrame)) or 0
        avg_q = session.scalar(select(func.avg(IssueFrame.quality))) or 0.0
        with_both = session.scalar(
            select(func.count())
            .select_from(IssueFrame)
            .where(IssueFrame.root_cause.is_not(None))
            .where(IssueFrame.resolution.is_not(None))
        ) or 0
        stats["frames_total"] = int(total)
        stats["avg_quality"] = round(float(avg_q), 3)
        stats["with_cause_and_resolution"] = int(with_both)

    logger.info("frame extract done %s", stats)
    return stats


def extract_citec_domains(
    *,
    source_type: str = "incident_reports",
    limit: Optional[int] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Idempotent upsert of issue_frames.citec_domains/severity_tier — the
    CI-TEC 11-domain dashboard lens (app.frames.citec_taxonomy), separate
    from extract_frames()'s general symptom/root_cause/components slots.

    Creates the issue_frames row if extract_frames() hasn't run for a
    document yet (rather than requiring it first) — only citec_domains/
    severity_tier are touched either way; a pre-existing row's
    symptom/root_cause/... are left untouched.

    "Already processed" is tracked via severity_tier IS NOT NULL, not via
    citec_domains being non-empty — classify_severity_tier() always
    returns a string (worst case "unknown"), so it's an unambiguous
    processed marker. citec_domains=[] alone can't serve that role: an
    incident with genuinely zero matching CI-TEC domains would otherwise
    look identical to "not yet computed" and get rescanned every run
    (force=False would never skip it).

    severity_tier is meaningful for SWIM's 최종등급 specifically — running
    this against a non-incident_reports source_type will tag citec_domains
    correctly but severity_tier will just be "unknown" for every row
    (no 최종등급 field to read).
    """
    stats: dict[str, Any] = {
        "processed": 0,
        "upserted": 0,
        "skipped_existing": 0,
        "errors": 0,
        "source_type": source_type,
    }

    with session_scope() as session:
        stmt = (
            select(
                Document.id,
                Document.external_id,
                Document.title,
                Document.body_md,
                Document.metadata_,
            )
            .where(Document.source_type == source_type)
            .where(Document.status == "active")
            .order_by(Document.external_id)
        )
        if not force:
            already_tagged = select(IssueFrame.document_id).where(
                IssueFrame.severity_tier.is_not(None)
            )
            stmt = stmt.where(Document.id.not_in(already_tagged))
        if limit:
            stmt = stmt.limit(limit)
        docs = list(session.execute(stmt).all())

    for doc in docs:
        stats["processed"] += 1
        try:
            meta = doc.metadata_ or {}
            domains = tag_citec_domains(doc.body_md or "", title=doc.title or "")
            tier = classify_severity_tier(meta.get("최종등급"))
            with session_scope() as session:
                frame = session.scalar(
                    select(IssueFrame).where(IssueFrame.document_id == doc.id)
                )
                if frame is None:
                    frame = IssueFrame(id=str(uuid.uuid4()), document_id=doc.id)
                    session.add(frame)
                elif not force and frame.severity_tier is not None:
                    # Race: another process tagged it between the SELECT
                    # above and this upsert — don't clobber.
                    stats["skipped_existing"] += 1
                    continue
                frame.citec_domains = domains
                frame.severity_tier = tier
                frame.updated_at = _now()
                stats["upserted"] += 1
        except Exception:  # noqa: BLE001 — one bad document must not kill the batch
            logger.exception("citec domain tag failed doc=%s", doc.external_id)
            stats["errors"] += 1

    with session_scope() as session:
        by_domain: dict[str, int] = {}
        rows = session.execute(
            select(IssueFrame.citec_domains).where(IssueFrame.severity_tier.is_not(None))
        ).all()
        for (domains,) in rows:
            for d in domains or []:
                by_domain[d] = by_domain.get(d, 0) + 1
        by_tier: dict[str, int] = {}
        for (tier,) in session.execute(
            select(IssueFrame.severity_tier).where(IssueFrame.severity_tier.is_not(None))
        ).all():
            by_tier[tier] = by_tier.get(tier, 0) + 1
        stats["by_domain"] = by_domain
        stats["by_severity_tier"] = by_tier

    logger.info("citec domain tag done %s", stats)
    return stats
