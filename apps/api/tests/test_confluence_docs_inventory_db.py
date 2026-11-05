"""DB test for app.confluence.docs_inventory.run_docs_inventory (real ingest,
mocked Confluence). Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points
at a scratch Postgres with the alembic schema — never the live DB."""

from __future__ import annotations

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

ROOT = "222532692"  # a real confluence_docs root id from _SOURCE_DEFS
IDS = ["9101", "9102", "9103"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://c.example.com")
    monkeypatch.setenv("CONFLUENCE_USERNAME", "u")
    monkeypatch.setenv("CONFLUENCE_PASSWORD", "p")
    monkeypatch.setenv("CONFLUENCE_RATE_LIMIT_RPS", "0")
    get_settings.cache_clear()
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda *a, **k: {"embedded": 0})
    yield
    from app.db.models import Chunk, Document, DocumentSection
    from app.db.session import session_scope

    with session_scope() as session:
        docs = session.query(Document).filter(Document.external_id.in_(IDS)).all()
        for d in docs:
            session.query(Chunk).filter(Chunk.document_id == d.id).delete(synchronize_session=False)
            session.query(DocumentSection).filter(DocumentSection.document_id == d.id).delete(
                synchronize_session=False
            )
            session.query(Document).filter(Document.id == d.id).delete(synchronize_session=False)
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _full(pid, ancestors):
    return {
        "id": pid,
        "title": f"Page {pid}",
        "version": {"number": 1, "when": "2026-09-07T15:59:11.000+09:00"},
        "ancestors": [{"id": a} for a in ancestors],
        "body": {"storage": {"value": f"<p>body {pid}</p>"}},
    }


def _patch_confluence(monkeypatch, listing, fulls):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            params = dict(request.url.params)
            root = params["cql"].split("ancestor=", 1)[1].split(" ", 1)[0]
            if root != ROOT or int(params["start"]) > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": listing})
        pid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=fulls[pid])

    def fake_bulk(self, timeout=30.0):
        return httpx.AsyncClient(base_url=self._base_url, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.confluence.client.ConfluenceClient.bulk_client", fake_bulk)


def _only_one_root(monkeypatch):
    from app.confluence import docs_inventory as inv

    sd = dict(inv._SOURCE_DEFS["confluence_docs"])
    sd["roots"] = {ROOT: "Root"}
    monkeypatch.setitem(inv._SOURCE_DEFS, "confluence_docs", sd)


def test_moved_page_is_refreshed_and_gone_page_stays_archived(tmp_path, monkeypatch):
    from app.confluence.docs_inventory import run_docs_inventory
    from app.confluence.sync import _write_page
    from app.db.models import Document
    from app.db.session import session_scope
    from app.ingest.pipeline import run_ingest

    _only_one_root(monkeypatch)
    for pid in IDS:
        _write_page(
            source_type="confluence_docs", full=_full(pid, [ROOT]), root_label="Root",
            space_key="LOOKIN", base_url="https://c.example.com", tz_name="Asia/Seoul", raw_dir=tmp_path,
        )
    run_ingest(tmp_path, sources=["confluence_docs"])

    # 9101 stays, 9102 moved under another parent, 9103 left every root.
    moved_full = _full("9102", [ROOT, "7777"])
    _patch_confluence(
        monkeypatch,
        listing=[
            {"id": "9101", "title": "Page 9101", "ancestors": [{"id": ROOT}]},
            {"id": "9102", "title": "Page 9102", "ancestors": [{"id": ROOT}, {"id": "7777"}]},
        ],
        fulls={"9102": moved_full},
    )
    report = run_docs_inventory(tmp_path, sources=["confluence_docs"], max_archive_ratio=0.5)
    src = report["sources"]["confluence_docs"]
    assert src["fetch_by_reason"] == {"moved": 1}
    assert src["archived"] == ["9103"]
    assert src["raw_moved_aside"] == 1

    with session_scope() as session:
        rows = {d.external_id: d for d in session.query(Document).filter(Document.external_id.in_(IDS))}
        assert rows["9103"].status == "archived"  # not restored by the ingest that ran in the same call
        assert rows["9101"].status == "active" and rows["9102"].status == "active"
        assert rows["9102"].metadata_["ancestor_ids"] == [ROOT, "7777", "9102"]

    # A second ingest (the next daily sync) must not resurrect it.
    run_ingest(tmp_path, sources=["confluence_docs"])
    with session_scope() as session:
        assert session.query(Document).filter(Document.external_id == "9103").one().status == "archived"


def test_archive_skipped_when_ratio_exceeded_and_dry_run_writes_nothing(tmp_path, monkeypatch):
    from app.confluence.docs_inventory import run_docs_inventory
    from app.confluence.sync import _write_page
    from app.db.models import Document
    from app.db.session import session_scope
    from app.ingest.pipeline import run_ingest

    _only_one_root(monkeypatch)
    for pid in IDS:
        _write_page(
            source_type="confluence_docs", full=_full(pid, [ROOT]), root_label="Root",
            space_key="LOOKIN", base_url="https://c.example.com", tz_name="Asia/Seoul", raw_dir=tmp_path,
        )
    run_ingest(tmp_path, sources=["confluence_docs"])
    _patch_confluence(monkeypatch, listing=[], fulls={})  # everything vanished

    report = run_docs_inventory(tmp_path, sources=["confluence_docs"])  # default ratio 5%
    src = report["sources"]["confluence_docs"]
    assert src["archive_skipped"] == "ratio_exceeded"
    assert set(IDS) <= set(src["would_archive"])

    dry = run_docs_inventory(tmp_path, sources=["confluence_docs"], dry_run=True, max_archive_ratio=1.0)
    assert dry["sources"]["confluence_docs"]["archived"] == []
    assert (tmp_path / "confluence_docs" / "confluence_9103.md").exists()
    with session_scope() as session:
        assert session.query(Document).filter(Document.external_id.in_(IDS)).filter_by(status="active").count() == 3
