"""Redis job queue client (API side).

Queue:   list  citec:jobs:queue  (RPUSH / BLPOP)
Job:     hash  citec:jobs:{id}
Heartbeat: string citec:worker:heartbeat (unix ts)
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

import redis

from app.settings import get_settings

QUEUE_KEY = "citec:jobs:queue"
JOB_KEY = "citec:jobs:{id}"
HEARTBEAT_KEY = "citec:worker:heartbeat"

ALLOWED_TYPES = frozenset(
    {
        "ping",
        "lexicon_seed",
        "entities_seed",
        "capacity_seed",
        "noop",
        "insight_reindex",  # payload: {insight_id}
        "embed_document",  # payload: {document_id}
    }
)


def _client() -> redis.Redis:
    settings = get_settings()
    url = getattr(settings, "redis_url", None) or "redis://localhost:6379/0"
    return redis.from_url(url, decode_responses=True, socket_connect_timeout=3)


def enqueue_job(
    job_type: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    priority: int = 0,
) -> dict[str, Any]:
    if job_type not in ALLOWED_TYPES:
        raise ValueError(f"unsupported job type: {job_type}; allowed={sorted(ALLOWED_TYPES)}")
    jid = str(uuid.uuid4())
    now = int(time.time())
    job = {
        "id": jid,
        "type": job_type,
        "payload": json.dumps(payload or {}, ensure_ascii=False),
        "status": "queued",
        "priority": str(int(priority)),
        "created_at": str(now),
        "started_at": "",
        "finished_at": "",
        "result": "",
        "error": "",
    }
    r = _client()
    r.hset(JOB_KEY.format(id=jid), mapping=job)
    r.rpush(QUEUE_KEY, jid)
    r.expire(JOB_KEY.format(id=jid), 7 * 24 * 3600)
    return _public(job)


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    r = _client()
    data = r.hgetall(JOB_KEY.format(id=job_id))
    if not data:
        return None
    return _public(data)


def list_jobs(*, limit: int = 50) -> dict[str, Any]:
    r = _client()
    # scan recent keys is expensive; use queue length + sample by pattern
    qlen = int(r.llen(QUEUE_KEY) or 0)
    keys = []
    for key in r.scan_iter(match="citec:jobs:*", count=200):
        if key == QUEUE_KEY or key.endswith(":queue"):
            continue
        if key.count(":") >= 2:
            keys.append(key)
    # sort by created_at desc
    items = []
    for key in keys:
        data = r.hgetall(key)
        if data:
            items.append(_public(data))
    items.sort(key=lambda x: int(x.get("created_at") or 0), reverse=True)
    return {
        "queue_length": qlen,
        "total": len(items),
        "items": items[: max(1, min(limit, 200))],
        "worker": worker_status(),
    }


def worker_status() -> dict[str, Any]:
    r = _client()
    raw = r.get(HEARTBEAT_KEY)
    now = int(time.time())
    if not raw:
        return {"ok": False, "heartbeat": None, "age_sec": None}
    try:
        ts = int(raw)
    except ValueError:
        return {"ok": False, "heartbeat": raw, "age_sec": None}
    age = now - ts
    return {"ok": age <= 30, "heartbeat": ts, "age_sec": age}


def _public(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("payload") or "{}"
    if isinstance(payload, str):
        try:
            payload_obj = json.loads(payload)
        except json.JSONDecodeError:
            payload_obj = {"raw": payload}
    else:
        payload_obj = payload
    result = data.get("result") or ""
    if isinstance(result, str) and result.startswith("{"):
        try:
            result_obj = json.loads(result)
        except json.JSONDecodeError:
            result_obj = result
    else:
        result_obj = result
    return {
        "id": data.get("id"),
        "type": data.get("type"),
        "payload": payload_obj,
        "status": data.get("status"),
        "priority": int(data.get("priority") or 0),
        "created_at": int(data.get("created_at") or 0) or None,
        "started_at": int(data["started_at"]) if data.get("started_at") else None,
        "finished_at": int(data["finished_at"]) if data.get("finished_at") else None,
        "result": result_obj if result_obj != "" else None,
        "error": data.get("error") or None,
    }
