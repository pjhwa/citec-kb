"""E — '-test' trial registrations and idempotent registration (scratch DB)."""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL")
if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import session as db_session
    from app.main import app
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kw: {"embedded": 0})
    _purge()
    yield TestClient(app)
    _purge()


def _purge():
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("delete from documents where id in (select document_id from failure_buckets where created_by like 'tbpy-%')"))
        s.execute(text("delete from failure_buckets where created_by like 'tbpy-%'"))


def _body(tag, **over):
    b = {
        "bucket_name": f"tb-test {tag}", "fb_domain": "network", "symptom": f"zztb{tag} 증상",
        "discriminating_signals": [f"zztb{tag} 신호"], "root_cause": f"zztb{tag} rc", "recommended_action": "ra",
        "evidence_ref": "confluence:777", "created_by": "tbpy-tool@v0.1-test",
    }
    b.update(over)
    return b


def _ids(client, path, **params):
    r = client.get(path, params={"limit": 200, **params})
    assert r.status_code == 200
    return [i["id"] for i in r.json()["items"]]


def test_test_registration_is_hidden_from_list_match_search_but_get_works(client):
    tag = uuid.uuid4().hex[:8]
    d = client.post("/v1/failure-buckets", json=_body(tag)).json()
    bid = d["id"]
    assert d["status"] == "test"
    assert bid not in _ids(client, "/v1/failure-buckets")
    assert bid in _ids(client, "/v1/failure-buckets", include_retired="true")
    m = client.post(
        "/v1/failure-buckets/match",
        json={"observed_signals": [f"zztb{tag} 신호"], "symptom": f"zztb{tag} 증상", "top_k": 20},
    ).json()
    assert bid not in [x["bucket_id"] for x in m["results"]]
    s = client.post(
        "/v1/search",
        json={"q": f"zztb{tag}", "multi_query": False, "top_k": 20, "filters": {"source_type": "failure_bucket"}},
    ).json()
    assert d["document_id"] not in [x.get("document_id") or x.get("id") for x in s["results"]]
    assert client.get(f"/v1/failure-buckets/{bid}").json()["status"] == "test"


def test_refine_on_test_row_works_and_keeps_it_hidden(client):
    tag = uuid.uuid4().hex[:8]
    d = client.post("/v1/failure-buckets", json=_body(tag)).json()
    r = client.post(f"/v1/failure-buckets/{d['id']}/refine", json={"add_signal": f"zztb{tag} 추가 신호"})
    assert r.status_code == 200
    s = client.post(
        "/v1/search",
        json={"q": f"zztb{tag}", "multi_query": False, "top_k": 20, "filters": {"source_type": "failure_bucket"}},
    ).json()
    assert d["document_id"] not in [x.get("document_id") or x.get("id") for x in s["results"]]


def test_non_test_registration_is_unaffected(client):
    tag = uuid.uuid4().hex[:8]
    d = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool@v0.1")).json()
    assert d["status"] == "active" and d["id"] in _ids(client, "/v1/failure-buckets")


def test_same_key_twice_gives_one_row(client):
    tag = uuid.uuid4().hex[:8]
    first = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool@v0.1")).json()
    second = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool@v0.1"))
    assert second.status_code == 200
    assert second.json()["id"] == first["id"] and second.json()["already_registered"] is True
    assert first["already_registered"] is False
    rows = [i for i in client.get("/v1/failure-buckets", params={"limit": 200}).json()["items"] if i["created_by"] == "tbpy-tool@v0.1" and i["bucket_name"] == first["bucket_name"]]
    assert len(rows) == 1


def test_idempotency_key_is_creator_evidence_and_name(client):
    tag = uuid.uuid4().hex[:8]
    a = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool@v0.1")).json()
    other_ref = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool@v0.1", evidence_ref="confluence:778")).json()
    other_by = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool2@v0.1")).json()
    assert len({a["id"], other_ref["id"], other_by["id"]}) == 3


def test_test_rows_are_idempotent_too_and_retire_batch_cleans_them(client):
    tag = uuid.uuid4().hex[:8]
    a = client.post("/v1/failure-buckets", json=_body(tag)).json()
    b = client.post("/v1/failure-buckets", json=_body(tag)).json()
    assert a["id"] == b["id"] and b["already_registered"] is True
    run = client.post(
        "/v1/failure-buckets/retire-batch",
        json={"created_by_prefix": "tbpy-tool@", "reason": "시험 등록 정리", "dry_run": False},
    ).json()
    assert a["id"] in run["ids"]
    assert client.get(f"/v1/failure-buckets/{a['id']}").json()["status"] == "retired"


def test_restore_promotes_test_row_to_active(client):
    tag = uuid.uuid4().hex[:8]
    d = client.post("/v1/failure-buckets", json=_body(tag)).json()
    r = client.post(f"/v1/failure-buckets/{d['id']}/restore")
    assert r.json()["status"] == "active" and d["id"] in _ids(client, "/v1/failure-buckets")


def test_retired_row_does_not_block_reregistration(client):
    tag = uuid.uuid4().hex[:8]
    a = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool@v0.1")).json()
    client.post(f"/v1/failure-buckets/{a['id']}/retire", json={"reason": "잘못 등록해서 되돌림"})
    b = client.post("/v1/failure-buckets", json=_body(tag, created_by="tbpy-tool@v0.1")).json()
    assert b["id"] != a["id"] and b["already_registered"] is False
