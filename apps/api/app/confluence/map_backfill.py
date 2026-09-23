"""Full confluence_map backfill: every active source, then one ingest, then embed.

Inventory used to stop after writing files and ingesting. New excerpt text
has no vector until embed_pending_chunks runs, and ingesting the whole map
once per source repeats a 30k-file scan. This runs the crawls under one
lock, ingests once, and embeds until no active confluence_map chunk is left
without a vector.

Resume is the default. A finished source is skipped. Ingest and embed run
again if this process crawled anything, or if they did not finish last time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select

from app.confluence.map_sync import (
    _run_map_inventory_locked,
    _set_progress,
    _sync_run_lock,
    get_source_defs,
)
from app.db.models import Chunk, Document, Embedding
from app.db.session import session_scope
from app.embed.model import MODEL_ID

logger = logging.getLogger("citec.map_backfill")

_STATE_NAME = ".backfill_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_path(raw_dir: str | Path) -> Path:
    return Path(raw_dir) / "confluence_map" / _STATE_NAME


def load_state(raw_dir: str | Path) -> dict[str, Any]:
    path = state_path(raw_dir)
    if not path.is_file():
        return {"sources": {}, "ingest": None, "embed": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("backfill state unreadable, starting fresh: %s", path)
        return {"sources": {}, "ingest": None, "embed": None}
    data.setdefault("sources", {})
    return data


def save_state(raw_dir: str | Path, state: dict[str, Any]) -> None:
    path = state_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _doc_counts() -> dict[str, int]:
    with session_scope() as session:
        rows = session.execute(
            select(Document.metadata_["space_key"].astext, func.count())
            .where(Document.source_type == "confluence_map", Document.status == "active")
            .group_by(Document.metadata_["space_key"].astext)
        ).all()
    return {str(space or ""): int(n) for space, n in rows}


def ordered_source_ids(defs: dict[str, dict[str, Any]], counts: dict[str, int]) -> list[str]:
    """Small spaces first so a bad crawl fails before the 10k-page ones."""
    return sorted(defs, key=lambda sid: (counts.get(defs[sid].get("space_key") or "", 0), sid))


def count_pending_chunks(*, source_type: str, model_name: str = MODEL_ID) -> int:
    exists_emb = (
        select(Embedding.id)
        .where(Embedding.chunk_id == Chunk.id)
        .where(Embedding.model == model_name)
        .correlate(Chunk)
        .exists()
    )
    with session_scope() as session:
        n = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.is_active.is_(True))
            .where(Document.source_type == source_type)
            .where(Document.status == "active")
            .where(~exists_emb)
        )
    return int(n or 0)


_RETRY_LIMIT = 3


def _source_done(state: dict[str, Any], source_id: str) -> bool:
    """Skip on resume when the source finished, including a permanent failure."""
    row = (state.get("sources") or {}).get(source_id) or {}
    if row.get("status") == "crawled":
        return True
    return row.get("status") == "error" and int(row.get("attempts") or 0) >= _RETRY_LIMIT


def _touch(raw_dir: Path, state: dict[str, Any], **fields: Any) -> None:
    state.update(fields)
    state["updated_at"] = _now()
    save_state(raw_dir, state)
    progress = {
        "phase": state.get("phase") or "",
        "source_id": state.get("current_source_id") or "",
        "source_index": state.get("source_index") or "",
        "source_total": state.get("sources_total") or "",
        "retry_attempt": state.get("retry_attempt") or "",
        "pending": (state.get("embed") or {}).get("pending") if isinstance(state.get("embed"), dict) else "",
        "ok": state.get("ok") if state.get("ok") is not None else "",
        "finished_at": state.get("finished_at") or "",
    }
    _set_progress(**progress)


def backfill_status(raw_dir: str | Path) -> dict[str, Any]:
    """Compact view for admin.html. Reads the state file, not Redis."""
    state = load_state(raw_dir)
    sources = state.get("sources") or {}
    leftover = state.get("leftover_errors") or []
    embed = state.get("embed") if isinstance(state.get("embed"), dict) else None
    return {
        "phase": state.get("phase") or "idle",
        "updated_at": state.get("updated_at"),
        "finished_at": state.get("finished_at"),
        "ok": state.get("ok"),
        "message": state.get("message"),
        "sources_total": state.get("sources_total"),
        "sources_done": sum(1 for row in sources.values() if row.get("status") in {"crawled", "error"}),
        "current_source_id": state.get("current_source_id"),
        "source_index": state.get("source_index"),
        "retry_attempt": state.get("retry_attempt"),
        "ingest": state.get("ingest"),
        "embed": embed,
        "leftover_error_count": len(leftover) if isinstance(leftover, list) else 0,
        "complete": state.get("phase") == "done" and bool(embed) and embed.get("status") == "done",
    }


def _failure_items(source_id: str, sd: dict[str, Any], errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    roots = sd.get("roots") or {}
    explicit = sd.get("explicit_pages") or {}
    for err in errors:
        if not isinstance(err, dict):
            continue
        page_id = err.get("page_id")
        root_id = err.get("root_id")
        label = ""
        if page_id and str(page_id) in explicit:
            label = explicit[str(page_id)]
        elif root_id and str(root_id) in roots:
            label = roots[str(root_id)]
        elif root_id and str(root_id) in explicit:
            label = explicit[str(root_id)]
        items.append(
            {
                "source_id": source_id,
                "page_id": page_id,
                "root_id": root_id,
                "start": err.get("start"),
                "root_label": label,
                "space_key": sd.get("space_key"),
                "space_name": sd.get("space_name"),
                "attempts": 0,
                "error": str(err.get("error") or ""),
            }
        )
    return items


def retry_failures(
    client: Any,
    failures: list[dict[str, Any]],
    raw_dir: Path,
    settings: Any,
) -> list[dict[str, Any]]:
    """Try each failed page or listing request once. Successes drop out."""
    import asyncio

    from app.confluence.client import RateLimiter
    from app.confluence.map_sync import _write_map_page

    if not failures:
        return []

    async def _once() -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        limiter = RateLimiter(settings.confluence_rate_limit_rps)
        async with client.bulk_client() as http:
            for item in failures:
                attempts = int(item.get("attempts") or 0)
                if attempts >= _RETRY_LIMIT:
                    kept.append(item)
                    continue
                try:
                    page_id = item.get("page_id")
                    if page_id:
                        meta = await client.get_page_full(
                            str(page_id), client=http, limiter=limiter
                        )
                        _write_map_page(
                            meta=meta,
                            root_label=item.get("root_label") or "",
                            space_key=item.get("space_key") or "",
                            space_name=item.get("space_name") or "",
                            base_url=client._base_url,
                            tz_name=settings.confluence_timezone,
                            raw_dir=raw_dir,
                        )
                        continue
                    root_id = item.get("root_id")
                    start = item.get("start")
                    if root_id is None or start is None:
                        raise RuntimeError(item.get("error") or "retry target has no page_id")
                    data = await client.search_pages_incremental(
                        str(root_id),
                        since=None,
                        start=int(start),
                        limit=50,
                        client=http,
                        limiter=limiter,
                    )
                    for summary in data.get("results") or []:
                        pid = str(summary.get("id"))
                        meta = await client.get_page_full(pid, client=http, limiter=limiter)
                        _write_map_page(
                            meta=meta,
                            root_label=item.get("root_label") or "",
                            space_key=item.get("space_key") or "",
                            space_name=item.get("space_name") or "",
                            base_url=client._base_url,
                            tz_name=settings.confluence_timezone,
                            raw_dir=raw_dir,
                        )
                except Exception as exc:  # noqa: BLE001 — one failed retry must not stop the rest
                    logger.warning("backfill retry failed page=%s root=%s: %s", item.get("page_id"), item.get("root_id"), exc)
                    nxt = dict(item)
                    nxt["attempts"] = attempts + 1
                    nxt["error"] = str(exc)
                    kept.append(nxt)
        return kept

    return asyncio.run(_once())


def run_backfill(
    raw_dir: str | Path,
    *,
    source_ids: Optional[list[str]] = None,
    resume: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Crawl every source, ingest confluence_map once, embed until pending is 0.

    dry_run only prints the plan. It does not call Confluence.
    """
    from app.confluence.client import ConfluenceClient
    from app.settings import get_settings

    raw_root = Path(raw_dir)
    settings = get_settings()
    defs = get_source_defs(active_only=True)
    if source_ids:
        unknown = [s for s in source_ids if s not in defs]
        if unknown:
            raise ValueError(f"unknown or disabled confluence_map source_id: {unknown}")
        selected = {s: defs[s] for s in source_ids}
    else:
        selected = defs
    counts = _doc_counts()
    order = ordered_source_ids(selected, counts)
    plan = [
        {
            "source_id": sid,
            "space_key": selected[sid].get("space_key"),
            "active_documents": counts.get(selected[sid].get("space_key") or "", 0),
        }
        for sid in order
    ]
    pages = sum(row["active_documents"] for row in plan)
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "plan": plan,
        "active_documents": pages,
        "estimate_crawl_hours": round(pages / max(settings.confluence_rate_limit_rps, 0.01) / 3600, 2),
        "sources": {},
    }
    if dry_run:
        logger.info("backfill dry-run sources=%s pages=%s", len(order), pages)
        return report

    state = load_state(raw_root) if resume else {"sources": {}, "ingest": None, "embed": None}
    _touch(
        raw_root,
        state,
        phase="starting",
        sources_total=len(order),
        message=f"백필 시작, 소스 {len(order)}개",
        ok=None,
        finished_at=None,
    )
    state["sources_total"] = len(order)
    crawled_this_run = False
    failures: list[dict[str, Any]] = list(state.get("leftover_errors") or []) if resume else []

    def crawl_one(sid: str, index: int) -> dict[str, Any]:
        _touch(
            raw_root,
            state,
            phase="backfill-crawl",
            current_source_id=sid,
            source_index=index,
            message=f"크롤 {index}/{len(order)} {sid}",
        )
        logger.info("backfill crawl start %s (%s/%s)", sid, index, len(order))
        result = _run_map_inventory_locked(
            sid,
            raw_root,
            client=client,
            settings=settings,
            sd=selected[sid],
            dry_run=False,
            ingest=False,
            progress={
                "phase": "backfill-crawl",
                "source_id": sid,
                "space_key": selected[sid].get("space_key") or "",
                "source_index": index,
                "source_total": len(order),
            },
        )
        errors = [e for e in (result.get("errors") or []) if isinstance(e, dict)]
        failures.extend(_failure_items(sid, selected[sid], errors))
        return {
            "status": "crawled",
            "written": result.get("written"),
            "error_count": len(errors),
            "errors": errors[:20],
            "archived": len(result.get("archived") or []),
            "archive_skipped_due_to_errors": result.get("archive_skipped_due_to_errors"),
            "attempts": int(((state.get("sources") or {}).get(sid) or {}).get("attempts") or 0),
            "finished_at": _now(),
        }

    with _sync_run_lock() as acquired:
        if not acquired:
            report["skipped"] = True
            report["reason"] = "already_running"
            return report
        client = ConfluenceClient(settings)
        source_retry: list[str] = []
        for index, sid in enumerate(order, start=1):
            if resume and _source_done(state, sid):
                logger.info("backfill skip finished source %s", sid)
                report["sources"][sid] = {**(state.get("sources") or {}).get(sid, {}), "skipped": True}
                continue
            try:
                row = crawl_one(sid, index)
            except Exception as exc:  # noqa: BLE001 — one source must not stop the batch
                logger.exception("backfill source failed %s", sid)
                row = {
                    "status": "error",
                    "written": None,
                    "error_count": 1,
                    "errors": [{"error": str(exc)}],
                    "attempts": int(((state.get("sources") or {}).get(sid) or {}).get("attempts") or 0),
                    "finished_at": _now(),
                }
                source_retry.append(sid)
            state.setdefault("sources", {})[sid] = row
            _touch(raw_root, state, phase="backfill-crawl", current_source_id=sid, source_index=index)
            report["sources"][sid] = row
            crawled_this_run = True

        # First pass is finished. Retry only the sources that threw, up to 3 times.
        for attempt in range(1, _RETRY_LIMIT + 1):
            if not source_retry:
                break
            _touch(
                raw_root,
                state,
                phase="backfill-retry-source",
                retry_attempt=attempt,
                message=f"소스 재시도 {attempt}/{_RETRY_LIMIT} ({len(source_retry)}개)",
            )
            logger.info("backfill source retry %s/%s n=%s", attempt, _RETRY_LIMIT, len(source_retry))
            still: list[str] = []
            for sid in source_retry:
                prev = state["sources"].get(sid) or {}
                attempts = int(prev.get("attempts") or 0) + 1
                try:
                    row = crawl_one(sid, order.index(sid) + 1)
                    row["attempts"] = attempts
                    row["retried"] = True
                    state["sources"][sid] = row
                    report["sources"][sid] = row
                except Exception as exc:  # noqa: BLE001
                    logger.exception("backfill source retry failed %s attempt=%s", sid, attempts)
                    row = {
                        **prev,
                        "status": "error",
                        "attempts": attempts,
                        "error_count": 1,
                        "errors": [{"error": str(exc)}],
                        "finished_at": _now(),
                    }
                    state["sources"][sid] = row
                    report["sources"][sid] = row
                    if attempts < _RETRY_LIMIT:
                        still.append(sid)
            source_retry = still
            _touch(raw_root, state, phase="backfill-retry-source", retry_attempt=attempt)

        for attempt in range(1, _RETRY_LIMIT + 1):
            if not failures:
                break
            _touch(
                raw_root,
                state,
                phase="backfill-retry",
                retry_attempt=attempt,
                message=f"실패 요청 재시도 {attempt}/{_RETRY_LIMIT} ({len(failures)}건)",
            )
            logger.info("backfill page retry %s/%s n=%s", attempt, _RETRY_LIMIT, len(failures))
            failures = retry_failures(client, failures, raw_root, settings)
            crawled_this_run = True
        state["leftover_errors"] = failures
        state["retry"] = {
            "leftover": len(failures),
            "finished_at": _now(),
        }
        _touch(raw_root, state, phase="backfill-retry", message=f"재시도 종료, 남은 오류 {len(failures)}건")
        report["leftover_errors"] = len(failures)

        need_ingest = crawled_this_run or (state.get("ingest") or {}).get("status") != "done"
        ingest_row: dict[str, Any]
        if need_ingest:
            from app.ingest.pipeline import run_ingest

            ingest_row = {"status": "error", "errors": 1, "finished_at": _now()}
            for attempt in range(1, _RETRY_LIMIT + 1):
                _touch(
                    raw_root,
                    state,
                    phase="backfill-ingest",
                    retry_attempt=attempt,
                    message=f"인제스트 {attempt}/{_RETRY_LIMIT}",
                )
                logger.info("backfill ingest attempt %s", attempt)
                try:
                    ingest_stats = run_ingest(raw_root, sources=["confluence_map"])
                except Exception as exc:  # noqa: BLE001
                    logger.exception("backfill ingest failed attempt=%s", attempt)
                    ingest_row = {"status": "error", "errors": 1, "error": str(exc), "attempt": attempt, "finished_at": _now()}
                    continue
                ingest_row = {
                    "status": "done" if not ingest_stats.get("errors") else "error",
                    "inserted": ingest_stats.get("inserted"),
                    "updated": ingest_stats.get("updated"),
                    "skipped": ingest_stats.get("skipped"),
                    "errors": ingest_stats.get("errors"),
                    "attempt": attempt,
                    "finished_at": _now(),
                }
                if not ingest_stats.get("errors"):
                    break
            state["ingest"] = ingest_row
            report["ingest"] = ingest_row
        else:
            ingest_row = {**(state.get("ingest") or {}), "skipped": True}
            report["ingest"] = ingest_row
        _touch(raw_root, state, phase="backfill-ingest", ingest=report["ingest"])

        from app.embed.job import embed_pending_chunks

        passes: list[dict[str, Any]] = []
        pending = count_pending_chunks(source_type="confluence_map")
        logger.info("backfill embed first pass pending=%s", pending)
        _touch(raw_root, state, phase="backfill-embed", retry_attempt=0, message=f"임베딩 시작, 대기 {pending}건")
        if pending:
            try:
                stats = embed_pending_chunks(source_type="confluence_map")
                passes.append({"embedded": stats.get("embedded"), "errors": stats.get("errors"), "elapsed_sec": stats.get("elapsed_sec"), "attempt": 0})
            except Exception as exc:  # noqa: BLE001
                logger.exception("backfill embed first pass failed")
                passes.append({"embedded": 0, "errors": 1, "error": str(exc), "attempt": 0})
            pending = count_pending_chunks(source_type="confluence_map")
        for attempt in range(1, _RETRY_LIMIT + 1):
            if pending == 0:
                break
            _touch(
                raw_root,
                state,
                phase="backfill-embed",
                retry_attempt=attempt,
                message=f"임베딩 재시도 {attempt}/{_RETRY_LIMIT}, 대기 {pending}건",
            )
            logger.info("backfill embed retry %s pending=%s", attempt, pending)
            try:
                stats = embed_pending_chunks(source_type="confluence_map")
                passes.append({"embedded": stats.get("embedded"), "errors": stats.get("errors"), "elapsed_sec": stats.get("elapsed_sec"), "attempt": attempt})
            except Exception as exc:  # noqa: BLE001
                logger.exception("backfill embed retry failed attempt=%s", attempt)
                passes.append({"embedded": 0, "errors": 1, "error": str(exc), "attempt": attempt})
            pending = count_pending_chunks(source_type="confluence_map")
        embed_row = {
            "status": "done" if pending == 0 else "error",
            "pending": pending,
            "passes": passes,
            "finished_at": _now(),
        }
        finished = _now()
        ok = pending == 0 and not failures and all(
            (row.get("status") == "crawled") or row.get("skipped") or int(row.get("attempts") or 0) >= _RETRY_LIMIT
            for row in report["sources"].values()
        )
        if report["ingest"].get("errors"):
            ok = False
        message = "맵 백필 완료" if ok else f"맵 백필 종료 (남은 오류 {len(failures)}건, 임베딩 대기 {pending}건)"
        _touch(
            raw_root,
            state,
            phase="done",
            embed=embed_row,
            ingest=report["ingest"],
            ok=ok,
            finished_at=finished,
            message=message,
            current_source_id="",
        )
        report["embed"] = embed_row
        report["ok"] = ok
        report["finished_at"] = finished
        report["message"] = message
        _set_progress(
            phase="backfill-done",
            source_id="",
            pending=pending,
            ok=ok,
            finished_at=finished,
            message=message,
        )
    return report
