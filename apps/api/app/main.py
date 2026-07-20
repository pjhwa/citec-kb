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

# routers
from app.routers import bundles as bundles_router  # noqa: E402
from app.routers import chat as chat_router  # noqa: E402
from app.routers import checkitems as checkitems_router  # noqa: E402
from app.routers import frames as frames_router  # noqa: E402
from app.routers import ingest as ingest_router  # noqa: E402
from app.routers import search as search_router  # noqa: E402
from app.routers import similar_incident as si_router  # noqa: E402
from app.routers import analytics as analytics_router  # noqa: E402
from app.routers import capacity as capacity_router  # noqa: E402
from app.routers import entities as entities_router  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import insights as insights_router  # noqa: E402
from app.routers import jobs as jobs_router  # noqa: E402
from app.routers import lexicon as lexicon_router  # noqa: E402
from app.routers import ops as ops_router  # noqa: E402
from app.routers import tickets as tickets_router  # noqa: E402

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
app.include_router(ingest_router.router)
app.include_router(search_router.router)
app.include_router(checkitems_router.router)
app.include_router(chat_router.router)
app.include_router(frames_router.router)
app.include_router(si_router.router)
app.include_router(bundles_router.router)
app.include_router(tickets_router.router)
app.include_router(analytics_router.router)
app.include_router(capacity_router.router)
app.include_router(entities_router.router)
app.include_router(lexicon_router.router)
app.include_router(jobs_router.router)
app.include_router(ops_router.router)
app.include_router(insights_router.router)
app.include_router(auth_router.router)


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

    # Postgres + schema revision
    try:
        import psycopg

        dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'alembic_version')"
                )
                has_alembic = bool(cur.fetchone()[0])
                rev = None
                if has_alembic:
                    cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                    row = cur.fetchone()
                    rev = row[0] if row else None
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
                table_count = int(cur.fetchone()[0])
                docs_count = None
                if rev:
                    cur.execute(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'documents')"
                    )
                    if cur.fetchone()[0]:
                        cur.execute("SELECT COUNT(*) FROM documents")
                        docs_count = int(cur.fetchone()[0])
        checks["postgres"] = {
            "ok": True,
            "alembic_revision": rev,
            "public_tables": table_count,
            "documents_count": docs_count,
        }
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = {"ok": False, "error": str(exc)}

    # raw dir (registered knowledge corpus)
    raw = Path(settings.raw_dir)
    source_counts: dict[str, int] = {}
    total = 0
    if raw.is_dir():
        for child in sorted(raw.iterdir()):
            if child.is_dir():
                n = sum(1 for f in child.rglob("*") if f.is_file() and f.name != ".gitkeep")
                source_counts[child.name] = n
                total += n
    checks["raw_dir"] = {
        "ok": raw.is_dir() and total > 0,
        "path": str(raw),
        "exists": raw.exists(),
        "total_files": total,
        "sources": source_counts,
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
