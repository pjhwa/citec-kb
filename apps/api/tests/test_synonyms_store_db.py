"""Synonym candidate store (scratch DB only)."""

from __future__ import annotations

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
def _db():
    from app.db import session as db_session
    from app.db.models import SearchSynonymCandidate
    from app.db.session import get_engine, session_scope
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    SearchSynonymCandidate.__table__.create(bind=get_engine(), checkfirst=True)
    with session_scope() as s:
        s.query(SearchSynonymCandidate).delete()
    yield
    with session_scope() as s:
        s.query(SearchSynonymCandidate).delete()
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _item(terms, tickets=("T-1", "T-2")):
    return {"terms": terms, "evidence": {"tickets": list(tickets), "df": {}, "reason": "r"}}


def test_upsert_inserts_then_skips_duplicates():
    from app.synonyms import store

    r1 = store.upsert_candidates([_item(["OOM2x", "메모리 고갈"])], run_id="r1", model="m")
    assert r1 == {"inserted": 1, "skipped": 0, "invalid": 0}
    r2 = store.upsert_candidates([_item(["메모리 고갈", "oom2x"])], run_id="r2", model="m")
    assert r2 == {"inserted": 0, "skipped": 1, "invalid": 0}


def test_upsert_rejects_invalid_terms():
    from app.synonyms import store

    r = store.upsert_candidates([_item(["행", "펜싱"])], run_id="r", model="m")
    assert r["invalid"] == 1 and r["inserted"] == 0


def test_status_transitions_and_runtime_load():
    from app.synonyms import store

    store.upsert_candidates([_item(["OOM2x", "메모리 고갈"])], run_id="r", model="m")
    cid = store.list_candidates(status="proposed")["items"][0]["id"]
    assert store.load_groups(include_proposed=False) == []
    assert store.load_groups(include_proposed=True) == [["OOM2x", "메모리 고갈"]]

    assert store.set_status(cid, "approve", decided_by="tester")["status"] == "approved"
    assert store.load_groups(include_proposed=False) == [["OOM2x", "메모리 고갈"]]
    assert store.set_status(cid, "disable", decided_by="tester")["status"] == "disabled"
    assert store.load_groups(include_proposed=False) == []


def test_illegal_transition_raises():
    from app.synonyms import store

    store.upsert_candidates([_item(["OOM2x", "메모리 고갈"])], run_id="r", model="m")
    cid = store.list_candidates(status="proposed")["items"][0]["id"]
    with pytest.raises(store.TransitionError):
        store.set_status(cid, "disable", decided_by="t")  # proposed -> disabled not allowed
    with pytest.raises(store.NotFound):
        store.set_status(999999, "approve", decided_by="t")


def _one(terms=("OOM2x", "메모리 고갈")):
    from app.synonyms import store

    store.upsert_candidates([_item(list(terms))], run_id="r", model="m")
    return store.list_candidates(status="proposed")["items"][0]["id"]


def test_reject_path_and_no_approve_after_reject():
    from app.synonyms import store

    cid = _one()
    assert store.set_status(cid, "reject", decided_by="t")["status"] == "rejected"
    with pytest.raises(store.TransitionError):
        store.set_status(cid, "approve", decided_by="t")
    assert store.load_groups(include_proposed=True) == []


def test_approve_from_disabled_works():
    from app.synonyms import store

    cid = _one()
    store.set_status(cid, "approve", decided_by="t")
    store.set_status(cid, "disable", decided_by="t")
    assert store.set_status(cid, "approve", decided_by="t2")["status"] == "approved"
    assert store.load_groups() == [["OOM2x", "메모리 고갈"]]


def test_note_and_decision_metadata_stored_and_returned():
    from app.synonyms import store

    cid = _one()
    row = store.set_status(cid, "approve", decided_by="alice", note="looks right")
    assert row["note"] == "looks right"
    assert row["decided_by"] == "alice"
    assert row["decided_at"]
    again = store.list_candidates(status="approved")["items"][0]
    assert again["note"] == "looks right" and again["decided_by"] == "alice" and again["decided_at"]
    # note is preserved when a later action passes none
    row2 = store.set_status(cid, "disable", decided_by="bob")
    assert row2["note"] == "looks right" and row2["decided_by"] == "bob"


def test_existing_keys_returns_terms_key():
    from app.synonyms import store
    from app.synonyms.rules import terms_key

    _one()
    assert store.existing_keys() == {terms_key(["OOM2x", "메모리 고갈"])}


def test_unknown_action_raises_transition_error():
    from app.synonyms import store

    cid = _one()
    with pytest.raises(store.TransitionError):
        store.set_status(cid, "explode", decided_by="t")
