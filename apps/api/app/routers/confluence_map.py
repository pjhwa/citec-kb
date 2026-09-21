"""Confluence *map* (structure-only index) admin ops: trigger a sync run in
the background and expose its live progress for the admin page.

Two endpoints:
- POST /v1/confluence-map/_run-sync — synchronous; runs sync_map() to
  completion and returns its stats. Not meant to be hit directly from a
  browser (a full run can take hours) — it exists so the Redis job worker
  can call it with a long HTTP timeout after the admin UI enqueues a
  "confluence_map_sync" job via POST /v1/jobs. FastAPI dispatches this
  (non-async def) handler to its threadpool, so it doesn't block other
  requests on the same api process.
- GET /v1/confluence-map/status — cheap read of each source's cursor +
  in-progress checkpoint (updated by sync_map() every ~50 pages), plus
  whether a run currently holds the module's advisory lock. This is what
  the admin page polls every few seconds to show live progress.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.confluence.map_sync import MAP_SOURCE_DEFS
from app.db.models import Document, Source
from app.db.session import session_scope
from app.settings import get_settings

router = APIRouter(prefix="/v1/confluence-map", tags=["confluence-map"])


class RunBody(BaseModel):
    source_ids: Optional[list[str]] = None
    dry_run: bool = False
    max_pages_per_root: Optional[int] = None
    root_id: Optional[str] = None


@router.post("/_run-sync")
def run_sync(
    body: RunBody,
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    _ = principal
    from app.confluence.map_sync import sync_map

    settings = get_settings()
    try:
        return sync_map(
            settings.raw_dir,
            dry_run=body.dry_run,
            source_ids=body.source_ids,
            max_pages_per_root=body.max_pages_per_root,
            root_id=body.root_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class InventoryBody(BaseModel):
    source_id: str
    dry_run: bool = False


@router.post("/_run-inventory")
def run_inventory(
    body: InventoryBody,
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    _ = principal
    from app.confluence.map_sync import run_map_inventory

    settings = get_settings()
    try:
        return run_map_inventory(body.source_id, settings.raw_dir, dry_run=body.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/children")
def get_children(space_key: str, parent_path: str = "") -> dict[str, Any]:
    """Direct children of a space (parent_path="") or of a given breadcrumb
    path within it, read from confluence_map documents already ingested
    into the KB search index. Only pages already indexed by confluence_map
    show up here — this is not a general live Confluence browser, and
    (per the 2026-09-21 ACL scope decision — see map_sync.py) carries no
    per-user permission check, matching every other confluence_map/search
    read path today.
    """
    with session_scope() as session:
        rows = session.execute(
            select(Document.external_id, Document.title, Document.source_uri, Document.metadata_)
            .where(
                Document.source_type == "confluence_map",
                Document.status == "active",
                Document.metadata_["space_key"].astext == space_key,
            )
        ).all()

    items: list[dict[str, Any]] = []
    for external_id, title, source_uri, meta in rows:
        path = (meta or {}).get("경로") or ""
        if parent_path:
            prefix = parent_path + " > "
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix):]
        else:
            remainder = path
        if not remainder or " > " in remainder:
            continue  # not present at this level, or a deeper descendant
        items.append(
            {
                "page_id": external_id,
                "title": title,
                "url": source_uri,
                "path": path,
                "is_folder": (meta or {}).get("유형") == "폴더",
            }
        )
    items.sort(key=lambda it: it["title"])
    return {"space_key": space_key, "parent_path": parent_path, "items": items}


@router.get("/status")
def get_status(
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    _ = principal
    from app.confluence.map_sync import get_progress, is_sync_running

    with session_scope() as session:
        rows = session.query(Source).filter(Source.type == "confluence_map").all()
        by_id = {
            r.id: {
                "space_key": (r.config or {}).get("space_key"),
                "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
                "checkpoint": (r.config or {}).get("checkpoint") or {},
            }
            for r in rows
        }
    # Sources never run yet have no row — report them too so the admin page
    # always shows every configured source, not just whichever have already
    # synced once.
    for source_id, sd in MAP_SOURCE_DEFS.items():
        by_id.setdefault(
            source_id,
            {"space_key": sd["space_key"], "last_sync_at": None, "checkpoint": {}},
        )
    lock_error = None
    try:
        running = is_sync_running()
    except Exception as exc:  # noqa: BLE001 — DB hiccup shouldn't break the status view
        running = None
        lock_error = str(exc)
    return {
        "running": running,
        "lock_error": lock_error,
        # "Which space is being crawled right now, how far in" — a
        # best-effort Redis pointer (app.confluence.map_sync._set_progress),
        # separate from `running`/the per-source checkpoints below: it names
        # the *currently active* source/root, which those two alone can't
        # unambiguously tell you. sync_map() clears this on any normal
        # return (success or a raised error), so a non-null value while
        # `running` is false means the process was killed outright (e.g.
        # `docker compose restart api` mid-crawl) rather than exiting
        # through ordinary control flow — that's the case this is actually
        # useful for spotting. None once cleared, once its 1h TTL lapses,
        # or if Redis is unreachable.
        "current": get_progress(),
        "sources": by_id,
    }
