"""Worker process stub: heartbeat on Redis until real jobs land (PR-03+)."""

from __future__ import annotations

import logging
import os
import signal
import time

import redis

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("citec.worker")

_running = True


def _stop(*_args: object) -> None:
    global _running
    _running = False
    logger.info("shutdown signal received")


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    raw_dir = os.getenv("RAW_DIR", "/data/raw")
    logger.info("worker starting redis=%s raw_dir=%s", redis_url, raw_dir)

    client = redis.from_url(redis_url, socket_connect_timeout=5)
    while _running:
        try:
            client.set("citec:worker:heartbeat", str(int(time.time())), ex=60)
            logger.debug("heartbeat ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis heartbeat failed: %s", exc)
        time.sleep(10)

    logger.info("worker stopped")


if __name__ == "__main__":
    main()
