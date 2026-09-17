"""Confluence *map* sync: structure-only index (title/URL/breadcrumb, no
body) for spaces CI-TEC references but does not fully ingest.

Why a separate module instead of extending app.confluence.sync: that
module's `_SOURCE_DEFS`/`sync()`/`_crawl_source()` assume exactly one
space_key per source_type and one shared cursor — true for confluence_docs
(LOOKIN) and tech_repo (TechRepo) today, but confluence_map spans multiple
independent spaces that each need their own cursor (a page in DevOps001
being "since last sync" has nothing to do with CLDENG's cursor). Cloning the
per-source-def/per-source-id pattern here instead of overloading sync.py's
frozen single-space contract (see that module's docstring) keeps the
already-live prod confluence_docs/tech_repo cron untouched.

Reuses sync.py's pure helpers (format_cursor/version_date/page_url/
directory_breadcrumb) and app.confluence.client (ConfluenceClient,
RateLimiter, build_incremental_cql) rather than reimplementing them.

Output contract (frontmatter field names) consumed by
app.ingest.adapters.iter_confluence_map() — do not change without updating
that adapter together. Also produced by (must match)
scripts/migrate_confluence_map_from_skill_index.py, which bootstraps the 4
whole-space sources below (LOOKIN/TechRepo/ServiceExcellenceTeam/ICLOUDUT)
from the citec-mcp-workbench skill's already-crawled page indices and seeds
their cursors — so this module's first live run only asks Confluence for
pages changed *after* that snapshot instead of re-crawling ~26,000 pages
from scratch.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from sqlalchemy import text

from app.confluence.client import ConfluenceClient, RateLimiter, build_incremental_cql
from app.confluence.sync import (
    CrawlResult,
    WrittenPage,
    _now,
    _sanitize_line_value,
    directory_breadcrumb,
    page_url,
    version_date,
)
from app.db.models import Source
from app.db.session import get_engine, session_scope
from app.settings import get_settings

logger = logging.getLogger("citec.confluence.map_sync")

_PAGE_SIZE = 50

# Arbitrary but stable key for a Postgres advisory lock guarding sync_map()
# run-exclusivity. Needed because this function has two independent
# callers that share no process/filesystem: scripts/map_sync.sh's
# `docker compose exec` CLI invocation (flock'd at the shell level, but
# only against *itself*) and the admin-triggered HTTP endpoint (run inside
# the api container's own thread pool, via the Redis job queue/worker —
# see app.routers.confluence_map). Without this, a manual/cron run and an
# admin-triggered run could race on the same source_id's cursor/checkpoint.
_SYNC_LOCK_KEY = 861_234_501

# Curated roots per space — narrow subtrees only (incident/tech-support
# relevant), same "don't ingest whole space" policy as sync.py's
# CONFLUENCE_DOCS_ROOTS/TECHREPO_ROOTS. Whole-space crawl would also pull
# other teams' 손익 관리/팀 KPI/해외법인 업무/Personal Space/999. FreeSpace —
# not CI-TEC's business and pure noise for incident lookup.
#
# Approved 2026-09-16 (박재화) after sampling each space's actual content via
# confluence-mcp getChild (see conversation) — content shape varies a lot:
# CLDENG's KDB root alone has 100s of pages titled as literal symptom/error
# strings (e.g. "[HW] CPU Uncorrectable Machine Check Exception"), so a
# title-only index is already high-value there; others (Openstack101,
# EMCloud) are much thinner.
#
# sysops (MSP인프라운영팀) intentionally excluded for now: its home subtree
# only has 2 children (과제 진행 현황/손익 관리), no issue/KDB-shaped content
# as of this date. Add a roots entry here once that space grows one.
#
# 2026-09-16 (박재화, 2차 결정): LOOKIN/TechRepo/ServiceExcellenceTeam/
# ICLOUDUT는 "나머지 부분만 1회성 마이그레이션"이 아니라 **전체 공간을
# confluence_map으로 상시 포함**하고 매일 증분 갱신한다 — root은 해당 공간의
# 홈/최상위 페이지 하나(= 전체가 그 밑에 있으므로 사실상 "전체 공간" 크롤).
# LOOKIN/TechRepo는 이미 confluence_docs/tech_repo(4+5개 서브트리)로 본문
#전체가 들어간 페이지도 있는데, 그 페이지들도 이 맵에 C등급 포인터로 중복
# 등록된다 — 검색 랭킹엔 무해(A등급이 항상 우선)하고, 매번 실시간이라 예전
# "1회성 마이그레이션 스냅샷이 stale해지는" 문제 자체가 없어진다(예:
# 스냅샷과 실제 사이 326건 격차 같은 것). 홈페이지 자기 자신은 `ancestor=`
# CQL 특성상 결과에 안 잡힌다(자손만 반환) — confluence_docs/tech_repo도
# 동일한 특성이라 새로운 제약이 아니다.
MAP_SOURCE_DEFS: dict[str, dict[str, Any]] = {
    "confluence_map_lookin": {
        "space_key": "LOOKIN",
        "space_name": "CI-TEC",
        "roots": {
            "222532724": "전체 공간 (CI-TEC Home)",
        },
    },
    "confluence_map_techrepo": {
        "space_key": "TechRepo",
        "space_name": "[클라우드] 테크리포(Tech-Repository)",
        "roots": {
            "31951116": "전체 공간 (테크리포 Home)",
        },
    },
    "confluence_map_serviceexcellenceteam": {
        "space_key": "ServiceExcellenceTeam",
        "space_name": "서비스일류화팀",
        "roots": {
            "2001257717": "전체 공간 (서비스일류화팀 Home)",
        },
    },
    "confluence_map_icloudut": {
        "space_key": "ICLOUDUT",
        "space_name": "Cloud Umbrella Team",
        "roots": {
            "230553963": "전체 공간 (SCP Umbrella Team Home)",
        },
    },
    "confluence_map_devops001": {
        "space_key": "DevOps001",
        "space_name": "SCP인프라운영팀",
        "roots": {
            "601879661": "005. 이슈/문제/KDB/SOP",
            "468085983": "006. SCP CASE study",
            "370644108": "★★ SCP (SCP SRE + SCP NW Share) ★★",
        },
    },
    "confluence_map_openstack101": {
        "space_key": "Openstack101",
        "space_name": "OPENSTACK PLATFORM",
        "roots": {
            "1148203906": "knowledge base",
            "2318695720": "9. 팀 ISSUE 관리",
            "1176274225": "Nuri 운영구성",
            "1204002143": "Nuri 운영 관련",
        },
    },
    "confluence_map_cldeng": {
        "space_key": "CLDENG",
        "space_name": "MSP인프라기술그룹",
        "roots": {
            "289411381": "KB/SOP 검색",
            "271492877": "문제 해결 문서 (KDB)",
            "271779122": "004. 이슈,장애 관리",
            "271779763": "006. 기술&자동화",
            "271786535": "007. HW 운영 (서버HW/가상화/스토리지)",
            "331893132": "009. 통합백업",
            "561906785": "119. (★)DR 전환 및 비상가동 절차(★)",
        },
    },
    "confluence_map_dftrts": {
        "space_key": "DFTRTS",
        "space_name": "기술검증그룹",
        "roots": {
            "145822951": "기술자료",
            "966044388": "인프라설계검증",
            "958711159": "하드웨어분석",
        },
    },
    "confluence_map_emcloud": {
        "space_key": "EMCloud",
        "space_name": "통합Managed Infra서비스팀",
        "roots": {
            "271034968": "문제 해결 문서",
            "184265267": "7. Cloud Engineering(Shared Service)",
        },
    },
}


def build_frontmatter_confluence_map(
    *,
    space_key: str,
    space_name: str,
    root_label: str,
    page_id: str,
    title: str,
    url: str,
    path_breadcrumb: str,
    last_modified: str,
    is_folder: bool,
) -> str:
    lines = [
        "---",
        "구분 : 컨플루언스맵",
        f"공간명 : {space_name}",
        f"space_key : {space_key}",
        f"루트 : {root_label}",
        f"Page ID : {page_id}",
        f"제목 : {_sanitize_line_value(title)}",
        f"URL : {url}",
        f"경로 : {_sanitize_line_value(path_breadcrumb)}",
        f"최종수정일 : {last_modified}",
        f"유형 : {'폴더' if is_folder else '문서'}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def _is_folder_title(title: str) -> bool:
    """Heuristic for "이정표" container pages vs real content — matches the
    numbered-menu/divider naming convention seen across every sampled space
    (e.g. "005. 이슈/문제/KDB/SOP", "998. MSP인프라기술그룹 소통함",
    "----------------------------------------------", "999. FreeSpace").
    False negatives (a real KDB article that happens to start with a
    number) are harmless here — this only affects the 유형 label, not
    whether the page is indexed."""
    t = title.strip()
    if not t:
        return True
    if set(t) <= {"-"}:
        return True
    if t[0].isdigit() and ("." in t.split(" ")[0] or t.split(" ")[0].rstrip(".").isdigit()):
        return True
    return t.upper() in {"FREESPACE", "999. FREESPACE"}


def _write_map_page(
    *,
    meta: dict[str, Any],
    root_label: str,
    space_key: str,
    space_name: str,
    base_url: str,
    tz_name: str,
    raw_dir: Path,
) -> WrittenPage:
    page_id = str(meta.get("id"))
    title = str(meta.get("title") or "")
    version = meta.get("version") or {}
    last_modified = version_date(version.get("when"), tz_name) if version.get("when") else ""
    url = page_url(base_url, page_id)
    ancestors = meta.get("ancestors") or []
    path_breadcrumb = directory_breadcrumb(ancestors, title)

    front = build_frontmatter_confluence_map(
        space_key=space_key,
        space_name=space_name,
        root_label=root_label,
        page_id=page_id,
        title=title,
        url=url,
        path_breadcrumb=path_breadcrumb,
        last_modified=last_modified,
        is_folder=_is_folder_title(title),
    )

    out_dir = raw_dir / "confluence_map"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"confluence_map_{page_id}.md"
    out_path.write_text(front + "\n" + path_breadcrumb + "\n", encoding="utf-8")
    return WrittenPage(page_id=page_id, path=out_path)


async def _crawl_map_source(
    client: ConfluenceClient,
    *,
    source_id: str,
    roots: dict[str, str],
    space_key: str,
    space_name: str,
    since: Optional[datetime],
    raw_dir: Path,
    max_pages_per_root: Optional[int],
    rps: float,
    tz_name: str,
    use_checkpoint: bool = False,
    progress: Optional[dict[str, Any]] = None,
) -> CrawlResult:
    from app.confluence.sync import format_cursor  # avoid import-time cycle risk

    base_url = client._base_url
    since_str = format_cursor(since, tz_name) if since else None
    limiter = RateLimiter(rps)
    written: list[WrittenPage] = []
    errors: list[dict[str, Any]] = []
    cql_log: list[str] = []
    checkpoints = _read_checkpoints(source_id) if use_checkpoint else {}

    async with client.bulk_client() as http_client:
        for root_id, root_label in roots.items():
            root_checkpoint = checkpoints.get(root_id) or {}
            if use_checkpoint and root_checkpoint.get("done"):
                note = f"root={root_id} skipped: already completed this cycle (checkpoint)"
                logger.info("confluence map %s since=%s", note, since_str)
                cql_log.append(note)
                continue
            start = int(root_checkpoint.get("start") or 0) if use_checkpoint else 0
            if progress is not None:
                _set_progress(
                    **progress,
                    root_id=root_id,
                    root_label=root_label,
                    pages_done_this_root=start,
                    updated_at=_now().isoformat(),
                )
            if start:
                note = f"root={root_id} resumed from checkpoint start={start}"
                logger.info(
                    "confluence map %s (previous run was interrupted mid-root)", note
                )
                cql_log.append(note)
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
                    # Prod evidence (2026-09-16 dry-run): an unhandled 401 on
                    # this call took down every remaining source_id in the
                    # run (sync_map()'s per-source_id loop never even
                    # reached confluence_map_techrepo/...) — only the
                    # per-page fetch below was ever guarded. Same fix
                    # applied to the sibling app.confluence.sync._crawl_source.
                    #
                    # Leave any saved checkpoint untouched here — the next
                    # run (or resumed process) retries from the last
                    # successfully completed page batch instead of root 0.
                    logger.exception(
                        "confluence map search failed root=%s start=%s cql=%r — abandoning this root",
                        root_id, start, cql,
                    )
                    errors.append({"page_id": None, "root_id": root_id, "error": str(exc)})
                    break
                results = data.get("results") or []
                cql_note = f"cql={cql!r} start={start} got={len(results)}"
                cql_log.append(cql_note)
                logger.info("confluence map search %s", cql_note)
                if not results:
                    if use_checkpoint:
                        _save_root_checkpoint(source_id, root_id, done=True)
                    break
                new_ids = {str(r.get("id")) for r in results} - seen_ids
                if not new_ids:
                    logger.error(
                        "confluence map pagination made no progress root=%s start=%s — "
                        "aborting this root",
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
                        meta = await client.get_page_meta(
                            page_id, client=http_client, limiter=limiter
                        )
                        wp = _write_map_page(
                            meta=meta,
                            root_label=root_label,
                            space_key=space_key,
                            space_name=space_name,
                            base_url=base_url,
                            tz_name=tz_name,
                            raw_dir=raw_dir,
                        )
                        written.append(wp)
                        root_written += 1
                    except Exception as exc:  # noqa: BLE001 — one bad page must not kill the batch
                        logger.exception(
                            "confluence map sync failed page_id=%s root=%s space=%s",
                            page_id, root_id, space_key,
                        )
                        errors.append(
                            {"page_id": page_id, "root_id": root_id, "error": str(exc)}
                        )
                if len(results) < _PAGE_SIZE:
                    if use_checkpoint:
                        _save_root_checkpoint(source_id, root_id, done=True)
                    break
                start += _PAGE_SIZE
                if use_checkpoint:
                    # Persisted every _PAGE_SIZE (50) pages, not per-page —
                    # a crash/restart mid-batch replays at most one batch
                    # (~a few minutes at the confluence rate limit) instead
                    # of the whole root.
                    _save_root_checkpoint(source_id, root_id, start=start)
                if progress is not None:
                    _set_progress(
                        **progress,
                        root_id=root_id,
                        root_label=root_label,
                        pages_done_this_root=start,
                        updated_at=_now().isoformat(),
                    )

    return CrawlResult(written=written, errors=errors, cql_log=cql_log)


@contextmanager
def _sync_run_lock() -> Iterator[bool]:
    """Postgres session-level advisory lock — held for the whole sync_map()
    call. Yields True if acquired (caller should proceed), False if some
    other sync_map() invocation already holds it (caller should skip).
    Uses AUTOCOMMIT so this doesn't sit as an open idle transaction for the
    entire (possibly many-hour) crawl — advisory locks are tied to the
    underlying connection/session, not to a transaction."""
    conn = get_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _SYNC_LOCK_KEY}).scalar())
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SYNC_LOCK_KEY})
    finally:
        conn.close()


_PROGRESS_KEY = "citec:confluence_map:progress"


def _progress_redis():
    import redis

    settings = get_settings()
    url = getattr(settings, "redis_url", None) or "redis://localhost:6379/0"
    return redis.from_url(url, decode_responses=True, socket_connect_timeout=3)


def _set_progress(**fields: Any) -> None:
    """Best-effort "what's happening right now" pointer for the admin
    status endpoint — answers "which space, which root, how far in" without
    the caller having to infer it from Source.config['checkpoint'] (which
    only says where each source *left off*, not which one is currently
    active). Never raises: this is telemetry, not correctness — a Redis
    hiccup must not take down the actual crawl."""
    try:
        r = _progress_redis()
        r.hset(_PROGRESS_KEY, mapping={k: "" if v is None else str(v) for k, v in fields.items()})
        r.expire(_PROGRESS_KEY, 3600)  # self-heals if a crash skips _clear_progress
    except Exception:  # noqa: BLE001
        logger.debug("confluence map progress pointer update failed (non-fatal)", exc_info=True)


def _clear_progress() -> None:
    try:
        _progress_redis().delete(_PROGRESS_KEY)
    except Exception:  # noqa: BLE001
        pass


def get_progress() -> Optional[dict[str, Any]]:
    """Read the current/last-known progress pointer. Combine with
    is_sync_running() in callers — a non-empty pointer while not running
    just means "this is where the last run stopped" (crash or clean finish)."""
    try:
        data = _progress_redis().hgetall(_PROGRESS_KEY)
        return data or None
    except Exception:  # noqa: BLE001
        return None


def is_sync_running() -> bool:
    """True if some sync_map() call currently holds the run lock. Used by
    the admin status endpoint and by restart_api_if_idle.sh (via a tiny
    inline script) to avoid restarting the api container mid-crawl when the
    crawl was triggered from the admin UI rather than the CLI (the CLI path
    is separately caught by that script's /proc cmdline grep).

    Reads pg_locks directly rather than attempting-then-releasing
    pg_try_advisory_lock: that acquire-probe pattern would itself briefly
    hold the lock, and a real sync_map() call trying to start in that same
    instant would see the lock as taken and skip with "already_running" —
    a status/health check silently cancelling a real run. _SYNC_LOCK_KEY
    fits in 32 bits, so the single-bigint pg_advisory_lock() form is stored
    as classid=0, objid=key, objsubid=1 (verified against pg_locks; the
    two-int32 form used by *_lock(a, b) would show objsubid=2 instead)."""
    conn = get_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        return bool(
            conn.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM pg_locks"
                    "  WHERE locktype = 'advisory' AND classid = 0"
                    "    AND objid = :k AND objsubid = 1 AND granted"
                    ")"
                ),
                {"k": _SYNC_LOCK_KEY},
            ).scalar()
        )
    finally:
        conn.close()


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
                    type="confluence_map",
                    name=f"Confluence Map {space_key}",
                    config={"space_key": space_key, "roots": roots},
                    status="active",
                )
            )


def _advance_cursor(source_id: str, run_started: datetime) -> None:
    from app.confluence.sync import _LAST_SYNC_MARGIN

    new_cursor = run_started - _LAST_SYNC_MARGIN
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src:
            src.last_sync_at = new_cursor


def _read_checkpoints(source_id: str) -> dict[str, dict[str, Any]]:
    """Per-root crawl progress for the *current* (not-yet-advanced) since
    cursor, keyed by root_id: {"start": N} (in progress, resume at page
    offset N) or {"done": True} (this root's incremental fetch already ran
    to completion this cycle — don't redo it if a later root in the same
    source_id gets interrupted). Cleared by _clear_checkpoints() once the
    whole source_id completes and its cursor advances past this cycle."""
    with session_scope() as session:
        src = session.get(Source, source_id)
        if not src:
            return {}
        return dict((src.config or {}).get("checkpoint") or {})


def _save_root_checkpoint(
    source_id: str, root_id: str, *, start: Optional[int] = None, done: bool = False
) -> None:
    with session_scope() as session:
        src = session.get(Source, source_id)
        if not src:
            return
        config = dict(src.config or {})
        checkpoint = dict(config.get("checkpoint") or {})
        checkpoint[root_id] = {"done": True} if done else {"start": start}
        config["checkpoint"] = checkpoint
        src.config = config  # reassign (not mutate) so SQLAlchemy detects the JSONB change


def _clear_checkpoints(source_id: str) -> None:
    with session_scope() as session:
        src = session.get(Source, source_id)
        if not src or "checkpoint" not in (src.config or {}):
            return
        config = dict(src.config)
        config.pop("checkpoint", None)
        src.config = config


def seed_cursor(source_id: str, seeded_at: datetime) -> None:
    """Used by scripts/migrate_confluence_map_from_skill_index.py right
    after a one-time bootstrap import: sets last_sync_at so the *next* live
    sync_map() run only asks Confluence for pages changed since the
    migration, instead of re-crawling everything the migration already
    covered (this matters most for ICLOUDUT's ~14,600 pages).

    Also drops any leftover per-root checkpoint (see _save_root_checkpoint):
    a stale {"start": N} paired with a freshly-seeded cursor would resume at
    offset N of a completely different (post-migration) result set and
    silently skip everything before it."""
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src:
            src.last_sync_at = seeded_at
            if "checkpoint" in (src.config or {}):
                config = dict(src.config)
                config.pop("checkpoint", None)
                src.config = config
        else:
            session.add(
                Source(
                    id=source_id,
                    type="confluence_map",
                    name=source_id,
                    config={"seeded_by": "migrate_confluence_map_from_skill_index"},
                    status="active",
                    last_sync_at=seeded_at,
                )
            )


def sync_map(
    raw_dir: str | Path,
    *,
    dry_run: bool = False,
    source_ids: Optional[list[str]] = None,
    max_pages_per_root: Optional[int] = None,
    root_id: Optional[str] = None,
    run_ingest_and_embed: bool = True,
) -> dict[str, Any]:
    """Poll each mapped space for pages changed since its own last_sync_at,
    write structure-only frontmatter to data/raw/confluence_map/, then
    (unless dry_run) run_ingest + embed_pending_chunks and advance each
    space's cursor independently.

    Mirrors app.confluence.sync.sync()'s dry-run/error-rate/cursor-advance
    policy exactly (see that function's docstring for the rationale) —
    intentionally not shared code because of the per-space cursor keying
    difference explained in this module's docstring.

    Thin wrapper around _sync_map_locked(): acquires _sync_run_lock() so a
    concurrent call (cron CLI vs. admin-triggered job, or two admin clicks)
    never runs two crawls over the same source_id at once. Returns a
    {"skipped": True, "reason": "already_running", "sources": {}} stub
    instead of racing when the lock is already held.
    """
    with _sync_run_lock() as acquired:
        if not acquired:
            logger.warning(
                "confluence map sync already running elsewhere (advisory lock held) — "
                "skipping this invocation"
            )
            return {"dry_run": dry_run, "skipped": True, "reason": "already_running", "sources": {}}
        return _sync_map_locked(
            raw_dir,
            dry_run=dry_run,
            source_ids=source_ids,
            max_pages_per_root=max_pages_per_root,
            root_id=root_id,
            run_ingest_and_embed=run_ingest_and_embed,
        )


def _sync_map_locked(
    raw_dir: str | Path,
    *,
    dry_run: bool = False,
    source_ids: Optional[list[str]] = None,
    max_pages_per_root: Optional[int] = None,
    root_id: Optional[str] = None,
    run_ingest_and_embed: bool = True,
) -> dict[str, Any]:
    raw_root = Path(raw_dir)
    settings = get_settings()
    client = ConfluenceClient(settings)
    defs = source_ids or list(MAP_SOURCE_DEFS.keys())
    run_started = _now()

    stats: dict[str, Any] = {"dry_run": dry_run, "started_at": run_started.isoformat(), "sources": {}}
    _set_progress(
        run_started_at=run_started.isoformat(),
        source_index=0,
        source_total=len(defs),
        source_id=None,
        space_key=None,
        root_id=None,
        root_label=None,
        pages_done_this_root=None,
        updated_at=_now().isoformat(),
    )

    try:
        return _sync_map_body(
            defs=defs,
            raw_root=raw_root,
            settings=settings,
            client=client,
            run_started=run_started,
            stats=stats,
            dry_run=dry_run,
            max_pages_per_root=max_pages_per_root,
            root_id=root_id,
            run_ingest_and_embed=run_ingest_and_embed,
        )
    finally:
        _clear_progress()


def _sync_map_body(
    *,
    defs: list[str],
    raw_root: Path,
    settings: Any,
    client: ConfluenceClient,
    run_started: datetime,
    stats: dict[str, Any],
    dry_run: bool,
    max_pages_per_root: Optional[int],
    root_id: Optional[str],
    run_ingest_and_embed: bool,
) -> dict[str, Any]:
    for idx, source_id in enumerate(defs, start=1):
        if source_id not in MAP_SOURCE_DEFS:
            logger.error("unknown confluence_map source_id=%s (valid: %s)", source_id, list(MAP_SOURCE_DEFS))
            stats["sources"][source_id] = {"skipped": True, "reason": "unknown source_id"}
            continue
        sd = MAP_SOURCE_DEFS[source_id]
        roots = sd["roots"]
        if root_id is not None:
            if root_id not in roots:
                stats["sources"][source_id] = {"skipped": True, "reason": "root_id not in this source"}
                continue
            roots = {root_id: roots[root_id]}

        _ensure_source_row(source_id, sd["space_key"], sd["roots"])
        since = _read_cursor(source_id)
        truncated = max_pages_per_root is not None or root_id is not None
        # Only checkpoint/resume full, untruncated crawls — a `--max-pages`/
        # `--root-id` smoke test must never read or clobber the real
        # in-progress checkpoint for this source.
        use_checkpoint = not truncated

        _set_progress(
            run_started_at=run_started.isoformat(),
            source_index=idx,
            source_total=len(defs),
            source_id=source_id,
            space_key=sd["space_key"],
            root_id=None,
            root_label=None,
            pages_done_this_root=None,
            updated_at=_now().isoformat(),
        )
        result = asyncio.run(
            _crawl_map_source(
                client,
                source_id=source_id,
                roots=roots,
                space_key=sd["space_key"],
                space_name=sd["space_name"],
                since=since,
                raw_dir=raw_root,
                max_pages_per_root=max_pages_per_root,
                rps=settings.confluence_rate_limit_rps,
                tz_name=settings.confluence_timezone,
                use_checkpoint=use_checkpoint,
                progress={
                    "run_started_at": run_started.isoformat(),
                    "source_index": idx,
                    "source_total": len(defs),
                    "source_id": source_id,
                    "space_key": sd["space_key"],
                },
            )
        )
        total_attempted = len(result.written) + len(result.errors)
        error_rate = (len(result.errors) / total_attempted) if total_attempted else 0.0
        error_rate_ok = not result.errors or error_rate <= settings.confluence_max_error_rate
        can_advance = not dry_run and not truncated and error_rate_ok
        if can_advance:
            _advance_cursor(source_id, run_started)
            if use_checkpoint:
                _clear_checkpoints(source_id)

        stats["sources"][source_id] = {
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

        stats["ingest"] = run_ingest(raw_root, sources=["confluence_map"])
        stats["embed"] = embed_pending_chunks()

    logger.info("confluence map sync complete dry_run=%s stats=%s", dry_run, stats)
    return stats
