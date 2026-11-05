"""Ops readiness status for pilot / G6 smoke."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text

from app import __version__
from app.audit.api_access import WINDOWS as _API_WINDOWS
from app.audit.api_access import api_usage_stats
from app.audit.mcp_access import mcp_query_stats
from app.auth.deps import require_roles
from app.auth.principal import Principal
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
from app.jobs.queue import list_jobs, worker_status
from app.ops.dashboard import (
    coverage_gaps,
    ingest_progress,
    query_stats,
    read_raw_manifest,
    recent_failure_buckets,
    resolve_raw_manifest_path,
    resource_snapshot,
)
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


@router.get("/dashboard")
def ops_dashboard(
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    """Aggregated real-time view for the admin dashboard (admin-only)."""
    _ = principal
    settings = get_settings()
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        manifest_path = resolve_raw_manifest_path(settings.raw_dir)
        raw_totals = read_raw_manifest(manifest_path)
        with session_scope() as session:
            result["ingest_progress"] = ingest_progress(session, raw_totals)
    except Exception as exc:  # noqa: BLE001
        result["ingest_progress"] = {"error": str(exc)}

    try:
        with session_scope() as session:
            result["coverage_gaps"] = coverage_gaps(session)
    except Exception as exc:  # noqa: BLE001
        result["coverage_gaps"] = {"error": str(exc)}

    try:
        result["jobs"] = list_jobs(limit=20)
    except Exception as exc:  # noqa: BLE001
        result["jobs"] = {"error": str(exc)}

    try:
        result["resources"] = resource_snapshot(settings.raw_dir)
    except Exception as exc:  # noqa: BLE001
        result["resources"] = {"error": str(exc)}

    try:
        with session_scope() as session:
            result["queries"] = query_stats(session)
    except Exception as exc:  # noqa: BLE001
        result["queries"] = {"error": str(exc)}

    try:
        with session_scope() as session:
            result["failure_buckets"] = recent_failure_buckets(session)
    except Exception as exc:  # noqa: BLE001
        result["failure_buckets"] = {"error": str(exc)}

    try:
        with session_scope() as session:
            result["mcp_queries"] = mcp_query_stats(session)
    except Exception as exc:  # noqa: BLE001
        result["mcp_queries"] = {"error": str(exc)}

    return result


_CRON_STALE_SEC = 15 * 60


@router.get("/cron")
def ops_cron(principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    """Host crontab jobs and their last-run state, from logs/cron_status.json.

    The file is written on the host by scripts/cron_snapshot.py (the api container cannot
    see the host crontab). `snapshot_stale` means the snapshot job itself stopped running.
    """
    import json
    import os
    from pathlib import Path

    path = Path(os.getenv("LOG_DIR", "/app/logs")) / "cron_status.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "available": False,
            "hint": "logs/cron_status.json 없음 — 호스트 crontab에 scripts/cron_snapshot.py를 5분 주기로 등록하세요.",
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "hint": f"cron_status.json 읽기 실패: {e}"}
    age = max(0, int(datetime.now(timezone.utc).timestamp() - path.stat().st_mtime))
    return {
        "available": True,
        "snapshot_age_sec": age,
        "snapshot_stale": age > _CRON_STALE_SEC,
        **data,
    }


# ---------------------------------------------------------------- backups (host script <-> admin page)

_BACKUP_SNAPSHOT_STALE_SEC = 5 * 60   # the host runner refreshes the status every minute
_BACKUP_WARN_H, _BACKUP_BAD_H = 30, 54  # daily schedule: one missed night warns, two is a problem


def _backup_files():
    from pathlib import Path

    d = Path(get_settings().data_dir)
    return d / "backup_status.json", d / "backup_requests"


def _read_json_file(path):
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _backup_warnings(st: dict[str, Any], snap_age: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    sets = st.get("sets") or []
    if not sets:
        out.append({"level": "bad", "text": "완료된 백업이 하나도 없습니다."})
    else:
        try:
            fin = datetime.fromisoformat(sets[0].get("finished_at"))
            age_h = (datetime.now(timezone.utc) - fin.astimezone(timezone.utc)).total_seconds() / 3600
            if age_h > _BACKUP_BAD_H:
                out.append({"level": "bad", "text": f"마지막 성공 백업이 {age_h:.0f}시간 전입니다."})
            elif age_h > _BACKUP_WARN_H:
                out.append({"level": "warn", "text": f"마지막 성공 백업이 {age_h:.0f}시간 전입니다(하루 1회 예정)."})
        except (TypeError, ValueError):
            pass
    last = st.get("last_run") or {}
    if last and last.get("ok") is False:
        out.append({"level": "bad", "text": "마지막 백업 시도가 실패했습니다: " + str(last.get("error") or "원인 불명")})
    disk = st.get("disk") or {}
    if disk.get("total"):
        free_pct = 100 * disk["free"] / disk["total"]
        if free_pct < 10:
            out.append({"level": "bad", "text": f"백업 디스크 여유가 {free_pct:.0f}%뿐입니다."})
        elif free_pct < 20:
            out.append({"level": "warn", "text": f"백업 디스크 여유가 {free_pct:.0f}%입니다."})
    if snap_age > _BACKUP_SNAPSHOT_STALE_SEC:
        out.append({"level": "warn", "text": "호스트 백업 러너의 상태 갱신이 멈췄습니다(crontab의 process-requests가 도는지 확인). 지금 백업 요청은 처리되지 않을 수 있습니다."})
    return out


def _recent_requests(req_dir) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    pending = None
    for p in sorted(req_dir.glob("*.json")) if req_dir.is_dir() else []:
        r = _read_json_file(p)
        if isinstance(r, dict):
            pending = r
            break
    done = []
    dd = req_dir / "done"
    for p in sorted(dd.glob("*.json"), reverse=True)[:5] if dd.is_dir() else []:
        r = _read_json_file(p)
        if isinstance(r, dict):
            done.append(r)
    return pending, done


@router.get("/backups")
def ops_backups(principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    """Backup sets and health, from data/backup_status.json (written on the host by scripts/backup_manager.py)."""
    status_file, req_dir = _backup_files()
    st = _read_json_file(status_file)
    if not isinstance(st, dict):
        return {
            "available": False,
            "hint": "data/backup_status.json 없음 — 호스트 crontab에 scripts/backup_manager.py를 등록하세요 (docs/BACKUP_RESTORE.md).",
        }
    age = max(0, int(datetime.now(timezone.utc).timestamp() - status_file.stat().st_mtime))
    warnings = _backup_warnings(st, age)
    pending, done = _recent_requests(req_dir)
    level = "bad" if any(w["level"] == "bad" for w in warnings) else "warn" if warnings else "ok"
    return {"available": True, "overall": level, "warnings": warnings, "snapshot_age_sec": age,
            "pending_request": pending, "recent_requests": done, **st}


@router.post("/backups/request")
def ops_backup_request(principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    """Ask the host runner for a backup now. It is picked up within about a minute."""
    import json
    import secrets

    status_file, req_dir = _backup_files()
    pending, _ = _recent_requests(req_dir)
    if pending:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="이미 처리 대기/진행 중인 백업 요청이 있습니다.")
    req_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    req = {"id": now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2), "requested_at": now.isoformat(timespec="seconds"),
           "requested_by": principal.name, "status": "pending"}
    path = req_dir / (req["id"] + ".json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    stale = (not status_file.exists()) or (now.timestamp() - status_file.stat().st_mtime > _BACKUP_SNAPSHOT_STALE_SEC)
    return {**req, "runner_warning": "호스트 러너가 응답하지 않는 것 같습니다. crontab 등록을 확인하세요." if stale else None}


@router.get("/api-usage")
def ops_api_usage(
    window: str = "24h", principal: Principal = Depends(require_roles("admin"))
) -> dict[str, Any]:
    """API usage (non-MCP) by caller kind: web UI / internal systems / external systems."""
    from fastapi import HTTPException

    if window not in _API_WINDOWS:
        raise HTTPException(status_code=422, detail=f"window는 {', '.join(_API_WINDOWS)} 중 하나여야 합니다.")
    with session_scope() as session:
        return api_usage_stats(session, window)
