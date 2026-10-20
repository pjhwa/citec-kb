"""DB-touching tests for app.confluence.sync: Source cursor/margin logic and
the full ingest+embed pipeline (insert → skipped → updated), run against a
throwaway scratch database — never the live citec_knowledge DB.

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
Postgres+pgvector instance with the alembic schema applied (CI's
DATABASE_URL is deliberately unreachable — port 1 — to keep pure-unit tests
DB-free; this file opts in separately). To set one up locally:

    docker exec <pg-container> psql -U citec -d postgres -c \
        "CREATE DATABASE citec_kb_test;"
    docker exec <pg-container> psql -U citec -d citec_kb_test -c \
        "CREATE EXTENSION IF NOT EXISTS vector;"
    cd apps/api && DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_kb_test \
        alembic upgrade head

Then run:
    CONFLUENCE_SYNC_TEST_DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_kb_test \
        pytest tests/test_confluence_sync_db.py -q

This file must never point at the same DB the live citec-kb-api container
uses (citec_knowledge on the shared dev postgres) — run_ingest()/
embed_pending_chunks() here operate with no source_type/document_id scoping
in some assertions and would otherwise touch real production rows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN
    os.environ.setdefault("CONFLUENCE_BASE_URL", "https://c.example.com")
    os.environ.setdefault("CONFLUENCE_USERNAME", "u")
    os.environ.setdefault("CONFLUENCE_PASSWORD", "p")

FIXTURES = Path(__file__).parent / "fixtures" / "confluence_sync"


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    """get_engine()/get_settings() are @lru_cache'd — must not leak a
    connection pool built against a different DATABASE_URL from a prior
    test module."""
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _cleanup_document(external_id: str, source_type: str = "confluence_docs") -> None:
    from sqlalchemy import delete

    from app.db.models import Chunk, Document, DocumentSection, Embedding
    from app.db.session import session_scope

    with session_scope() as session:
        doc = session.query(Document).filter_by(
            source_type=source_type, external_id=external_id
        ).one_or_none()
        if not doc:
            return
        chunk_ids = [c.id for c in session.query(Chunk.id).filter_by(document_id=doc.id)]
        if chunk_ids:
            session.execute(delete(Embedding).where(Embedding.chunk_id.in_(chunk_ids)))
        session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        session.execute(delete(DocumentSection).where(DocumentSection.document_id == doc.id))
        session.delete(doc)


def test_source_cursor_bootstrap_then_margin_advance(tmp_path):
    from app.confluence.sync import _advance_cursor, _ensure_source_row, _read_cursor
    from app.db.session import session_scope
    from app.db.models import Source

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    try:
        _ensure_source_row(source_id, "LOOKIN", {"1": "root"})
        assert _read_cursor(source_id) is None  # bootstrap: no cursor yet

        run_started = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
        _advance_cursor(source_id, run_started)

        cursor = _read_cursor(source_id)
        assert cursor is not None
        # exactly a 5-minute safety margin behind run start, never "now"
        assert run_started - cursor == timedelta(minutes=5)
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_dry_run_never_advances_cursor(tmp_path, monkeypatch):
    """The entire first-run-on-prod safety story rests on this: --dry-run
    must leave Source.last_sync_at untouched no matter what the crawl
    returns."""
    import app.confluence.sync as sync_mod
    from app.db.models import Source
    from app.db.session import session_scope

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        return sync_mod.CrawlResult(
            written=[sync_mod.WrittenPage(page_id="1", path=tmp_path / "x.md")],
            errors=[],
            cql_log=["cql=fake"],
        )

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)

    try:
        stats = sync_mod.sync(
            tmp_path, dry_run=True, sources=["confluence_docs"], run_ingest_and_embed=False
        )
        assert stats["sources"]["confluence_docs"]["cursor_advanced"] is False
        with session_scope() as session:
            src = session.get(Source, source_id)
            assert src is not None  # row created (bootstrap) but...
            assert src.last_sync_at is None  # ...cursor never advanced
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_truncated_by_max_pages_does_not_advance_cursor(tmp_path, monkeypatch):
    """--max-pages is an operator safety knob for a first small crawl — it
    must not let a partial run masquerade as a complete one and silently
    skip everything beyond the truncation point on the next incremental
    run."""
    import app.confluence.sync as sync_mod
    from app.db.models import Source
    from app.db.session import session_scope

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        return sync_mod.CrawlResult(written=[], errors=[], cql_log=[])

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)

    try:
        stats = sync_mod.sync(
            tmp_path,
            dry_run=False,
            sources=["confluence_docs"],
            max_pages_per_root=5,
            run_ingest_and_embed=False,
        )
        assert stats["sources"]["confluence_docs"]["cursor_advanced"] is False
        with session_scope() as session:
            src = session.get(Source, source_id)
            assert src.last_sync_at is None
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_full_successful_run_advances_cursor(tmp_path, monkeypatch):
    import app.confluence.sync as sync_mod
    from app.db.models import Source
    from app.db.session import session_scope

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        return sync_mod.CrawlResult(written=[], errors=[], cql_log=[])

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)

    try:
        stats = sync_mod.sync(
            tmp_path, dry_run=False, sources=["confluence_docs"], run_ingest_and_embed=False
        )
        assert stats["sources"]["confluence_docs"]["cursor_advanced"] is True
        with session_scope() as session:
            src = session.get(Source, source_id)
            assert src.last_sync_at is not None
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_advances_cursor_despite_low_error_rate(tmp_path, monkeypatch):
    """Prod evidence (2026-09-15): 1 transient 401 out of 5,483 pages must
    not block the cursor forever — that forced a full ~90min re-bootstrap
    on every single run. A low error rate (default threshold 1%) is
    tolerated so the cursor still advances; the failed page stays visible
    in error_detail for follow-up."""
    import app.confluence.sync as sync_mod
    from app.db.models import Source
    from app.db.session import session_scope

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        written = [sync_mod.WrittenPage(page_id=str(i), path=tmp_path / f"{i}.md") for i in range(999)]
        errors = [{"page_id": "999", "root_id": "r1", "error": "401"}]
        return sync_mod.CrawlResult(written=written, errors=errors, cql_log=[])

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)

    try:
        stats = sync_mod.sync(
            tmp_path, dry_run=False, sources=["confluence_docs"], run_ingest_and_embed=False
        )
        src_stats = stats["sources"]["confluence_docs"]
        assert src_stats["errors"] == 1
        assert src_stats["cursor_advanced"] is True  # 1/1000 = 0.1% < default 1%
        with session_scope() as session:
            src = session.get(Source, source_id)
            assert src.last_sync_at is not None
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_blocks_cursor_when_error_rate_exceeds_threshold(tmp_path, monkeypatch):
    import app.confluence.sync as sync_mod
    from app.db.models import Source
    from app.db.session import session_scope
    from app.settings import get_settings

    monkeypatch.delenv("CONFLUENCE_MAX_ERROR_COUNT", raising=False)  # 기본(0) = 비율 기준만
    get_settings.cache_clear()

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        written = [sync_mod.WrittenPage(page_id="1", path=tmp_path / "1.md")]
        errors = [{"page_id": "2", "root_id": "r1", "error": "500"}]
        return sync_mod.CrawlResult(written=written, errors=errors, cql_log=[])

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)

    try:
        stats = sync_mod.sync(
            tmp_path, dry_run=False, sources=["confluence_docs"], run_ingest_and_embed=False
        )
        src_stats = stats["sources"]["confluence_docs"]
        assert src_stats["cursor_advanced"] is False  # 1/2 = 50% >> 1%
        with session_scope() as session:
            src = session.get(Source, source_id)
            assert src.last_sync_at is None
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def _run_small_crawl(tmp_path, monkeypatch, *, n_written, n_errors, root_errors=0):
    import app.confluence.sync as sync_mod
    from app.db.models import Source
    from app.db.session import session_scope
    from app.settings import get_settings

    get_settings.cache_clear()
    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        written = [sync_mod.WrittenPage(page_id=str(i), path=tmp_path / f"{i}.md") for i in range(n_written)]
        errors = [{"page_id": f"e{i}", "root_id": "r1", "error": "401"} for i in range(n_errors)]
        errors += [{"page_id": None, "root_id": "r1", "error": "search 401"} for _ in range(root_errors)]
        return sync_mod.CrawlResult(written=written, errors=errors, cql_log=[])

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)
    try:
        stats = sync_mod.sync(
            tmp_path, dry_run=False, sources=["confluence_docs"], run_ingest_and_embed=False
        )
        return stats["sources"]["confluence_docs"]
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_small_run_error_blocks_cursor_by_default(tmp_path, monkeypatch):
    """기본(COUNT=0): 소규모 실행의 페이지 에러 1건은 cursor를 막는다 — 다음 실행이
    같은 구간을 재크롤하므로 유실 없음."""
    monkeypatch.delenv("CONFLUENCE_MAX_ERROR_COUNT", raising=False)
    src = _run_small_crawl(tmp_path, monkeypatch, n_written=2, n_errors=1)
    assert src["cursor_advanced"] is False


def test_sync_opt_in_count_tolerates_single_page_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFLUENCE_MAX_ERROR_COUNT", "1")
    src = _run_small_crawl(tmp_path, monkeypatch, n_written=2, n_errors=1)
    assert src["cursor_advanced"] is True


def test_sync_opt_in_count_still_blocks_when_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFLUENCE_MAX_ERROR_COUNT", "1")
    src = _run_small_crawl(tmp_path, monkeypatch, n_written=2, n_errors=2)
    assert src["cursor_advanced"] is False


def test_sync_root_level_error_never_advances_cursor(tmp_path, monkeypatch):
    """루트 검색 실패(page_id None)는 그 루트 변경분 전체 누락 — 비율(1/1000)이든
    건수 허용이든 cursor를 전진시키면 안 된다."""
    monkeypatch.setenv("CONFLUENCE_MAX_ERROR_COUNT", "5")
    src = _run_small_crawl(tmp_path, monkeypatch, n_written=999, n_errors=0, root_errors=1)
    assert src["cursor_advanced"] is False
    monkeypatch.delenv("CONFLUENCE_MAX_ERROR_COUNT", raising=False)
    src = _run_small_crawl(tmp_path, monkeypatch, n_written=999, n_errors=0, root_errors=1)
    assert src["cursor_advanced"] is False


def test_sync_unknown_source_type_is_skipped_not_a_crash(tmp_path):
    """An operator typo in --sources must produce a readable 'skipped:
    unknown source_type' entry in the result JSON, not a bare KeyError
    traceback they can only see in logs on a remote server."""
    from app.confluence.sync import sync

    stats = sync(tmp_path, dry_run=True, sources=["not_a_real_source"], run_ingest_and_embed=False)
    assert stats["sources"]["not_a_real_source"]["skipped"] is True


def test_ingest_insert_skip_update_cycle_scoped_to_tmp_raw_dir(tmp_path):
    """The spec's core증분 check: same content → skipped (no rechunk),
    changed content → updated. Uses ONLY a tmp_path raw dir with a single
    fixture-derived file — never touches the real data/raw/confluence_docs
    corpus — and scopes embedding to this one document_id."""
    from app.confluence.sync import build_frontmatter_confluence_docs
    from app.embed.job import embed_pending_chunks
    from app.ingest.pipeline import run_ingest

    page = json.loads((FIXTURES / "sample_confluence_docs_page.json").read_text(encoding="utf-8"))
    external_id = "2510261901"
    _cleanup_document(external_id, "confluence_docs")

    raw_dir = tmp_path / "raw"
    docs_dir = raw_dir / "confluence_docs"
    docs_dir.mkdir(parents=True)
    front = build_frontmatter_confluence_docs(
        space_key="LOOKIN",
        folder="CI-TEC 과제",
        page_id=external_id,
        title=page["title"],
        url=page["url"],
        last_modified="2026-09-07",
    )
    target = docs_dir / f"confluence_{external_id}.md"
    target.write_text(front + "\n핵심 결론: 원본 본문 텍스트입니다.\n", encoding="utf-8")

    try:
        stats1 = run_ingest(raw_dir, sources=["confluence_docs"])
        assert stats1["inserted"] == 1
        assert stats1["errors"] == 0

        from app.db.session import session_scope
        from app.db.models import Document

        with session_scope() as session:
            doc = session.query(Document).filter_by(
                source_type="confluence_docs", external_id=external_id
            ).one()
            document_id = doc.id

        emb_stats = embed_pending_chunks(document_id=document_id)
        assert emb_stats["errors"] == 0
        assert emb_stats["embedded"] >= 1

        # same content, re-ingested → skipped (no rechunk)
        stats2 = run_ingest(raw_dir, sources=["confluence_docs"])
        assert stats2["skipped"] == 1
        assert stats2.get("inserted", 0) == 0
        assert stats2.get("updated", 0) == 0

        # content changed → updated
        target.write_text(front + "\n핵심 결론: 본문이 수정되었습니다.\n", encoding="utf-8")
        stats3 = run_ingest(raw_dir, sources=["confluence_docs"])
        assert stats3["updated"] == 1
        assert stats3.get("inserted", 0) == 0
        assert stats3.get("skipped", 0) == 0
    finally:
        _cleanup_document(external_id, "confluence_docs")


def test_reingest_with_unchanged_body_backfills_all_hash_excluded_fields(tmp_path):
    """2026-10-02 production bug, found via scripts/collect_prod_diagnostics.sh
    after a real `backfill_p0b_p1b_metadata.sh --apply docs` run:
    confluence_docs missing_ancestor_ids dropped from 100% to 0.3%, but
    missing_source_version barely moved (100% → 89.5%). Root cause:
    app.ingest.pipeline._upsert_document's hash-unchanged fast path
    special-cased only `ancestor_ids` for backfill (written back when
    ancestor_ids was the only DocumentDraft._HASH_EXCLUDED_METADATA_KEYS
    field, for P0-B) — source_version/source_modified_at, added later,
    were never wired into that branch, so a --from-scratch recrawl whose
    pages had an unchanged body (the common case) refreshed ancestor_ids
    forever but never backfilled the other two. Fixed by generalizing the
    branch to loop over the whole exclusion set."""
    from app.confluence.sync import build_frontmatter_confluence_docs
    from app.ingest.pipeline import run_ingest

    external_id = "9100001"
    _cleanup_document(external_id, "confluence_docs")

    raw_dir = tmp_path / "raw"
    docs_dir = raw_dir / "confluence_docs"
    docs_dir.mkdir(parents=True)
    body = "동일한 본문 — 재크롤해도 바뀌지 않음.\n"

    # Pass 1: no ancestor_ids/source_version/source_modified_at at all —
    # simulates a page ingested before those fields existed.
    front_v1 = build_frontmatter_confluence_docs(
        space_key="LOOKIN",
        folder="CI-TEC 과제",
        page_id=external_id,
        title="test",
        url="https://x/pages/viewpage.action?pageId=9100001",
        last_modified="2026-09-07",
    )
    target = docs_dir / f"confluence_{external_id}.md"
    target.write_text(front_v1 + "\n" + body, encoding="utf-8")

    try:
        stats1 = run_ingest(raw_dir, sources=["confluence_docs"])
        assert stats1["inserted"] == 1

        from app.db.models import Document
        from app.db.session import session_scope

        with session_scope() as session:
            doc = session.query(Document).filter_by(
                source_type="confluence_docs", external_id=external_id
            ).one()
            assert "ancestor_ids" not in (doc.metadata_ or {})
            assert "source_version" not in (doc.metadata_ or {})

        # Pass 2: SAME body (content_hash unchanged) but now carries all
        # three hash-excluded fields — simulates a --from-scratch recrawl
        # of an unedited Confluence page after this session's code shipped.
        front_v2 = build_frontmatter_confluence_docs(
            space_key="LOOKIN",
            folder="CI-TEC 과제",
            page_id=external_id,
            title="test",
            url="https://x/pages/viewpage.action?pageId=9100001",
            last_modified="2026-09-07",
            ancestor_ids=["1", external_id],
            source_version="42",
            source_modified_at="2026-09-07T15:59:11.000+09:00",
        )
        target.write_text(front_v2 + "\n" + body, encoding="utf-8")
        stats2 = run_ingest(raw_dir, sources=["confluence_docs"])
        # content_hash is unchanged (ancestor_ids/source_version/
        # source_modified_at are all hash-excluded) → this must be
        # "updated" (metadata backfilled in place), not "skipped", and
        # definitely not "inserted" (no duplicate row).
        assert stats2.get("updated", 0) == 1
        assert stats2.get("inserted", 0) == 0
        assert stats2.get("skipped", 0) == 0

        with session_scope() as session:
            doc = session.query(Document).filter_by(
                source_type="confluence_docs", external_id=external_id
            ).one()
            meta = doc.metadata_ or {}
            assert meta.get("ancestor_ids") == ["1", external_id]
            assert meta.get("source_version") == 42
            assert meta.get("source_modified_at") == "2026-09-07T15:59:11.000+09:00"
            # body/content_hash genuinely unchanged — this was metadata-only
            assert doc.body_md.strip().endswith("동일한 본문 — 재크롤해도 바뀌지 않음.")
    finally:
        _cleanup_document(external_id, "confluence_docs")


def test_reingest_with_unchanged_body_restores_archived_status(tmp_path):
    """F05(2026-10-13 최종 핸드오프) §2.5.1/§2.5.6 — 조사 결과, 이 코드베이스
    어디에도 "사람이 개별 문서를 수동 archive"하는 경로는 없다(archived는
    전부 크롤 감지 자동 archive거나 map_space_setup.py의 공간 단위 bulk
    archive뿐). 그런데 _upsert_document의 hash-불변 skip 분기는 status를
    전혀 안 건드려서, archived된 문서가 본문 그대로 재크롤되면(흔한 경우)
    영원히 archived로 고착됐다 — 바로 아래(hash가 바뀌거나 활성 chunk가
    0인) 분기는 무조건 status="active"로 되살리는 것과 비대칭이었다. 같은
    "원본이 다시 보였다" 신호에 두 분기가 다르게 반응하면 안 된다."""
    from app.confluence.sync import build_frontmatter_confluence_docs
    from app.ingest.pipeline import run_ingest
    from app.db.models import Document
    from app.db.session import session_scope

    external_id = "9100002"
    _cleanup_document(external_id, "confluence_docs")

    raw_dir = tmp_path / "raw"
    docs_dir = raw_dir / "confluence_docs"
    docs_dir.mkdir(parents=True)
    body = "archived 복원 테스트 — 본문은 두 패스 동안 그대로.\n"

    front = build_frontmatter_confluence_docs(
        space_key="LOOKIN", folder="CI-TEC 과제", page_id=external_id, title="test",
        url="https://x/pages/viewpage.action?pageId=9100002", last_modified="2026-09-07",
    )
    target = docs_dir / f"confluence_{external_id}.md"
    target.write_text(front + "\n" + body, encoding="utf-8")

    try:
        stats1 = run_ingest(raw_dir, sources=["confluence_docs"])
        assert stats1["inserted"] == 1

        # 공간 단위 bulk archive(map_space_setup.py)를 흉내낸다 — 이 코드베이스가
        # 실제로 archived를 만드는 유일한 "의도적" 경로.
        with session_scope() as session:
            doc = session.query(Document).filter_by(
                source_type="confluence_docs", external_id=external_id
            ).one()
            doc.status = "archived"

        # 같은 본문으로 재크롤(content_hash 불변) — skip 분기를 탄다.
        stats2 = run_ingest(raw_dir, sources=["confluence_docs"])
        assert stats2.get("inserted", 0) == 0
        assert stats2.get("updated", 0) == 1  # status 복원이 dirty를 만듦
        assert stats2.get("skipped", 0) == 0

        with session_scope() as session:
            doc = session.query(Document).filter_by(
                source_type="confluence_docs", external_id=external_id
            ).one()
            assert doc.status == "active"
    finally:
        _cleanup_document(external_id, "confluence_docs")


def test_sync_ingest_failure_does_not_advance_cursor(tmp_path, monkeypatch):
    """P1-B (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md
    §9, REVIEW.md item 8 "수집 cursor는 ingest/embed 전에 전진한다"): a crawl
    can succeed (pages written to disk) while the subsequent run_ingest call
    fails (DB outage, crash, ...). The cursor must NOT have advanced in that
    case — otherwise the next run's `since` filter permanently skips
    re-fetching those pages from Confluence, since Confluence itself has
    nothing newer to report once the cursor is past their lastmodified."""
    import app.confluence.sync as sync_mod
    import app.ingest.pipeline as pipeline_mod
    from app.db.models import Source
    from app.db.session import session_scope

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        return sync_mod.CrawlResult(
            written=[sync_mod.WrittenPage(page_id="1", path=tmp_path / "x.md")],
            errors=[],
            cql_log=["cql=fake"],
        )

    def fake_run_ingest(*args, **kwargs):
        raise RuntimeError("simulated ingest crash")

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)
    monkeypatch.setattr(pipeline_mod, "run_ingest", fake_run_ingest)

    try:
        with pytest.raises(RuntimeError, match="simulated ingest crash"):
            sync_mod.sync(
                tmp_path,
                dry_run=False,
                sources=["confluence_docs"],
                run_ingest_and_embed=True,
            )
        with session_scope() as session:
            src = session.get(Source, source_id)
            # row was created (bootstrap) by _ensure_source_row, but the
            # cursor itself must still be untouched — the crash happened
            # before the commit loop that would have advanced it.
            assert src is not None
            assert src.last_sync_at is None
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_map_ingest_failure_does_not_advance_cursor_or_clear_checkpoint(tmp_path, monkeypatch):
    """Same P1-B fix, mirrored in app.confluence.map_sync._sync_map_body
    (which the module's own docstring says mirrors app.confluence.sync.sync()
    exactly). Calls _sync_map_body directly (bypassing sync_map()'s advisory
    lock / DB-backed source registry, which aren't needed to exercise this
    logic) with a synthetic single-source def."""
    import app.confluence.map_sync as map_sync_mod
    import app.ingest.pipeline as pipeline_mod
    from app.db.models import Source
    from app.db.session import session_scope
    from app.settings import get_settings

    source_id = "test_p1b_map_source"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()
        # _advance_cursor() only *updates* an existing row (session.get +
        # `if src:` — it never creates one), so the row must pre-exist here
        # for this test to actually distinguish "cursor untouched" from
        # "there was never a row to touch" in the first place.
        session.add(
            Source(id=source_id, type="confluence_map", name="Test Space", config={})
        )

    async def fake_crawl_map_source(*args, **kwargs):
        return map_sync_mod.CrawlResult(
            written=[map_sync_mod.WrittenPage(page_id="1", path=tmp_path / "x.md")],
            errors=[],
            cql_log=["cql=fake"],
        )

    def fake_run_ingest(*args, **kwargs):
        raise RuntimeError("simulated ingest crash")

    monkeypatch.setattr(map_sync_mod, "_crawl_map_source", fake_crawl_map_source)
    monkeypatch.setattr(pipeline_mod, "run_ingest", fake_run_ingest)

    all_defs = {
        source_id: {"space_key": "TESTSPACE", "space_name": "Test Space", "roots": {"1": "root"}}
    }
    run_started = datetime.now(timezone.utc)

    try:
        with pytest.raises(RuntimeError, match="simulated ingest crash"):
            map_sync_mod._sync_map_body(
                defs=[source_id],
                raw_root=tmp_path,
                settings=get_settings(),
                client=None,
                run_started=run_started,
                stats={"dry_run": False, "started_at": run_started.isoformat(), "sources": {}},
                dry_run=False,
                max_pages_per_root=None,
                root_id=None,
                run_ingest_and_embed=True,
                all_defs=all_defs,
            )
        with session_scope() as session:
            src = session.get(Source, source_id)
            assert src is not None
            assert src.last_sync_at is None
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def _run_map_body(tmp_path, monkeypatch, *, n_written, page_errors=0, root_errors=0):
    """_sync_map_body를 합성 source 1개로 직접 호출(위 P1-B 테스트와 같은 방식)하고
    (stats, last_sync_at)을 돌려준다. run_ingest_and_embed=False라 커서는 크롤 직후
    판정 결과대로 바로 커밋된다."""
    import app.confluence.map_sync as map_sync_mod
    from app.db.models import Source
    from app.db.session import session_scope
    from app.settings import get_settings

    get_settings.cache_clear()
    source_id = "test_map_error_policy_source"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()
        session.add(Source(id=source_id, type="confluence_map", name="Test Space", config={}))

    async def fake_crawl_map_source(*args, **kwargs):
        written = [map_sync_mod.WrittenPage(page_id=str(i), path=tmp_path / f"{i}.md") for i in range(n_written)]
        errors = [{"page_id": f"e{i}", "root_id": "1", "error": "401"} for i in range(page_errors)]
        errors += [{"page_id": None, "root_id": "1", "error": "search 401"} for _ in range(root_errors)]
        return map_sync_mod.CrawlResult(written=written, errors=errors, cql_log=[])

    monkeypatch.setattr(map_sync_mod, "_crawl_map_source", fake_crawl_map_source)
    all_defs = {source_id: {"space_key": "TESTSPACE", "space_name": "Test Space", "roots": {"1": "root"}}}
    run_started = datetime.now(timezone.utc)
    try:
        stats = map_sync_mod._sync_map_body(
            defs=[source_id], raw_root=tmp_path, settings=get_settings(), client=None,
            run_started=run_started,
            stats={"dry_run": False, "started_at": run_started.isoformat(), "sources": {}},
            dry_run=False, max_pages_per_root=None, root_id=None,
            run_ingest_and_embed=False, all_defs=all_defs,
        )
        with session_scope() as session:
            last = session.get(Source, source_id).last_sync_at
        return stats["sources"][source_id], last
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()


def test_sync_map_root_level_error_never_advances_cursor(tmp_path, monkeypatch):
    """confluence_map도 confluence_docs와 같은 규칙: 루트 검색 실패(page_id None)는
    오류율이 낮아도(1/1000) 건수 허용을 켜도 커서를 전진시키면 안 된다."""
    monkeypatch.delenv("CONFLUENCE_MAX_ERROR_COUNT", raising=False)
    src, last = _run_map_body(tmp_path, monkeypatch, n_written=999, root_errors=1)
    assert src["cursor_advanced"] is False and last is None
    monkeypatch.setenv("CONFLUENCE_MAX_ERROR_COUNT", "5")
    src, last = _run_map_body(tmp_path, monkeypatch, n_written=999, root_errors=1)
    assert src["cursor_advanced"] is False and last is None


def test_sync_map_low_page_error_rate_still_advances_cursor(tmp_path, monkeypatch):
    """기존 1% 비율 허용은 페이지 단위 에러에 대해 그대로 유지(회귀 방지)."""
    monkeypatch.delenv("CONFLUENCE_MAX_ERROR_COUNT", raising=False)
    src, last = _run_map_body(tmp_path, monkeypatch, n_written=999, page_errors=1)
    assert src["cursor_advanced"] is True and last is not None


def test_sync_map_small_run_page_error_blocks_cursor_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFLUENCE_MAX_ERROR_COUNT", raising=False)
    src, last = _run_map_body(tmp_path, monkeypatch, n_written=2, page_errors=1)
    assert src["cursor_advanced"] is False and last is None


def test_sync_map_no_errors_advances_cursor(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFLUENCE_MAX_ERROR_COUNT", raising=False)
    src, last = _run_map_body(tmp_path, monkeypatch, n_written=3)
    assert src["cursor_advanced"] is True and last is not None


def test_sync_embed_failure_does_not_block_cursor_advance(tmp_path, monkeypatch):
    """embed_pending_chunks() is a global self-healing queue over every
    chunk lacking an embedding, not scoped to this run — unlike a
    run_ingest failure, its failure must NOT re-block the cursor, or a
    transient embedding-model outage would force a pointless full re-crawl
    of the same window on every run until it recovers, without the
    re-crawl doing anything to actually fix the missing embeddings."""
    import app.confluence.sync as sync_mod
    import app.embed.job as embed_job_mod
    from app.db.models import Source
    from app.db.session import session_scope

    source_id = "confluence_lookin_docs"
    with session_scope() as session:
        session.query(Source).filter_by(id=source_id).delete()

    async def fake_crawl_source(*args, **kwargs):
        return sync_mod.CrawlResult(written=[], errors=[], cql_log=["cql=fake"])

    def fake_embed_pending_chunks(*args, **kwargs):
        raise RuntimeError("simulated embedding model outage")

    monkeypatch.setattr(sync_mod, "_crawl_source", fake_crawl_source)
    monkeypatch.setattr(embed_job_mod, "embed_pending_chunks", fake_embed_pending_chunks)

    try:
        stats = sync_mod.sync(
            tmp_path,
            dry_run=False,
            sources=["confluence_docs"],
            run_ingest_and_embed=True,
        )
        assert stats["sources"]["confluence_docs"]["cursor_advanced"] is True
        assert "simulated embedding model outage" in stats.get("embed_error", "")
        with session_scope() as session:
            src = session.get(Source, source_id)
            assert src is not None
            assert src.last_sync_at is not None  # cursor DID advance
    finally:
        with session_scope() as session:
            session.query(Source).filter_by(id=source_id).delete()
