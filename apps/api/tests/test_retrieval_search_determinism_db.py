"""DB-touching tests for deterministic tie-break in app.retrieval.search
(P1-A §8 item 6, docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md,
REVIEW.md item 5: "일부 LIMIT에 동점 기준이 빠진 경로가 있다").

Reproduces the repeat-stability regression the review measured directly:
several chunks with identical FTS rank (same text) must return in the same
order on every call, not whatever order Postgres happens to scan in.

Same opt-in-DB convention as test_search_confluence_map_freshness_db.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid

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


_PREFIX = "test_search_determinism"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        session.query(Document).filter(Document.id.like(f"{_PREFIX}:%")).delete(
            synchronize_session=False
        )


def _seed_tied_chunks(session, *, n: int) -> list[str]:
    """n documents with byte-identical body text → identical ts_rank_cd."""
    from sqlalchemy import text as sql_text

    from app.db.models import Chunk, Document

    marker = "unique_marker_determinism_test"
    chunk_ids = []
    for i in range(n):
        doc_id = f"{_PREFIX}:{i}"
        title = f"{marker} doc {i}"
        body = f"{marker} identical body text for tie-break testing"
        payload = f"{title}\n{body}"
        doc = Document(
            id=doc_id,
            source_type="tech_repo",
            external_id=str(i),
            title=title,
            body_md=body,
            metadata_={},
            content_hash=hashlib.sha256(payload.encode()).hexdigest(),
            evidence_grade="A",
            status="active",
        )
        session.add(doc)
        session.flush()
        chunk_id = str(uuid.uuid4())
        chunk_ids.append(chunk_id)
        session.add(
            Chunk(id=chunk_id, document_id=doc_id, ordinal=0, text=body, header_context="")
        )
        session.flush()
        session.execute(
            sql_text("UPDATE chunks SET tsv = to_tsvector('simple', :t) WHERE id = :id"),
            {"t": f"\n{body}", "id": chunk_id},
        )
    return chunk_ids


def test_fts_search_repeat_calls_return_identical_order_on_tied_rank():
    from app.retrieval.search import SearchFilters, SearchRequest, fts_search
    from app.db.session import session_scope

    with session_scope() as session:
        _seed_tied_chunks(session, n=5)

    req = SearchRequest(q="unique_marker_determinism_test", top_k=5, filters=SearchFilters())
    with session_scope() as session:
        first = fts_search(session, req)
    with session_scope() as session:
        second = fts_search(session, req)
        third = fts_search(session, req)

    assert len(first) >= 5
    assert first == second == third


def test_fts_search_tied_rank_order_matches_ascending_chunk_id():
    """Not just stable — the actual documented tie-break key (Chunk.id)."""
    from app.retrieval.search import SearchFilters, SearchRequest, fts_search
    from app.db.session import session_scope

    with session_scope() as session:
        chunk_ids = _seed_tied_chunks(session, n=5)

    req = SearchRequest(q="unique_marker_determinism_test", top_k=5, filters=SearchFilters())
    with session_scope() as session:
        result = fts_search(session, req)

    our_ids_in_result = [cid for cid in result if cid in chunk_ids]
    assert our_ids_in_result == sorted(chunk_ids)
