"""DB-touching tests for app.graph.sync_state.

Skipped unless GRAPH_TEST_DATABASE_URL points at a reachable scratch
Postgres DB with the alembic schema applied — never the live citec_knowledge DB.
"""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set GRAPH_TEST_DATABASE_URL to a scratch Postgres DB to run these",
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


def _make_document_row(session):
    from app.db.models import Document

    doc = Document(
        id=str(uuid.uuid4()),
        source_type="tech_repo",
        external_id=str(uuid.uuid4()),
        title="test doc",
        body_md="hello",
        content_hash="abc123",
    )
    session.add(doc)
    session.flush()
    return doc.id


def test_mark_synced_then_get_state_roundtrips():
    from app.db.session import session_scope
    from app.graph.sync_state import get_state, mark_synced

    with session_scope() as session:
        doc_id = _make_document_row(session)

    mark_synced(doc_id, input_hash="h1", extractor_version="v1")
    state = get_state(doc_id)
    assert state is not None
    assert state["input_hash"] == "h1"
    assert state["graph_extractor_version"] == "v1"
    assert state["last_error"] is None


def test_mark_failed_sets_error_without_clearing_hash():
    from app.db.session import session_scope
    from app.graph.sync_state import get_state, mark_failed, mark_synced

    with session_scope() as session:
        doc_id = _make_document_row(session)

    mark_synced(doc_id, input_hash="h1", extractor_version="v1")
    mark_failed(doc_id, error="neo4j connection refused")
    state = get_state(doc_id)
    assert state["last_error"] == "neo4j connection refused"
    assert state["input_hash"] == "h1"  # 직전 성공 해시는 유지


def test_get_state_returns_none_for_unknown_document():
    from app.graph.sync_state import get_state

    assert get_state("does-not-exist") is None


def test_mark_failed_on_never_synced_document_does_not_raise():
    from app.db.session import session_scope
    from app.graph.sync_state import get_state, mark_failed

    with session_scope() as session:
        doc_id = _make_document_row(session)

    mark_failed(doc_id, error="neo4j connection refused")
    state = get_state(doc_id)
    assert state["last_error"] == "neo4j connection refused"
    assert state["input_hash"] is None
    assert state["graph_extractor_version"] is None


def test_get_latest_synced_at_returns_max_synced_at():
    from app.db.session import session_scope
    from app.graph.sync_state import get_latest_synced_at, mark_synced

    with session_scope() as session:
        doc_id_a = _make_document_row(session)
        doc_id_b = _make_document_row(session)

    mark_synced(doc_id_a, input_hash="h1", extractor_version="v1")
    mark_synced(doc_id_b, input_hash="h2", extractor_version="v1")

    result = get_latest_synced_at()
    assert result is not None


def test_get_latest_synced_at_returns_none_when_no_rows():
    from app.graph.sync_state import get_latest_synced_at
    from app.db.session import session_scope
    from app.db.models import GraphSyncState

    with session_scope() as session:
        session.query(GraphSyncState).delete()

    assert get_latest_synced_at() is None
