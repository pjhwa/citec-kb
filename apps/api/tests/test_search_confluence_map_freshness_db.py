"""DB-touching tests for the SearchHit.evidence_eligible / map_synced_at
fields added to app.retrieval.search. Same opt-in-DB convention as
test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

import hashlib
import json
import os

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


def _seed(session, *, source_type: str, external_id: str, title: str, evidence_grade: str):
    from app.db.models import Chunk, Document

    meta = {"space_key": "SPC"} if source_type == "confluence_map" else {}
    payload = f"{title}\n\n{json.dumps(meta, sort_keys=True)}"
    doc_id = f"test_fresh:{source_type}:{external_id}"
    doc = Document(
        id=doc_id,
        source_type=source_type,
        external_id=external_id,
        title=title,
        body_md=f"{title} unique_marker_freshness_test",
        metadata_=meta,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        evidence_grade=evidence_grade,
        status="active",
    )
    session.add(doc)
    session.flush()
    session.add(
        Chunk(
            id=f"{doc_id}:chunk",
            document_id=doc_id,
            ordinal=0,
            text=f"{title} unique_marker_freshness_test",
            header_context="",
        )
    )
    return doc_id


def test_confluence_map_hit_has_evidence_eligible_false_and_map_synced_at(tmp_path):
    from app.db.session import session_scope
    from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

    with session_scope() as session:
        _seed(
            session,
            source_type="confluence_map",
            external_id="unique_marker_freshness_test_page",
            title="unique_marker_freshness_test page",
            evidence_grade="C",
        )

    with session_scope() as session:
        resp = hybrid_search(
            session,
            SearchRequest(q="unique_marker_freshness_test", top_k=5, filters=SearchFilters()),
        )

    hits = [h for h in resp.results if h.source_type == "confluence_map"]
    assert hits, "expected the seeded confluence_map document to be found"
    assert hits[0].evidence_eligible is False
    assert hits[0].map_synced_at is not None


def test_non_confluence_map_hit_is_evidence_eligible_with_no_synced_at():
    from app.db.session import session_scope
    from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

    with session_scope() as session:
        _seed(
            session,
            source_type="tech_repo",
            external_id="unique_marker_freshness_test_doc",
            title="unique_marker_freshness_test doc",
            evidence_grade="A",
        )

    with session_scope() as session:
        resp = hybrid_search(
            session,
            SearchRequest(q="unique_marker_freshness_test", top_k=5, filters=SearchFilters()),
        )

    hits = [h for h in resp.results if h.source_type == "tech_repo"]
    assert hits, "expected the seeded tech_repo document to be found"
    assert hits[0].evidence_eligible is True
    assert hits[0].map_synced_at is None
