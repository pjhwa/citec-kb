"""DB-touching tests for app.confluence.map_sync.run_map_inventory.

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
scratch Postgres DB with the alembic schema applied (same opt-in
convention as test_citec_dashboard_service.py) — never the live
citec_knowledge DB.
"""

from __future__ import annotations

import hashlib
import json
import os

import httpx
import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _cleanup_seeded_documents():
    """Pre-existing gap: _seed_document() below inserts fixed-id Document
    rows (test_inv:111/test_inv:999) that none of this file's tests ever
    cleaned up, so running all 3 in one pytest invocation against a DB
    that isn't wiped between tests hits a duplicate-key IntegrityError on
    the 2nd/3rd test. Delete them after every test (regardless of pass/
    fail) so this file stays safely re-runnable against the shared
    scratch DB.
    """
    try:
        yield
    finally:
        from app.db.models import Document
        from app.db.session import session_scope

        with session_scope() as session:
            session.query(Document).filter(
                Document.id.in_(["test_inv:111", "test_inv:999"])
            ).delete(synchronize_session=False)


def _seed_document(session, *, external_id: str, space_key: str, root_label: str, title: str):
    from app.db.models import Document

    meta = {
        "space_key": space_key,
        "루트": root_label,
        "Page ID": external_id,
        "제목": title,
        "경로": title,
    }
    payload = f"{title}\n\n{json.dumps(meta, sort_keys=True)}"
    doc = Document(
        id=f"test_inv:{external_id}",
        source_type="confluence_map",
        external_id=external_id,
        title=title,
        body_md=title,
        metadata_=meta,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        evidence_grade="C",
        status="active",
    )
    session.add(doc)


def test_run_map_inventory_archives_a_page_no_longer_returned(tmp_path, monkeypatch):
    from app.confluence.map_sync import get_source_defs, run_map_inventory
    from app.db.models import Document
    from app.db.session import session_scope
    from sqlalchemy import select

    source_id = "confluence_map_devops001"
    space_key = get_source_defs()[source_id]["space_key"]

    with session_scope() as session:
        # "111" is still returned by the mocked full listing below; "999" is
        # a page that has since moved/been deleted and must be archived.
        _seed_document(session, external_id="111", space_key=space_key, root_label="R", title="Still here")
        _seed_document(session, external_id="999", space_key=space_key, root_label="R", title="Gone now")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            params = dict(request.url.params)
            if int(params["start"]) > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": "111"}]})
        return httpx.Response(
            200,
            json={
                "id": "111",
                "title": "Still here",
                "version": {"number": 2, "when": "2026-09-21T00:00:00.000+09:00"},
                "ancestors": [],
            },
        )

    def fake_bulk_client(self, timeout: float = 30.0):
        return httpx.AsyncClient(base_url=self._base_url, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.confluence.client.ConfluenceClient.bulk_client", fake_bulk_client)

    stats = run_map_inventory(source_id, tmp_path)

    assert stats["archived"] == ["999"]
    assert stats["would_archive"] == ["999"]
    assert stats["archive_skipped_due_to_errors"] is False
    with session_scope() as session:
        gone = session.scalar(select(Document).where(Document.id == "test_inv:999"))
        still_here = session.scalar(select(Document).where(Document.id == "test_inv:111"))
        assert gone.status == "archived"
        assert still_here.status == "active"


def test_run_map_inventory_skips_archive_when_crawl_had_errors(tmp_path, monkeypatch):
    """A transient error elsewhere in this inventory run (e.g. another
    root's search call failing) must not cause a genuinely-gone page to be
    archived — the whole listing is untrustworthy, not just the errored
    root, so archiving is skipped entirely and reported via
    archive_skipped_due_to_errors/would_archive instead."""
    from app.confluence.map_sync import get_source_defs, run_map_inventory
    from app.db.models import Document
    from app.db.session import session_scope
    from sqlalchemy import select

    source_id = "confluence_map_devops001"
    space_key = get_source_defs()[source_id]["space_key"]
    roots = list(get_source_defs()[source_id]["roots"])
    failing_root = roots[1]  # not "111"/"999" related — just some other root

    with session_scope() as session:
        # "111" is still returned by the mocked listing below; "999" is
        # genuinely gone, but a different root's search call fails, so the
        # overall listing is incomplete and must not be trusted to archive.
        _seed_document(session, external_id="111", space_key=space_key, root_label="R", title="Still here")
        _seed_document(session, external_id="999", space_key=space_key, root_label="R", title="Gone now")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            params = dict(request.url.params)
            cql = params.get("cql", "")
            if failing_root in cql:
                return httpx.Response(500, json={"message": "boom"})
            if int(params["start"]) > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": "111"}]})
        return httpx.Response(
            200,
            json={
                "id": "111",
                "title": "Still here",
                "version": {"number": 2, "when": "2026-09-21T00:00:00.000+09:00"},
                "ancestors": [],
            },
        )

    def fake_bulk_client(self, timeout: float = 30.0):
        return httpx.AsyncClient(base_url=self._base_url, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.confluence.client.ConfluenceClient.bulk_client", fake_bulk_client)

    stats = run_map_inventory(source_id, tmp_path)

    assert stats["errors"], "expected the failing root to produce an error"
    assert stats["archived"] == []
    assert stats["would_archive"] == ["999"]
    assert stats["archive_skipped_due_to_errors"] is True
    with session_scope() as session:
        gone = session.scalar(select(Document).where(Document.id == "test_inv:999"))
        assert gone.status == "active"  # NOT wrongly archived


def test_run_map_inventory_dry_run_previews_without_mutating(tmp_path, monkeypatch):
    """dry_run=True must still compute and report would_archive (the full
    diff) so a caller gets a real preview, while leaving archived == [] and
    the DB untouched."""
    from app.confluence.map_sync import get_source_defs, run_map_inventory
    from app.db.models import Document
    from app.db.session import session_scope
    from sqlalchemy import select

    source_id = "confluence_map_devops001"
    space_key = get_source_defs()[source_id]["space_key"]

    with session_scope() as session:
        _seed_document(session, external_id="111", space_key=space_key, root_label="R", title="Still here")
        _seed_document(session, external_id="999", space_key=space_key, root_label="R", title="Gone now")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            params = dict(request.url.params)
            if int(params["start"]) > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": "111"}]})
        return httpx.Response(
            200,
            json={
                "id": "111",
                "title": "Still here",
                "version": {"number": 2, "when": "2026-09-21T00:00:00.000+09:00"},
                "ancestors": [],
            },
        )

    def fake_bulk_client(self, timeout: float = 30.0):
        return httpx.AsyncClient(base_url=self._base_url, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.confluence.client.ConfluenceClient.bulk_client", fake_bulk_client)

    stats = run_map_inventory(source_id, tmp_path, dry_run=True)

    assert stats["would_archive"] == ["999"]
    assert stats["archived"] == []
    with session_scope() as session:
        still_active = session.scalar(select(Document).where(Document.id == "test_inv:999"))
        assert still_active.status == "active"  # dry_run must not mutate
