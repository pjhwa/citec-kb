"""Ingest pipeline: adapters → documents/chunks/checkitems upsert."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Checkitem, Chunk, Document, DocumentSection, IngestJob, Source
from app.db.session import session_scope
from app.ingest.adapters import DocumentDraft, iter_all
from app.ingest.chunking import chunk_markdown

logger = logging.getLogger("citec.ingest")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _doc_id(source_type: str, external_id: str) -> str:
    # stable id for upsert readability
    safe = re.sub(r"[^a-zA-Z0-9._:-]+", "_", f"{source_type}:{external_id}")
    return safe[:64]


def _ensure_source(session: Session, source_id: str = "fs_raw") -> None:
    src = session.get(Source, source_id)
    if not src:
        session.add(
            Source(
                id=source_id,
                type="fs_raw",
                name="Local raw corpus",
                config={"path": "/data/raw"},
                status="active",
            )
        )


def _upsert_document(session: Session, draft: DocumentDraft, source_id: str = "fs_raw") -> str:
    """Insert or update document; rechunk when content_hash changes. Returns action."""
    existing = session.scalar(
        select(Document).where(
            Document.source_type == draft.source_type,
            Document.external_id == draft.external_id,
        )
    )
    if existing and existing.content_hash == draft.content_hash:
        return "skipped"

    if existing:
        doc = existing
        doc.title = draft.title
        doc.body_md = draft.body_md
        doc.metadata_ = draft.metadata
        doc.content_hash = draft.content_hash
        doc.version = int(doc.version or 1) + 1
        doc.status = "active"
        doc.source_uri = draft.source_uri
        doc.evidence_grade = draft.evidence_grade
        doc.environment = draft.environment
        doc.domain = draft.domain
        doc.work_type = draft.work_type
        doc.path_l2 = draft.path_l2
        doc.path_l3 = draft.path_l3
        doc.updated_at = _now()
        action = "updated"
        # soft-deactivate old chunks
        for ch in list(doc.chunks):
            ch.is_active = False
        # remove sections (cascade chunks FK set null / we delete sections)
        for sec in list(doc.sections):
            session.delete(sec)
        session.flush()
    else:
        doc = Document(
            id=_doc_id(draft.source_type, draft.external_id),
            source_id=source_id,
            source_type=draft.source_type,
            external_id=draft.external_id,
            title=draft.title,
            body_md=draft.body_md,
            metadata_=draft.metadata,
            content_hash=draft.content_hash,
            version=1,
            status="active",
            source_uri=draft.source_uri,
            evidence_grade=draft.evidence_grade,
            environment=draft.environment,
            domain=draft.domain,
            work_type=draft.work_type,
            path_l2=draft.path_l2,
            path_l3=draft.path_l3,
        )
        # handle id collision with different external
        if session.get(Document, doc.id):
            doc.id = str(uuid.uuid4())
        session.add(doc)
        session.flush()
        action = "inserted"

    header = f"[{draft.source_type} | {draft.external_id} | {draft.title[:80]}]"
    drafts = chunk_markdown(draft.body_md, doc_header=header)

    # one section per unique path (simple)
    section_ids: dict[str, str] = {}
    for i, cd in enumerate(drafts):
        sp = cd.section_path or ""
        if sp not in section_ids:
            sec = DocumentSection(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                heading_path=(sp or "")[:2000],
                level=1 if sp else 0,
                body_md="",
                token_count=0,
                ordinal=len(section_ids),
            )
            session.add(sec)
            session.flush()
            section_ids[sp] = sec.id
        chunk = Chunk(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            section_id=section_ids[sp],
            ordinal=cd.ordinal,
            text=cd.text,
            header_context=cd.header_context,
            token_count=cd.token_count,
            is_active=True,
        )
        session.add(chunk)
        session.flush()
        session.execute(
            text(
                "UPDATE chunks SET tsv = to_tsvector('simple', :t) WHERE id = :id"
            ),
            {"t": f"{cd.header_context}\n{cd.text}", "id": chunk.id},
        )

    # checkitems dual-write
    if draft.source_type == "checkitem":
        _upsert_checkitem(session, draft, doc.id)

    return action


def _upsert_checkitem(session: Session, draft: DocumentDraft, document_id: str) -> None:
    item = draft.metadata or {}
    code = draft.external_id
    lang = str(item.get("LANG") or "KO")
    existing = session.scalar(
        select(Checkitem).where(Checkitem.code == code, Checkitem.lang == lang)
    )
    fields = dict(
        area=str(item.get("Area") or ""),
        category=str(item.get("Category") or "") or None,
        category_1=str(item.get("Category_1") or "") or None,
        subcategory=str(item.get("Subcategory") or "") or None,
        subject=str(item.get("Subject") or draft.title),
        check_method=item.get("점검방법"),
        check_criteria=item.get("점검기준"),
        check_result=item.get("점검결과"),
        risk_if_vulnerable=item.get("취약시 문제점"),
        remediation=item.get("개선방안"),
        raw=item,
        document_id=document_id,
    )
    blob = " ".join(
        str(x or "")
        for x in [
            code,
            fields["subject"],
            fields["area"],
            fields["category_1"],
            fields["check_method"],
            fields["check_criteria"],
            fields["remediation"],
        ]
    )
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        session.flush()
        session.execute(
            text("UPDATE checkitems SET tsv = to_tsvector('simple', :t) WHERE id = :id"),
            {"t": blob, "id": existing.id},
        )
    else:
        ci = Checkitem(id=str(uuid.uuid4()), code=code, lang=lang, **fields)
        session.add(ci)
        session.flush()
        session.execute(
            text("UPDATE checkitems SET tsv = to_tsvector('simple', :t) WHERE id = :id"),
            {"t": blob, "id": ci.id},
        )


def run_ingest(
    raw_dir: str | Path,
    *,
    sources: Optional[list[str]] = None,
    limit: Optional[int] = None,
    mode: str = "full",
) -> dict[str, Any]:
    root = Path(raw_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"raw_dir not found: {root}")

    job_id = str(uuid.uuid4())
    stats: dict[str, Any] = {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "by_source": {},
        "limit": limit,
        "sources": sources,
    }

    with session_scope() as session:
        _ensure_source(session)
        job = IngestJob(
            id=job_id,
            source_id="fs_raw",
            mode=mode,
            status="running",
            started_at=_now(),
            stats={},
        )
        session.add(job)
        session.flush()

        n = 0
        try:
            for draft in iter_all(root, sources=sources):
                if limit is not None and n >= limit:
                    break
                n += 1
                # Nested transaction so one bad doc cannot wipe the batch
                try:
                    with session.begin_nested():
                        action = _upsert_document(session, draft)
                    if action not in ("inserted", "updated", "skipped"):
                        action = "inserted"
                    stats[action] = int(stats.get(action, 0)) + 1
                    src = draft.source_type
                    stats["by_source"].setdefault(
                        src, {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
                    )
                    stats["by_source"][src][action] = (
                        int(stats["by_source"][src].get(action, 0)) + 1
                    )
                    if n % 100 == 0:
                        session.commit()
                        logger.info("ingest progress n=%s stats=%s", n, stats)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "ingest failed for %s:%s", draft.source_type, draft.external_id
                    )
                    stats["errors"] += 1
                    src = draft.source_type
                    stats["by_source"].setdefault(
                        src, {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
                    )
                    stats["by_source"][src]["errors"] = (
                        int(stats["by_source"][src].get("errors", 0)) + 1
                    )

            job = session.get(IngestJob, job_id)
            if job:
                job.status = "success"
                job.finished_at = _now()
                job.stats = {**stats, "processed": n}
            session.commit()
        except Exception as exc:  # noqa: BLE001
            job = session.get(IngestJob, job_id)
            if job:
                job.status = "failed"
                job.finished_at = _now()
                job.error = str(exc)
                job.stats = stats
            session.commit()
            raise

    stats["processed"] = n
    stats["job_id"] = job_id
    logger.info("ingest complete %s", stats)
    return stats
