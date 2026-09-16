"""Confluence incremental sync: poll LOOKIN confluence_docs + TechRepo,
re-extract changed pages into data/raw/<source_type>/, then ingest+embed.

Ported logic (per the sync request prompt — reuse, don't reimplement):
- storage_html_to_text(): confluence-docs skill scripts/extract_page_text.py
- clean_body(): MY-OS corpus/build/export_confluence_docs.py

Frozen output contract: the frontmatter shape produced here is consumed by
app.ingest.adapters.iter_confluence_docs()/iter_tech_repo() — do not change
field names/order without updating those adapters together.

**Never verified against live Confluence** (dev system has no Confluence
access): the CQL date format, the `ancestor=` clause syntax, and pagination
behavior at TechRepo scale (~971 pages). See docs/CONFLUENCE_SYNC.md for the
first-run --dry-run checklist an operator must run on the prod server.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.confluence.client import ConfluenceClient, RateLimiter, build_incremental_cql
from app.db.models import Source
from app.db.session import session_scope
from app.settings import get_settings

logger = logging.getLogger("citec.confluence.sync")

CONFLUENCE_DOCS_ROOTS: dict[str, str] = {
    "222532692": "CI-TEC 소개",
    "377920133": "CI-TEC 과제",
    "131290561": "CI-TEC 기술지원",
    "178797461": "오픈스택 역량강화",
}

TECHREPO_ROOTS: dict[str, str] = {
    "1541715706": "CI-TEC의 구름다리",
    "133859925": "클라우드 장애 대응",
    "449603893": "클라우드 CSP별 상품서비스 비교",
    "289434485": "클라우드 테스트 시나리오 및 도구",
    "133859927": "교육 및 세미나",
}

_SOURCE_DEFS: dict[str, dict[str, Any]] = {
    "confluence_docs": {
        "source_id": "confluence_lookin_docs",
        "space_key": "LOOKIN",
        "roots": CONFLUENCE_DOCS_ROOTS,
    },
    "tech_repo": {
        "source_id": "confluence_techrepo",
        "space_key": "TechRepo",
        "roots": TECHREPO_ROOTS,
    },
}

# Clock-skew safety margin: don't trust "now" as the new cursor, since a
# page edited mid-run could land just before the run's end but never be
# picked up by a query that filters "lastmodified > run_end". Subtracting a
# margin re-scans a short overlap window on the next run instead of
# permanently missing it. Overlap is safe/cheap — content_hash makes
# re-ingesting an unchanged page a no-op (run_ingest → "skipped").
_LAST_SYNC_MARGIN = timedelta(minutes=5)

_PAGE_SIZE = 50


# --- storage_html_to_text / clean_body (ported, do not reimplement) ---


def storage_html_to_text(body: str) -> str:
    body = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|table|ul|ol|br)>", "\n", body)
    body = re.sub(r"(?i)<br\s*/?>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return body.strip()


_CDATA_TAIL = re.compile(r"\]\]>")
_INLINE_CSS_RULE = re.compile(r"^\s*\.[\w-]+\s*\{[^{}]*\}\s*$", re.M)


def clean_body(body: str) -> str:
    body = _CDATA_TAIL.sub("", body)
    body = _INLINE_CSS_RULE.sub("", body)
    return body


# --- cursor formatting ---


def format_cursor(dt: datetime, tz_name: str = "Asia/Seoul") -> str:
    """Render dt in Confluence CQL's `yyyy/MM/dd HH:mm` form, converted to
    the Confluence instance's timezone (default Asia/Seoul — the fixture's
    version.when carries +09:00). A naive dt is treated as UTC, matching
    how Source.last_sync_at (DateTime(timezone=True)) round-trips through
    the DB driver."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz_name))
    return local.strftime("%Y/%m/%d %H:%M")


def version_date(when: str, tz_name: str = "Asia/Seoul") -> str:
    """Confluence version.when (ISO8601 with offset) → YYYY-MM-DD for the
    최종수정일 frontmatter field, in the page's own (source) timezone."""
    dt = datetime.fromisoformat(when)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(ZoneInfo(tz_name)).date().isoformat()


