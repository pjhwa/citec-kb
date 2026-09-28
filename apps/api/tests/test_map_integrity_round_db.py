"""DB-backed checks for subtree exclusion, copy folding and the
include_irrelevant_maps override. Same opt-in scratch-DB convention as
test_search_confluence_map_freshness_db.py."""

from __future__ import annotations

import hashlib
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

MARK = "mapintegrityzq"


@pytest.fixture(autouse=True)
def _fresh_engine_and_cleanup():
    from app.db import session as db_session
    from app.settings import get_settings

    def clear():
        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()
        get_settings.cache_clear()

    clear()
    yield
    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        session.query(Document).filter(Document.id.like("test_mapint:%")).delete(
            synchronize_session=False
        )
    clear()


def _seed(session, external_id, title, *, source_type="confluence_map", meta=None, path_l2="SPC"):
    from sqlalchemy import text as sql_text

    from app.db.models import Chunk, Document

    meta = dict(meta or {})
    doc_id = f"test_mapint:{source_type}:{external_id}"
    session.add(
        Document(
            id=doc_id,
            source_type=source_type,
            external_id=external_id,
            title=title,
            body_md=f"{title} {MARK}",
            metadata_=meta,
            content_hash=hashlib.sha256(f"{doc_id}{meta}".encode()).hexdigest(),
            evidence_grade="C",
            status="active",
            path_l2=path_l2,
        )
    )
    session.flush()
    cid = str(uuid.uuid4())
    text = f"{title} {MARK}"
    session.add(Chunk(id=cid, document_id=doc_id, ordinal=0, text=text, header_context=""))
    session.flush()
    session.execute(
        sql_text("UPDATE chunks SET tsv = to_tsvector('simple', :t) WHERE id = :id"),
        {"t": f"\n{text}", "id": cid},
    )


def _search(**filters):
    from app.db.session import session_scope
    from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

    with session_scope() as session:
        return hybrid_search(
            session,
            SearchRequest(q=MARK, top_k=20, filters=SearchFilters(**filters)),
        )


def _ids(resp):
    return {h.external_id for h in resp.results}


def test_exclude_subtree_ids_drops_parent_child_and_keeps_unrelated():
    from app.db.session import session_scope

    with session_scope() as session:
        _seed(session, "P1", "parent page", meta={"ancestor_ids": ["H", "P1"]})
        _seed(session, "C1", "child page", meta={"ancestor_ids": ["H", "P1", "C1"]})
        _seed(session, "G1", "grandchild page", meta={"ancestor_ids": ["H", "P1", "C1", "G1"]})
        _seed(session, "S1", "sibling page", meta={"ancestor_ids": ["H", "S1"]})
        _seed(session, "N1", "legacy row without ancestors")
        _seed(session, "D1", "docs row", source_type="confluence_docs")

    assert {"P1", "C1", "G1", "S1", "N1", "D1"} <= _ids(_search())
    assert _ids(_search(exclude_page_ids=["P1"])) >= {"C1", "G1"}  # unchanged behaviour

    got = _ids(_search(exclude_subtree_ids=["P1"]))
    assert not got & {"P1", "C1", "G1"}
    assert {"S1", "N1", "D1"} <= got

    # a non-map page in the list is still excluded by its own id
    assert "D1" not in _ids(_search(exclude_subtree_ids=["D1"]))


def test_upsert_backfills_ancestor_ids_without_rechunking():
    """A re-synced page whose only change is the new ancestor_ids must be
    updated in place (same content_hash → no rechunk/re-embed)."""
    from app.db.models import Chunk, Document
    from app.db.session import session_scope
    from app.ingest.adapters import DocumentDraft
    from app.ingest.pipeline import _upsert_document

    def draft(meta):
        return DocumentDraft(
            source_type="confluence_map", external_id="test_mapint_up", title="t up",
            body_md="b", metadata=meta, evidence_grade="C", path_l2="SPC",
        ).finalize()

    old = draft({"space_key": "SPC"})
    with session_scope() as session:
        _upsert_document(session, old)
        doc = session.query(Document).filter_by(external_id="test_mapint_up").one()
        if not session.query(Chunk).filter_by(document_id=doc.id, is_active=True).count():
            session.add(
                Chunk(id=str(uuid.uuid4()), document_id=doc.id, ordinal=0, text="b", header_context="")
            )
        session.flush()
        n_chunks = session.query(Chunk).filter_by(document_id=doc.id).count()
        doc_id, version = doc.id, doc.version
    assert n_chunks >= 1
    try:
        new = draft({"space_key": "SPC", "ancestor_ids": ["A", "test_mapint_up"]})
        assert new.content_hash == old.content_hash
        with session_scope() as session:
            assert _upsert_document(session, new) == "updated"
        with session_scope() as session:
            doc = session.get(Document, doc_id)
            assert doc.metadata_["ancestor_ids"] == ["A", "test_mapint_up"]
            assert doc.version == version
            assert session.query(Chunk).filter_by(document_id=doc_id).count() == n_chunks
        with session_scope() as session:
            assert _upsert_document(session, new) == "skipped"
    finally:
        with session_scope() as session:
            session.query(Document).filter_by(external_id="test_mapint_up").delete()
