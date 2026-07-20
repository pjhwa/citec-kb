"""Ops readiness status for pilot / G6 smoke."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app import __version__
from app.db.models import (
    CapacityRule,
    Document,
    DocumentEntity,
    Entity,
    Embedding,
    IssueFrame,
    LexiconTerm,
    QueryLog,
)
from app.db.session import session_scope
from app.jobs.queue import worker_status
from app.settings import get_settings

router = APIRouter(prefix="/v1/ops", tags=["ops"])


@router.get("/status")
def ops_status() -> dict[str, Any]:
    """Aggregate health for operators and pilot pre-checks."""
    settings = get_settings()
    checks: dict[str, Any] = {}
    ok = True

    # DB counts
    try:
        with session_scope() as session:
            counts = {
                "documents": int(session.scalar(select(func.count()).select_from(Document)) or 0),
                "embeddings": int(session.scalar(select(func.count()).select_from(Embedding)) or 0),
                "entities": int(session.scalar(select(func.count()).select_from(Entity)) or 0),
                "document_entities": int(
                    session.scalar(select(func.count()).select_from(DocumentEntity)) or 0
                ),
                "issue_frames": int(
                    session.scalar(select(func.count()).select_from(IssueFrame)) or 0
                ),
                "capacity_rules": int(
                    session.scalar(select(func.count()).select_from(CapacityRule)) or 0
                ),
                "lexicon_terms": int(
                    session.scalar(select(func.count()).select_from(LexiconTerm)) or 0
                ),
                "queries_logged": int(
                    session.scalar(select(func.count()).select_from(QueryLog)) or 0
                ),
            }
            session.execute(text("SELECT 1"))
        checks["postgres"] = {"ok": True, "counts": counts}
        if counts["documents"] < 1000 or counts["embeddings"] < 1000:
            ok = False
            checks["postgres"]["ok"] = False
            checks["postgres"]["error"] = "corpus too small"
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["postgres"] = {"ok": False, "error": str(exc)}

    # worker
    try:
        w = worker_status()
        checks["worker"] = w
        if not w.get("ok"):
            ok = False
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["worker"] = {"ok": False, "error": str(exc)}

    # auth mode (non-secret)
    mode = (settings.auth_mode or "off").lower()
    checks["auth"] = {
        "ok": True,
        "mode": mode,
        "enforced": mode not in ("", "off", "disabled", "none"),
        "oidc_configured": bool(settings.oidc_issuer and settings.oidc_client_id),
    }

    # seeds readiness flags
    c = (checks.get("postgres") or {}).get("counts") or {}
    checks["seeds"] = {
        "ok": all(
            [
                int(c.get("entities") or 0) >= 1,
                int(c.get("capacity_rules") or 0) >= 1,
                int(c.get("lexicon_terms") or 0) >= 1,
                int(c.get("issue_frames") or 0) >= 1,
            ]
        ),
        "entities": c.get("entities"),
        "capacity_rules": c.get("capacity_rules"),
        "lexicon_terms": c.get("lexicon_terms"),
        "issue_frames": c.get("issue_frames"),
    }
    if not checks["seeds"]["ok"]:
        ok = False

    checks["env"] = {
        "app_env": settings.app_env,
        "llm_backend": settings.llm_backend,
        "version": __version__,
    }

    return {
        "status": "ok" if ok else "degraded",
        "version": __version__,
        "checks": checks,
        "pilot_engineering_ready": ok,
        "note": "도메인 파일럿 사인은 별도 체크리스트 필요 (engineering ready만 판정)",
    }