def page_url(base_url: str, page_id: str) -> str:
    return f"{base_url.rstrip('/')}/pages/viewpage.action?pageId={page_id}"


def directory_breadcrumb(ancestors: list[dict[str, Any]], title: str) -> str:
    """`"A > B > C"` breadcrumb from the Confluence ancestors chain plus the
    page's own title — matches the real tech_repo corpus's 디렉토리 field
    exactly, including the trailing "> " when title is blank (joining an
    empty last segment naturally produces that)."""
    parts = [str(a.get("title") or "") for a in ancestors] + [title or ""]
    return " > ".join(parts)


def _sanitize_line_value(s: str) -> str:
    # Frontmatter is one "key : value" per line — an embedded newline would
    # corrupt the next field's parsing, so collapse to spaces defensively.
    return re.sub(r"\s+", " ", (s or "")).strip()


# --- frontmatter generation (frozen format — see module docstring) ---


def build_frontmatter_confluence_docs(
    *, space_key: str, folder: str, page_id: str, title: str, url: str, last_modified: str
) -> str:
    lines = [
        "---",
        "구분 : 컨플루언스",
        f"공간명 : {space_key}",
        f"폴더분류 : {folder}",
        f"Page ID : {page_id}",
        f"제목 : {_sanitize_line_value(title)}",
        f"URL : {url}",
        f"최종수정일 : {last_modified}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def build_frontmatter_tech_repo(
    *, space_key: str, directory: str, page_id: str, title: str, url: str, last_modified: str
) -> str:
    lines = [
        "---",
        "구분 : 컨플루언스",
        f"공간명 : {space_key}",
        f"디렉토리 : {_sanitize_line_value(directory)}",
        f"Page ID : {page_id}",
        f"제목 : {_sanitize_line_value(title)}",
        f"URL : {url}",
        f"최종수정일 : {last_modified}",
        "---",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class WrittenPage:
    page_id: str
    path: Path


@dataclass
class CrawlResult:
    written: list[WrittenPage]
    errors: list[dict[str, Any]]
    cql_log: list[str]


def _write_page(
    *,
    source_type: str,
    full: dict[str, Any],
    root_label: str,
    space_key: str,
    base_url: str,
    tz_name: str,
    raw_dir: Path,
) -> WrittenPage:
    page_id = str(full.get("id"))
    title = str(full.get("title") or "")
    version = full.get("version") or {}
    last_modified = version_date(version.get("when"), tz_name) if version.get("when") else ""
    url = page_url(base_url, page_id)
    body_storage = ((full.get("body") or {}).get("storage") or {}).get("value") or ""
    text = clean_body(storage_html_to_text(body_storage))
    ancestors = full.get("ancestors") or []

    if source_type == "confluence_docs":
        front = build_frontmatter_confluence_docs(
            space_key=space_key,
            folder=root_label,
            page_id=page_id,
            title=title,
            url=url,
            last_modified=last_modified,
        )
    else:
        directory = directory_breadcrumb(ancestors, title)
        space_display = str(ancestors[0].get("title") or space_key) if ancestors else space_key
        front = build_frontmatter_tech_repo(
            space_key=space_display,
            directory=directory,
            page_id=page_id,
            title=title,
            url=url,
            last_modified=last_modified,
        )

    out_dir = raw_dir / source_type
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"confluence_{page_id}.md"
    out_path.write_text(front + "\n" + text + "\n", encoding="utf-8")
    return WrittenPage(page_id=page_id, path=out_path)


async def _crawl_source(
    client: ConfluenceClient,
    *,
    source_type: str,
    roots: dict[str, str],
    space_key: str,
    since: Optional[datetime],
    raw_dir: Path,
    max_pages_per_root: Optional[int],
    rps: float,
    tz_name: str,
) -> CrawlResult:
    base_url = client._base_url
    since_str = format_cursor(since, tz_name) if since else None
    limiter = RateLimiter(rps)
    written: list[WrittenPage] = []
    errors: list[dict[str, Any]] = []
    cql_log: list[str] = []

    async with client.bulk_client() as http_client:
        for root_id, root_label in roots.items():
            start = 0
            root_written = 0
            seen_ids: set[str] = set()
            while True:
                if max_pages_per_root is not None and root_written >= max_pages_per_root:
                    break
                cql = build_incremental_cql(root_id, since=since_str)
                try:
                    data = await client.search_pages_incremental(
                        root_id,
                        since=since_str,
                        start=start,
                        limit=_PAGE_SIZE,
                        client=http_client,
                        limiter=limiter,
                    )
                except Exception as exc:  # noqa: BLE001 — a bad search call must not kill the whole crawl
                    # Prod evidence (2026-09-16, confluence_map's sibling
                    # crawl): an unhandled 401 on this call took down every
                    # remaining root/source in the run, not just this one —
                    # only the per-page fetch below was ever guarded.
                    logger.exception(
                        "confluence search failed root=%s start=%s cql=%r — abandoning this root",
                        root_id, start, cql,
                    )
                    errors.append({"page_id": None, "root_id": root_id, "error": str(exc)})
                    break
                results = data.get("results") or []
                cql_note = f"cql={cql!r} start={start} got={len(results)}"
                cql_log.append(cql_note)
                logger.info("confluence search %s", cql_note)
                if not results:
                    break
                new_ids = {str(r.get("id")) for r in results} - seen_ids
                if not new_ids:
                    logger.error(
                        "confluence pagination made no progress root=%s start=%s — "
                        "server may be ignoring start/limit, aborting this root",
                        root_id, start,
                    )
                    errors.append(
                        {"page_id": None, "root_id": root_id, "error": "pagination stalled (no new ids)"}
                    )
                    break
                seen_ids |= new_ids
                for summary in results:
                    if max_pages_per_root is not None and root_written >= max_pages_per_root:
                        break
                    page_id = str(summary.get("id"))
                    try:
                        full = await client.get_page_full(
                            page_id, client=http_client, limiter=limiter
                        )
                        wp = _write_page(
                            source_type=source_type,
                            full=full,
                            root_label=root_label,
                            space_key=space_key,
                            base_url=base_url,
                            tz_name=tz_name,
                            raw_dir=raw_dir,
                        )
                        written.append(wp)
                        root_written += 1
                    except Exception as exc:  # noqa: BLE001 — one bad page must not kill the batch
                        logger.exception(
                            "confluence sync failed page_id=%s root=%s source_type=%s",
                            page_id, root_id, source_type,
                        )
                        errors.append(
                            {"page_id": page_id, "root_id": root_id, "error": str(exc)}
                        )
                if len(results) < _PAGE_SIZE:
                    break
                start += _PAGE_SIZE

    return CrawlResult(written=written, errors=errors, cql_log=cql_log)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_cursor(source_id: str) -> Optional[datetime]:
    with session_scope() as session:
        src = session.get(Source, source_id)
        return src.last_sync_at if src else None


def _ensure_source_row(source_id: str, space_key: str, roots: dict[str, str]) -> None:
    with session_scope() as session:
        src = session.get(Source, source_id)
        if not src:
            session.add(
                Source(
                    id=source_id,
                    type="confluence",
                    name=f"Confluence {space_key}",
                    config={"space_key": space_key, "roots": roots},
                    status="active",
                )
            )


def _advance_cursor(source_id: str, run_started: datetime) -> None:
    new_cursor = run_started - _LAST_SYNC_MARGIN
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src:
            src.last_sync_at = new_cursor


def sync(
    raw_dir: str | Path,
    *,
    dry_run: bool = False,
    sources: Optional[list[str]] = None,
    max_pages_per_root: Optional[int] = None,
    root_id: Optional[str] = None,
    run_ingest_and_embed: bool = True,
) -> dict[str, Any]:
    """Poll Confluence for pages changed since each source's last_sync_at,
    re-extract to data/raw/<source_type>/, then (unless dry_run) run_ingest
    + embed_pending_chunks and advance the cursor.

    `root_id` restricts the crawl to a single root pageId across whichever
    `sources` were requested — meant for an operator's very first prod
    --dry-run, to see one root's worth of output before unleashing a full
    9-root crawl against a Confluence instance this code has never talked
    to live.
    """
    raw_root = Path(raw_dir)
    settings = get_settings()
    client = ConfluenceClient(settings)
    source_types = sources or list(_SOURCE_DEFS.keys())
    run_started = _now()

    stats: dict[str, Any] = {"dry_run": dry_run, "started_at": run_started.isoformat(), "sources": {}}

    for source_type in source_types:
        if source_type not in _SOURCE_DEFS:
            logger.error("unknown source_type=%s (valid: %s)", source_type, list(_SOURCE_DEFS))
            stats["sources"][source_type] = {"skipped": True, "reason": "unknown source_type"}
            continue
        sd = _SOURCE_DEFS[source_type]
        roots = sd["roots"]
        if root_id is not None:
            if root_id not in roots:
                stats["sources"][source_type] = {"skipped": True, "reason": "root_id not in this source"}
                continue
            roots = {root_id: roots[root_id]}

        _ensure_source_row(sd["source_id"], sd["space_key"], sd["roots"])
        since = _read_cursor(sd["source_id"])

        result = asyncio.run(
            _crawl_source(
                client,
                source_type=source_type,
                roots=roots,
                space_key=sd["space_key"],
                since=since,
                raw_dir=raw_root,
                max_pages_per_root=max_pages_per_root,
                rps=settings.confluence_rate_limit_rps,
                tz_name=settings.confluence_timezone,
            )
        )
        # A truncated crawl (--max-pages / --root-id) must NOT advance the
        # cursor — that's a deliberately partial operator test run, and
        # advancing would silently and permanently skip whatever wasn't
        # reached. Per-page errors are different: prod evidence (2026-09-15)
        # showed 1 transient 401 out of 5,483 pages was enough, under a
        # zero-tolerance rule, to force a full ~90min re-bootstrap of the
        # entire source on every single run forever — so a small error rate
        # is tolerated (CONFLUENCE_MAX_ERROR_RATE, default 1%) instead of
        # blocking the cursor outright. Failed page_ids stay in error_detail
        # either way so they're visible and checkable.
        truncated = max_pages_per_root is not None or root_id is not None
        total_attempted = len(result.written) + len(result.errors)
        error_rate = (len(result.errors) / total_attempted) if total_attempted else 0.0
        error_rate_ok = not result.errors or error_rate <= settings.confluence_max_error_rate
        can_advance = not dry_run and not truncated and error_rate_ok
        if can_advance and result.errors:
            logger.warning(
                "confluence cursor advancing despite %d error(s) (rate=%.4f <= %.4f) "
                "source_type=%s — failed page_ids=%s",
                len(result.errors), error_rate, settings.confluence_max_error_rate,
                source_type, [e.get("page_id") for e in result.errors],
            )
        if can_advance:
            _advance_cursor(sd["source_id"], run_started)

        stats["sources"][source_type] = {
            "written": len(result.written),
            "errors": len(result.errors),
            "error_rate": round(error_rate, 4),
            "error_detail": result.errors,
            "since": since.isoformat() if since else None,
            "cql_log": result.cql_log,
            "cursor_advanced": can_advance,
        }

    if not dry_run and run_ingest_and_embed:
        from app.embed.job import embed_pending_chunks
        from app.ingest.pipeline import run_ingest

        stats["ingest"] = run_ingest(raw_root, sources=source_types)
        stats["embed"] = embed_pending_chunks()

    logger.info("confluence sync complete dry_run=%s stats=%s", dry_run, stats)
    return stats
