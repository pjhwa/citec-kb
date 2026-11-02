"""doc_expansion store (scratch DB only)."""

from __future__ import annotations

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


@pytest.fixture
def doc_id():
    from app.db import session as db_session
    from app.db.models import DocExpansion, Document
    from app.db.session import session_scope
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    did = str(uuid.uuid4())
    with session_scope() as s:
        s.query(DocExpansion).delete()
        s.add(
            Document(
                id=did, source_type="confluence_map", external_id=f"x-{did}",
                title="t", body_md="b", content_hash="h",
            )
        )
    yield did
    with session_scope() as s:
        s.query(DocExpansion).delete()
        s.query(Document).filter(Document.id == did).delete()
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def test_upsert_computes_tsv_and_is_idempotent(doc_id):
    from app.docexp import store

    rec = {"summary": "메모리 부족 종료", "keywords": ["OOM", "서버 다운 현상"], "grounded": ["OOM"]}
    store.upsert(doc_id, rec, rule="meeting", model="m", run_id="r1")
    store.upsert(doc_id, rec, rule="meeting", model="m", run_id="r2")
    row = store.get(doc_id)
    assert row["run_id"] == "r2" and row["grounded"] == ["OOM"]
    assert store.matches(doc_id, "서버", column="tsv_all") is True
    assert store.matches(doc_id, "서버", column="tsv_grounded") is False
    assert store.matches(doc_id, "oom", column="tsv_grounded") is True
    assert store.count() == 1


def test_existing_ids_and_stats(doc_id):
    from app.docexp import store

    assert store.existing_ids([doc_id, "nope"]) == set()
    store.upsert(doc_id, {"summary": "s", "keywords": ["a1", "b2"], "grounded": ["a1"]}, rule="api", model="m", run_id="r")
    assert store.existing_ids([doc_id, "nope"]) == {doc_id}
    st = store.stats()
    assert st["count"] == 1 and st["by_rule"] == {"api": 1} and st["grounded_ratio"]["0.5-1"] == 1


def test_upsert_stores_hash_and_existing_ids_treats_mismatch_as_missing(doc_id):
    from app.docexp import store

    rec = {"summary": "s", "keywords": ["a1", "b2"], "grounded": []}
    store.upsert(doc_id, rec, rule="api", model="m", run_id="r", content_hash="h")
    assert store.get(doc_id)["source_content_hash"] == "h"
    assert store.existing_ids([doc_id], hashes={doc_id: "h"}) == {doc_id}
    assert store.existing_ids([doc_id], hashes={doc_id: "changed"}) == set()
    assert store.existing_ids([doc_id]) == {doc_id}  # no hashes given: presence only


def test_tsv_is_weighted_keywords_a_summary_c(doc_id):
    from sqlalchemy import text

    from app.db.session import session_scope
    from app.docexp import store

    rec = {"summary": "요약문 설명", "keywords": ["OOM", "서버 다운"], "grounded": ["OOM"]}
    store.upsert(doc_id, rec, rule="api", model="m", run_id="r", content_hash="h")
    with session_scope() as s:
        allv, gv = s.execute(
            text("select tsv_all::text, tsv_grounded::text from doc_expansion where document_id=:d"), {"d": doc_id}
        ).one()
    assert "'oom':1A" in allv and "'요약문':" in allv and allv.count("C") >= 1
    assert "A" in gv and "C" not in gv and "'서버'" not in gv
