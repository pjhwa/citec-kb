"""Weekly full reconciliation for confluence_docs / tech_repo.

``sync.sync()`` is incremental (CQL ``lastmodified > cursor``). A page that is
only *moved* in Confluence (new parent, new root) may not change
``lastmodified``, and a page that leaves every tracked root never shows up in
"what changed" at all, so the index keeps its old ``ancestor_ids`` / folder
and the moved-out page stays ``active`` forever. This is the same gap
``map_sync.run_map_inventory`` closes for confluence_map.

Unlike that function this one does not re-download every page (~8k pages at
0.3 req/s would take hours). It lists every configured root in full with
``expand=ancestors`` (about 170 search calls), diffs the listing against the
active documents in the DB, and fetches only the pages that are new, moved or
renamed. Pages the listing no longer returns are archived behind safety
guards (see ``run_docs_inventory``).

Never touches ``Source.last_sync_at`` — that is the daily sync's bookkeeping.

``plan_reconcile`` is pure so the decision logic is testable without
Confluence or a database.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from sqlalchemy import select, text

from app.confluence.client import ConfluenceClient, RateLimiter
from app.confluence.sync import _PAGE_SIZE, _SOURCE_DEFS, _write_page
from app.db.models import Document
from app.db.session import get_engine, session_scope
from app.settings import get_settings

logger = logging.getLogger("citec.confluence.docs_inventory")

# Distinct from map_sync's lock: the two jobs write different raw dirs and
# document sets, so neither should block the other for hours.
_INVENTORY_LOCK_KEY = 861_234_502

DEFAULT_MAX_ARCHIVE_RATIO = 0.05
ARCHIVE_SUBDIR = ".archived"

REASON_NEW = "new"
REASON_MOVED = "moved"
REASON_RENAMED = "renamed"
REASON_NO_ANCESTORS = "no_ancestors_in_listing"


@dataclass
class ListedPage:
    page_id: str
    title: str
    ancestor_ids: Optional[list[str]]  # self-inclusive, same shape as sync._write_page; None = listing had none
    root_id: str
    root_label: str


@dataclass
class DbPage:
    external_id: str
    title: str
    ancestor_ids: Optional[list[str]]


@dataclass
class Plan:
    fetch: dict[str, str]  # page_id -> reason
    gone: list[str]
    unchanged: int
    skipped_no_ancestors: list[str]


def listed_ancestor_ids(summary: dict[str, Any], page_id: str) -> Optional[list[str]]:
    """Ancestor chain + the page itself, or None when the search result did
    not carry ``ancestors`` at all (``expand`` unsupported or ignored)."""
    if "ancestors" not in summary:
        return None
    return [str(a["id"]) for a in (summary.get("ancestors") or []) if a.get("id")] + [page_id]


def plan_reconcile(
    listed: dict[str, ListedPage],
    db_rows: dict[str, DbPage],
    *,
    allow_full_fetch: bool = False,
) -> Plan:
    fetch: dict[str, str] = {}
    skipped: list[str] = []
    unchanged = 0
    for pid, lp in listed.items():
        row = db_rows.get(pid)
        if row is None:
            fetch[pid] = REASON_NEW
        elif lp.ancestor_ids is None:
            # Cannot tell whether it moved. Fetching every such page is a
            # full re-crawl, so only do it when explicitly allowed.
            if allow_full_fetch:
                fetch[pid] = REASON_NO_ANCESTORS
            else:
                skipped.append(pid)
        elif row.ancestor_ids != lp.ancestor_ids:
            fetch[pid] = REASON_MOVED
        elif (row.title or "").strip() != (lp.title or "").strip():
            fetch[pid] = REASON_RENAMED
        else:
            unchanged += 1
    gone = sorted(set(db_rows) - set(listed))
    return Plan(fetch=fetch, gone=gone, unchanged=unchanged, skipped_no_ancestors=sorted(skipped))


async def _list_roots(
    client: ConfluenceClient, roots: dict[str, str], rps: float
) -> tuple[dict[str, ListedPage], list[dict[str, Any]]]:
    """Full listing of every root (since=None). Errors are collected per root
    (never raised): any error makes the listing incomplete, which the caller
    must treat as 'absence proves nothing'."""
    listed: dict[str, ListedPage] = {}
    errors: list[dict[str, Any]] = []
    limiter = RateLimiter(rps)
    async with client.bulk_client() as http_client:
        for root_id, label in roots.items():
            start = 0
            seen: set[str] = set()
            while True:
                try:
                    data = await client.search_pages_incremental(
                        root_id,
                        since=None,
                        start=start,
                        limit=_PAGE_SIZE,
                        client=http_client,
                        limiter=limiter,
                        expand="ancestors",
                    )
                except Exception as exc:  # noqa: BLE001 — one bad root must not abort the others
                    logger.exception("docs inventory search failed root=%s start=%s", root_id, start)
                    errors.append({"page_id": None, "root_id": root_id, "error": str(exc)})
                    break
                results = data.get("results") or []
                if not results:
                    break
                new_ids = {str(r.get("id")) for r in results} - seen
                if not new_ids:
                    errors.append(
                        {"page_id": None, "root_id": root_id, "error": "pagination stalled (no new ids)"}
                    )
                    break
                seen |= new_ids
                for r in results:
                    pid = str(r.get("id"))
                    listed.setdefault(
                        pid,
                        ListedPage(
                            page_id=pid,
                            title=str(r.get("title") or ""),
                            ancestor_ids=listed_ancestor_ids(r, pid),
                            root_id=root_id,
                            root_label=label,
                        ),
                    )
                if len(results) < _PAGE_SIZE:
                    break
                start += _PAGE_SIZE
    return listed, errors


async def _fetch_and_write(
    client: ConfluenceClient,
    *,
    source_type: str,
    space_key: str,
    pages: list[ListedPage],
    raw_dir: Path,
    rps: float,
    tz_name: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    written: list[str] = []
    errors: list[dict[str, Any]] = []
    limiter = RateLimiter(rps)
    base_url = client._base_url
    async with client.bulk_client() as http_client:
        for lp in pages:
            try:
                full = await client.get_page_full(lp.page_id, client=http_client, limiter=limiter)
                _write_page(
                    source_type=source_type,
                    full=full,
                    root_label=lp.root_label,
                    space_key=space_key,
                    base_url=base_url,
                    tz_name=tz_name,
                    raw_dir=raw_dir,
                )
                written.append(lp.page_id)
            except Exception as exc:  # noqa: BLE001 — one bad page must not kill the batch
                logger.exception("docs inventory fetch failed page_id=%s", lp.page_id)
                errors.append({"page_id": lp.page_id, "root_id": lp.root_id, "error": str(exc)})
    return written, errors


def _load_db_rows(source_type: str) -> dict[str, DbPage]:
    with session_scope() as session:
        rows = session.execute(
            select(Document.external_id, Document.title, Document.metadata_).where(
                Document.source_type == source_type, Document.status == "active"
            )
        ).all()
    out: dict[str, DbPage] = {}
    for ext, title, meta in rows:
        anc = (meta or {}).get("ancestor_ids")
        out[str(ext)] = DbPage(
            external_id=str(ext),
            title=title or "",
            ancestor_ids=[str(a) for a in anc] if isinstance(anc, list) and anc else None,
        )
    return out


def _archive_documents(source_type: str, ids: list[str]) -> list[str]:
    archived: list[str] = []
    with session_scope() as session:
        rows = (
            session.execute(
                select(Document).where(
                    Document.source_type == source_type,
                    Document.external_id.in_(ids),
                    Document.status == "active",
                )
            )
            .scalars()
            .all()
        )
        for doc in rows:
            doc.status = "archived"
            archived.append(doc.external_id)
    return archived


def _move_raw_aside(raw_dir: Path, source_type: str, ids: list[str]) -> int:
    """run_ingest rescans every raw file and restores an archived document
    whose file is still present (ingest/pipeline.py, 'archived' restore), so
    an archive only sticks if the raw file leaves the scanned directory.
    Moved, not deleted: the adapters glob only ``*.md`` directly in the
    source dir, so ``.archived/`` is ignored and the file stays recoverable."""
    src_dir = raw_dir / source_type
    dst_dir = src_dir / ARCHIVE_SUBDIR
    moved = 0
    for pid in ids:
        f = src_dir / f"confluence_{pid}.md"
        if f.is_file():
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst_dir / f.name))
            moved += 1
    return moved


@contextmanager
def _inventory_lock() -> Iterator[bool]:
    conn = get_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquired = bool(
            conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _INVENTORY_LOCK_KEY}).scalar()
        )
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _INVENTORY_LOCK_KEY})
    finally:
        conn.close()


def run_docs_inventory(
    raw_dir: str | Path,
    *,
    sources: Optional[list[str]] = None,
    dry_run: bool = False,
    ingest: bool = True,
    archive: bool = True,
    max_archive_ratio: float = DEFAULT_MAX_ARCHIVE_RATIO,
    allow_full_fetch: bool = False,
) -> dict[str, Any]:
    """Reconcile confluence_docs / tech_repo with Confluence (see module doc).

    Archive guards — an absence from the listing is only evidence of a move
    or deletion when the listing is trustworthy, so archiving is skipped when
    (a) any root errored, (b) ``archive`` is False, or (c) the candidates
    exceed ``max_archive_ratio`` of the active documents (a wrong root list
    or a Confluence outage must not mass-archive the index). The candidates
    are always returned under ``would_archive``.
    """
    raw_root = Path(raw_dir)
    settings = get_settings()
    client = ConfluenceClient(settings)
    source_types = sources or list(_SOURCE_DEFS.keys())
    report: dict[str, Any] = {"dry_run": dry_run, "sources": {}}

    with _inventory_lock() as acquired:
        if not acquired:
            logger.warning("docs inventory already running elsewhere (advisory lock held) — skipping")
            return {"dry_run": dry_run, "skipped": True, "reason": "already_running", "sources": {}}

        for st in source_types:
            if st not in _SOURCE_DEFS:
                report["sources"][st] = {"skipped": True, "reason": "unknown source_type"}
                continue
            sd = _SOURCE_DEFS[st]
            listed, list_errors = asyncio.run(_list_roots(client, sd["roots"], settings.confluence_rate_limit_rps))
            db_rows = _load_db_rows(st)
            plan = plan_reconcile(listed, db_rows, allow_full_fetch=allow_full_fetch)

            by_reason: dict[str, int] = {}
            for reason in plan.fetch.values():
                by_reason[reason] = by_reason.get(reason, 0) + 1

            written: list[str] = []
            fetch_errors: list[dict[str, Any]] = []
            if plan.fetch and not dry_run:
                written, fetch_errors = asyncio.run(
                    _fetch_and_write(
                        client,
                        source_type=st,
                        space_key=sd["space_key"],
                        pages=[listed[p] for p in sorted(plan.fetch)],
                        raw_dir=raw_root,
                        rps=settings.confluence_rate_limit_rps,
                        tz_name=settings.confluence_timezone,
                    )
                )

            archive_skipped: Optional[str] = None
            ratio = (len(plan.gone) / len(db_rows)) if db_rows else 0.0
            if plan.gone:
                if list_errors:
                    archive_skipped = "listing_errors"
                elif not archive:
                    archive_skipped = "archive_disabled"
                elif ratio > max_archive_ratio:
                    archive_skipped = "ratio_exceeded"
            archived: list[str] = []
            moved_raw = 0
            if plan.gone and archive_skipped is None and not dry_run:
                archived = _archive_documents(st, plan.gone)
                moved_raw = _move_raw_aside(raw_root, st, archived)

            report["sources"][st] = {
                "listed": len(listed),
                "active_in_db": len(db_rows),
                "unchanged": plan.unchanged,
                "fetch_planned": len(plan.fetch),
                "fetch_by_reason": by_reason,
                "fetch_written": len(written),
                "fetch_errors": fetch_errors,
                "listing_errors": list_errors,
                "skipped_no_ancestors": len(plan.skipped_no_ancestors),
                "would_archive": plan.gone,
                "would_archive_ratio": round(ratio, 4),
                "archived": sorted(archived),
                "archive_skipped": archive_skipped,
                "raw_moved_aside": moved_raw,
            }
            # Per-source progress line: the final report only prints at the
            # very end, and a killed run would otherwise leave nothing.
            logger.info(
                "source_done %s",
                {
                    k: (len(v) if isinstance(v, (list, dict)) else v)
                    for k, v in {"source_type": st, **report["sources"][st]}.items()
                },
            )

        if not dry_run and ingest:
            from app.embed.job import embed_pending_chunks
            from app.ingest.pipeline import run_ingest

            report["ingest"] = run_ingest(raw_root, sources=[s for s in source_types if s in _SOURCE_DEFS])
            try:
                report["embed"] = embed_pending_chunks()
            except Exception as exc:  # noqa: BLE001 — chunks stay FTS-searchable and are embedded next run
                logger.exception("embed_pending_chunks failed after docs inventory ingest")
                report["embed_error"] = str(exc)
    return report
