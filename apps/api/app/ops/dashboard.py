"""Aggregation helpers for the admin real-time dashboard (GET /v1/ops/dashboard).

Split into pure functions (no DB/network — unit tested directly) and
session-based functions (exercised manually / in the running app, matching
the existing convention in app/routers/ops.py: this repo's CI runs with an
unreachable DATABASE_URL, so DB-touching code isn't unit tested).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("citec.ops.dashboard")


def read_raw_manifest(path: str) -> dict[str, int]:
    """Source -> raw file count from data/raw_manifest.json. {} if missing/invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    sources = data.get("sources") or {}
    out: dict[str, int] = {}
    for name, info in sources.items():
        if isinstance(info, dict) and "files" in info:
            try:
                out[name] = int(info["files"])
            except (TypeError, ValueError):
                continue
    return out


def progress_row(
    *,
    raw_files: Optional[int],
    documents: int,
    chunks: int,
    chunks_active: int,
    embeddings: int,
) -> dict[str, Any]:
    """One source_type's ingest/embed progress row. embed_pct is over active chunks."""
    embed_pct = 100 if chunks_active == 0 else round(embeddings / chunks_active * 100)
    return {
        "raw_files": raw_files,
        "documents": documents,
        "chunks": chunks,
        "chunks_active": chunks_active,
        "embeddings": embeddings,
        "embed_pct": min(100, embed_pct),
    }


def resource_snapshot(disk_path: str) -> dict[str, Any]:
    """API-process resource usage: RSS memory, host load average, disk usage at disk_path.

    Not per-container Docker stats — see design spec non-goals.
    """
    snap: dict[str, Any] = {}

    try:
        import resource as _resource

        rss_kb = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB; macOS reports bytes. This app only ships on Linux containers.
        snap["process_rss_mb"] = round(rss_kb / 1024, 1)
    except (ImportError, AttributeError, OSError):
        snap["process_rss_mb"] = None

    try:
        snap["load_avg"] = list(os.getloadavg())
    except (AttributeError, OSError):
        snap["load_avg"] = None

    try:
        total, used, _free = shutil.disk_usage(disk_path)
        total_gb = total / (1024**3)
        used_gb = used / (1024**3)
        pct = round(used / total * 100) if total else 0
        snap["disk"] = {
            "path": disk_path,
            "total_gb": round(total_gb, 1),
            "used_gb": round(used_gb, 1),
            "pct": pct,
        }
    except OSError as exc:
        snap["disk"] = {"path": disk_path, "error": str(exc)}

    return snap


def truncate_query_text(text: Optional[str], limit: int = 120) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def ingest_progress(session, raw_totals: dict[str, int]) -> dict[str, Any]:
    """Per-source_type ingest/embed progress + activity from the latest IngestJob.

    IngestJob rows are not per-source_type: a single filesystem ingest run
    (source_id is always "fs_raw", see app/ingest/pipeline.py::run_ingest)
    covers every source_type in one row, and records a per-source_type
    breakdown in IngestJob.stats["by_source"]. So instead of joining on a
    Source row (there is no per-source_type Source row to join on), take the
    single most recent IngestJob and, for each source_type, surface its
    entry from that job's by_source stats if present.
    """
    from sqlalchemy import func, select

    from app.db.models import Chunk, Document, Embedding, IngestJob

    doc_counts = dict(
        session.execute(
            select(Document.source_type, func.count()).group_by(Document.source_type)
        ).all()
    )
    chunk_counts = dict(
        session.execute(
            select(Document.source_type, func.count(Chunk.id))
            .join(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.source_type)
        ).all()
    )
    chunk_active_counts = dict(
        session.execute(
            select(Document.source_type, func.count(Chunk.id))
            .join(Chunk, Chunk.document_id == Document.id)
            .where(Chunk.is_active.is_(True))
            .group_by(Document.source_type)
        ).all()
    )
    embedding_counts = dict(
        session.execute(
            select(Document.source_type, func.count(Embedding.id))
            .join(Chunk, Chunk.document_id == Document.id)
            .join(Embedding, Embedding.chunk_id == Chunk.id)
            .where(Chunk.is_active.is_(True))
            .group_by(Document.source_type)
        ).all()
    )

    source_types = sorted(
        set(raw_totals)
        | set(doc_counts)
        | set(chunk_counts)
        | set(chunk_active_counts)
        | set(embedding_counts)
    )

    latest_job = session.execute(
        select(IngestJob).order_by(IngestJob.started_at.desc().nullslast()).limit(1)
    ).scalar_one_or_none()
    by_source_stats = {}
    if latest_job and isinstance(latest_job.stats, dict):
        by_source_stats = latest_job.stats.get("by_source") or {}

    rows: dict[str, Any] = {}
    for st in source_types:
        rows[st] = progress_row(
            raw_files=raw_totals.get(st),
            documents=int(doc_counts.get(st, 0)),
            chunks=int(chunk_counts.get(st, 0)),
            chunks_active=int(chunk_active_counts.get(st, 0)),
            embeddings=int(embedding_counts.get(st, 0)),
        )

        last_job = None
        if latest_job is not None and st in by_source_stats:
            last_job = {
                "id": latest_job.id,
                "mode": latest_job.mode,
                "status": latest_job.status,
                "started_at": latest_job.started_at.isoformat() if latest_job.started_at else None,
                "finished_at": latest_job.finished_at.isoformat() if latest_job.finished_at else None,
                "error": latest_job.error,
                "by_source": by_source_stats.get(st),
            }
        rows[st]["last_job"] = last_job

    return rows


def query_stats(session) -> dict[str, Any]:
    """Query volume/latency stats from QueryLog, last 1h/24h + recent 10."""
    from sqlalchemy import func, select

    from app.db.models import QueryLog

    now = datetime.now(timezone.utc)
    since_1h = now - timedelta(hours=1)
    since_24h = now - timedelta(hours=24)

    count_1h = session.scalar(
        select(func.count()).select_from(QueryLog).where(QueryLog.created_at >= since_1h)
    ) or 0
    count_24h = session.scalar(
        select(func.count()).select_from(QueryLog).where(QueryLog.created_at >= since_24h)
    ) or 0
    avg_latency = session.scalar(
        select(func.avg(QueryLog.latency_ms)).where(QueryLog.created_at >= since_24h)
    )

    recent_rows = session.execute(
        select(QueryLog).order_by(QueryLog.created_at.desc()).limit(10)
    ).scalars().all()

    return {
        "count_1h": int(count_1h),
        "count_24h": int(count_24h),
        "avg_latency_ms_24h": round(float(avg_latency), 1) if avg_latency is not None else None,
        "recent": [
            {
                "id": q.id,
                "query": truncate_query_text(q.query),
                "mode": q.mode,
                "latency_ms": q.latency_ms,
                "created_at": q.created_at.isoformat() if q.created_at else None,
            }
            for q in recent_rows
        ],
    }
