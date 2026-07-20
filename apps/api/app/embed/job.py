"""Batch embed active chunks into pgvector (idempotent, keyset-paged)."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.db.models import Chunk, Embedding, IngestJob
from app.db.session import session_scope
from app.embed.model import EMBEDDING_DIM, MODEL_ID, embed_passages, get_model

logger = logging.getLogger("citec.embed.job")

# Truncate passage text before encode (e5 max_seq ~512 tokens ≈ ~1500–2000 chars).
_TEXT_CAP = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_pending_batch(
    *,
    model_name: str,
    batch_size: int,
    after_id: Optional[str],
    remaining: Optional[int],
) -> list[Any]:
    """Keyset page of active chunks missing an embedding for model_name."""
    limit = batch_size if remaining is None else min(batch_size, remaining)
    if limit <= 0:
        return []

    with session_scope() as session:
        # LEFT ANTI JOIN via NOT EXISTS — avoids huge NOT IN lists.
        exists_emb = (
            select(Embedding.id)
            .where(Embedding.chunk_id == Chunk.id)
            .where(Embedding.model == model_name)
            .correlate(Chunk)
            .exists()
        )
        stmt = (
            select(Chunk.id, Chunk.header_context, Chunk.text)
            .where(Chunk.is_active.is_(True))
            .where(~exists_emb)
            .order_by(Chunk.id)
            .limit(limit)
        )
        if after_id:
            stmt = stmt.where(Chunk.id > after_id)
        rows = list(session.execute(stmt).all())
    return rows


def embed_pending_chunks(
    *,
    batch_size: int = 16,
    limit: Optional[int] = None,
    model_name: str = MODEL_ID,
) -> dict[str, Any]:
    """Embed chunks that lack a row in embeddings for this model (streamed)."""
    job_id = str(uuid.uuid4())
    stats: dict[str, Any] = {
        "embedded": 0,
        "skipped_existing": 0,
        "errors": 0,
        "batches": 0,
        "model": model_name,
        "dim": EMBEDDING_DIM,
        "batch_size": batch_size,
        "limit": limit,
    }
    t_job = time.perf_counter()

    # Ensure weights warm before job row / first DB page.
    get_model()
    logger.info(
        "embed job start id=%s model=%s batch_size=%s limit=%s",
        job_id,
        model_name,
        batch_size,
        limit,
    )

    with session_scope() as session:
        job = IngestJob(
            id=job_id,
            source_id="fs_raw",
            mode="reembed",
            status="running",
            started_at=_now(),
            stats={"phase": "running"},
        )
        session.add(job)

    after_id: Optional[str] = None
    remaining: Optional[int] = limit

    while True:
        if remaining is not None and remaining <= 0:
            break
        t_fetch = time.perf_counter()
        batch = _fetch_pending_batch(
            model_name=model_name,
            batch_size=batch_size,
            after_id=after_id,
            remaining=remaining,
        )
        fetch_ms = (time.perf_counter() - t_fetch) * 1000
        if not batch:
            logger.info("embed no more pending after_id=%s stats=%s", after_id, stats)
            break

        texts = [
            f"{(r.header_context or '')}\n{(r.text or '')}"[:_TEXT_CAP] for r in batch
        ]
        ids = [r.id for r in batch]
        after_id = ids[-1]

        t_enc = time.perf_counter()
        try:
            vectors = embed_passages(texts)
        except Exception:
            logger.exception(
                "embed batch failed n=%s after_id=%s first_id=%s",
                len(batch),
                after_id,
                ids[0],
            )
            stats["errors"] += len(batch)
            stats["batches"] += 1
            if remaining is not None:
                remaining -= len(batch)
            continue
        enc_ms = (time.perf_counter() - t_enc) * 1000

        t_db = time.perf_counter()
        with session_scope() as session:
            for cid, vec in zip(ids, vectors):
                if len(vec) != EMBEDDING_DIM:
                    stats["errors"] += 1
                    continue
                exists = session.scalar(
                    select(Embedding.id).where(
                        Embedding.chunk_id == cid, Embedding.model == model_name
                    )
                )
                if exists:
                    stats["skipped_existing"] += 1
                    continue
                session.add(
                    Embedding(
                        chunk_id=cid,
                        model=model_name,
                        dim=EMBEDDING_DIM,
                        vector=vec,
                    )
                )
                stats["embedded"] += 1
        db_ms = (time.perf_counter() - t_db) * 1000

        stats["batches"] += 1
        if remaining is not None:
            remaining -= len(batch)

        logger.info(
            "embed progress batch=%s n=%s embedded=%s errors=%s "
            "fetch_ms=%.0f enc_ms=%.0f db_ms=%.0f after_id=%s",
            stats["batches"],
            len(batch),
            stats["embedded"],
            stats["errors"],
            fetch_ms,
            enc_ms,
            db_ms,
            after_id,
        )

        # Persist running stats every batch (crash-visible).
        if stats["batches"] % 5 == 0:
            with session_scope() as session:
                job = session.get(IngestJob, job_id)
                if job:
                    job.stats = {**stats, "phase": "running"}

    elapsed = time.perf_counter() - t_job
    stats["elapsed_sec"] = round(elapsed, 2)

    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        total_emb = session.scalar(select(func.count()).select_from(Embedding)) or 0
        stats["embeddings_total"] = int(total_emb)
        if job:
            job.status = "success" if stats["errors"] == 0 else "partial"
            job.finished_at = _now()
            job.stats = stats

    stats["job_id"] = job_id
    logger.info("embed job done %s", stats)
    return stats
