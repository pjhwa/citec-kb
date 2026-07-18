from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import __version__
from app.llm import check_llm
from app.settings import get_settings

logger = logging.getLogger("citec.api")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logger.info(
        "starting api version=%s env=%s llm_backend=%s",
        __version__,
        settings.app_env,
        settings.llm_backend,
    )
    yield
    logger.info("shutting down api")


app = FastAPI(
    title="CI-TEC Knowledge Platform API",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str
    checks: dict[str, Any]


@app.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    checks: dict[str, Any] = {}

    # Redis
    try:
        r = redis.from_url(settings.redis_url, socket_connect_timeout=2)
        checks["redis"] = {"ok": r.ping()}
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = {"ok": False, "error": str(exc)}

    # Postgres (optional at PR-01 — may be empty schema)
    try:
        import psycopg

        # strip SQLAlchemy-style driver prefix if present
        dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        checks["postgres"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = {"ok": False, "error": str(exc)}

    # raw dir
    raw = Path(settings.raw_dir)
    checks["raw_dir"] = {
        "ok": raw.is_dir(),
        "path": str(raw),
        "exists": raw.exists(),
    }

    # LLM config presence (not full probe on every health — use /v1/health/llm)
    checks["llm"] = {
        "ok": settings.llm_backend != "none",
        "backend": settings.llm_backend,
        "profile": settings.model_profile_key,
        "openrouter_model": settings.openrouter_model_id
        if settings.llm_backend == "openrouter"
        else None,
        "max_context_tokens": settings.max_context_tokens,
    }

    critical_ok = checks["redis"].get("ok") and checks["postgres"].get("ok")
    status = "ok" if critical_ok else "degraded"

    return HealthResponse(
        status=status,
        version=__version__,
        env=settings.app_env,
        checks=checks,
    )


@app.get("/v1/health/llm")
async def health_llm() -> dict[str, Any]:
    """Live LLM probe (OpenRouter ping). Prefer not to hit on liveness every 10s."""
    return await check_llm()


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "citec-knowledge-api",
        "version": __version__,
        "docs": "/docs",
        "health": "/v1/health",
    }
