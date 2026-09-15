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
