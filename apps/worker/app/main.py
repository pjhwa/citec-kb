"""Worker: Redis queue consumer + heartbeat.

Jobs are JSON ids on list `citec:jobs:queue`. Payload/status in hash `citec:jobs:{id}`.
Seed jobs call the API HTTP endpoints so worker stays dependency-light.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import redis

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("citec.worker")

QUEUE_KEY = "citec:jobs:queue"
JOB_KEY = "citec:jobs:{id}"
HEARTBEAT_KEY = "citec:worker:heartbeat"

_running = True


def _stop(*_args: object) -> None:
    global _running
    _running = False
    logger.info("shutdown signal received")


def _api_base() -> str:
    return os.getenv("API_BASE_URL", "http://api:8000").rstrip("/")


def _http_json(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: int = 120,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    # Service token when API AUTH_MODE is enforced
    token = os.getenv("WORKER_API_KEY") or os.getenv("API_SERVICE_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(_api_base() + path, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _handle(job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if job_type == "ping":
        return {"pong": True, "ts": int(time.time()), "payload": payload}
    if job_type == "noop":
        return {"ok": True}
    if job_type == "lexicon_seed":
        return _http_json("POST", "/v1/lexicon/seed")
    if job_type == "entities_seed":
        # link can be slow; allow payload.link bool
        link = payload.get("link", True)
        return _http_json("POST", f"/v1/entities/seed?link={'true' if link else 'false'}")
    if job_type == "capacity_seed":
        return _http_json("POST", "/v1/capacity/seed")
    if job_type == "insight_reindex":
        iid = payload.get("insight_id")
        if not iid:
            raise ValueError("insight_reindex requires payload.insight_id")
        # embed can take a while (model load)
        return _http_json("POST", f"/v1/insights/{iid}/reindex", timeout=600)
    if job_type == "embed_document":
        # Prefer insight_reindex when possible; this path hits a thin API via reindex of
        # a promoted insight is preferred. For raw document_id, call internal-style
        # reindex is not exposed — fall back to insight_id if provided.
        iid = payload.get("insight_id")
        if iid:
            return _http_json("POST", f"/v1/insights/{iid}/reindex", timeout=600)
        doc_id = payload.get("document_id")
        if not doc_id:
            raise ValueError("embed_document requires document_id or insight_id")
        # No public embed-by-doc endpoint; report for ops
        raise ValueError(
            f"embed_document by document_id={doc_id} unsupported; pass insight_id"
        )
    raise ValueError(f"unknown job type: {job_type}")


def _process_one(client: redis.Redis, job_id: str) -> None:
    key = JOB_KEY.format(id=job_id)
    data = client.hgetall(key)
    if not data:
        logger.warning("job missing hash id=%s", job_id)
        return
    job_type = data.get("type") or ""
    try:
        payload = json.loads(data.get("payload") or "{}")
    except json.JSONDecodeError:
        payload = {}

    client.hset(key, mapping={"status": "running", "started_at": str(int(time.time()))})
    logger.info("job start id=%s type=%s", job_id, job_type)
    try:
        result = _handle(job_type, payload if isinstance(payload, dict) else {})
        client.hset(
            key,
            mapping={
                "status": "done",
                "finished_at": str(int(time.time())),
                "result": json.dumps(result, ensure_ascii=False),
                "error": "",
            },
        )
        logger.info("job done id=%s type=%s", job_id, job_type)
    except (HTTPError, URLError, ValueError, OSError) as exc:
        client.hset(
            key,
            mapping={
                "status": "failed",
                "finished_at": str(int(time.time())),
                "error": str(exc)[:2000],
            },
        )
        logger.exception("job failed id=%s: %s", job_id, exc)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    logger.info("worker starting redis=%s api=%s", redis_url, _api_base())
    client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=5)

    last_hb = 0.0
    while _running:
        now = time.time()
        if now - last_hb >= 5:
            try:
                client.set(HEARTBEAT_KEY, str(int(now)), ex=60)
                last_hb = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("heartbeat failed: %s", exc)

        try:
            item = client.blpop(QUEUE_KEY, timeout=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("blpop failed: %s", exc)
            time.sleep(2)
            continue

        if not item:
            continue
        _, job_id = item
        try:
            _process_one(client, job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("process error: %s", exc)

    logger.info("worker stopped")


if __name__ == "__main__":
    main()
