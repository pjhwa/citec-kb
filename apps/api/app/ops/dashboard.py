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
from pathlib import Path
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
