"""DB-touching test for /v1/health's active_documents_count field (P1-B,
docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §9,
REVIEW.md item 8: health's documents_count counts every row regardless of
status, while /api/wiki/stats's `total` counts only status='active' under a
different name — the two were never reconcilable from health alone).

Same opt-in-DB convention as test_confluence_sync_db.py.
"""

from __future__ import annotations

import hashlib
import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
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


_PREFIX = "test_health_active_docs"


def _make_doc(session, *, external_id: str, status: str) -> str:
    from app.db.models import Document

    doc_id = f"{_PREFIX}:{external_id}"
    payload = f"{external_id}\n{status}"
    doc = Document(
        id=doc_id,
        source_id="fs_raw",
        source_type="tech_repo",
        external_id=external_id,
        title="test",
        body_md="test body",
        metadata_={},
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        version=1,
        status=status,
        evidence_grade="A",
    )
    session.merge(doc)
    return doc_id


def _cleanup(external_ids: list[str]) -> None:
    from sqlalchemy import delete

    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        doc_ids = [f"{_PREFIX}:{eid}" for eid in external_ids]
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))


def test_health_reports_both_total_and_active_document_counts():
    from fastapi.testclient import TestClient

    from app.db.session import session_scope
    from app.main import app

    eids = ["1", "2", "3"]
    _cleanup(eids)
    try:
        with session_scope() as session:
            _make_doc(session, external_id="1", status="active")
            _make_doc(session, external_id="2", status="active")
            _make_doc(session, external_id="3", status="archived")

        r = TestClient(app).get("/v1/health")
        assert r.status_code == 200
        pg = r.json()["checks"]["postgres"]
        assert pg["ok"] is True
        assert pg["documents_count"] is not None
        assert pg["active_documents_count"] is not None
        # exactly the 2 active + 1 archived among ours must be reflected in
        # the gap between the two counts (can't assert absolute values —
        # other fixtures may coexist in this scratch DB run).
        assert pg["documents_count"] >= pg["active_documents_count"]
        assert pg["documents_count"] - pg["active_documents_count"] >= 1
    finally:
        _cleanup(eids)
