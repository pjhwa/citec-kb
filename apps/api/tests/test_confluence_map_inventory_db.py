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
    from app.confluence.map_sync import MAP_SOURCE_DEFS, run_map_inventory
    from app.db.models import Document
    from app.db.session import session_scope
    from sqlalchemy import select

    source_id = "confluence_map_devops001"
    space_key = MAP_SOURCE_DEFS[source_id]["space_key"]

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
    with session_scope() as session:
        gone = session.scalar(select(Document).where(Document.id == "test_inv:999"))
        still_here = session.scalar(select(Document).where(Document.id == "test_inv:111"))
        assert gone.status == "archived"
        assert still_here.status == "active"
