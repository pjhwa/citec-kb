"""Batch extract issue frames for support_history documents."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.db.models import Document, IssueFrame
from app.db.session import session_scope
from app.frames.extract import extract_frame_from_markdown

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

    When force=False, skips documents that already have a frame.
    """
    stats: dict[str, Any] = {
        "processed": 0,
        "upserted": 0,
        "skipped_existing": 0,
        "skipped_low_quality": 0,
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
                Document.environment,
            )
            .where(Document.source_type == source_type)
            .where(Document.status == "active")
            # Skip non-ticket markdown (e.g. 부서 소개)
            .where(Document.external_id.like("CITECTS-%"))
            .order_by(Document.external_id)
        )
        if not force:
            existing = select(IssueFrame.document_id)
            stmt = stmt.where(Document.id.not_in(existing))
        if limit:
            stmt = stmt.limit(limit)
        docs = list(session.execute(stmt).all())

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
                frame.symptom = extracted.get("symptom")
                frame.root_cause = extracted.get("root_cause")
                frame.resolution = extracted.get("resolution")
                frame.workaround = extracted.get("workaround")
                frame.components = list(extracted.get("components") or [])
                frame.environment = extracted.get("environment")
                frame.commands = list(extracted.get("commands") or [])
                frame.quality = q
                frame.raw_extract = extracted.get("raw_extract") or {}
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
