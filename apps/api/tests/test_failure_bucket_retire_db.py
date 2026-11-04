"""A — failure bucket soft retire / restore (scratch DB only).

Run: CONFLUENCE_SYNC_TEST_DATABASE_URL=postgresql+psycopg://... pytest tests/test_failure_bucket_retire_db.py
The DB must already be migrated to head (alembic upgrade head).
"""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

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
    # No embedding backend in the scratch env; the index step logs and continues.
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kw: {"embedded": 0})
    _purge_test_rows()
    yield TestClient(app)
    _purge_test_rows()


def _purge_test_rows():
    """Scratch-DB hygiene only (never exposed as an API): drop rows these tests created."""
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(
            text(
                "delete from documents where id in (select document_id from failure_buckets "
                "where created_by like 'retire-pytest-%' or created_by like 'batchpy-%' or created_by like 'keep-%')"
            )
        )
        s.execute(
            text(
                "delete from failure_buckets "
                "where created_by like 'retire-pytest-%' or created_by like 'batchpy-%' or created_by like 'keep-%'"
            )
        )


def _body(tag: str, **over):
    body = {
        "bucket_name": f"retire-test {tag}",
        "fb_domain": "network",
        "symptom": f"zzqx{tag} 연결 끊김 증상",
        "discriminating_signals": [f"zzqx{tag} RST 직전 idle 62초"],
        "root_cause": f"zzqx{tag} LB idle timeout",
        "recommended_action": "timeout 상향",
        "evidence_ref": "confluence:123456",
        "created_by": f"retire-pytest-{tag}@v1",
    }
    body.update(over)
    return body


def _register(client, **over):
    tag = uuid.uuid4().hex[:8]
    r = client.post("/v1/failure-buckets", json=_body(tag, **over))
    assert r.status_code == 200, r.text
    return tag, r.json()


def _search_doc_ids(client, tag):
    r = client.post(
        "/v1/search",
        json={"q": f"zzqx{tag}", "top_k": 20, "multi_query": False, "filters": {"source_type": "failure_bucket"}},
    )
    assert r.status_code == 200, r.text
    return [i.get("document_id") or i.get("id") for i in r.json()["results"]]


def _match_ids(client, tag):
    r = client.post(
        "/v1/failure-buckets/match",
        json={
            "observed_signals": [f"zzqx{tag} RST 직전 idle 62초"],
            "symptom": f"zzqx{tag} 연결 끊김 증상",
            "top_k": 20,
        },
    )
    assert r.status_code == 200, r.text
    return [x["bucket_id"] for x in r.json()["results"]]


def _list_ids(client, **params):
    r = client.get("/v1/failure-buckets", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def test_retire_hides_from_list_match_but_get_works_then_restore(client):
    tag, b = _register(client)
    bid = b["id"]
    assert b["status"] == "active"
    assert bid in _list_ids(client)
    assert bid in _match_ids(client, tag)

    r = client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "잘못 등록된 시험 건"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "retired"
    assert r.json()["retire_reason"] == "잘못 등록된 시험 건"

    assert bid not in _list_ids(client)
    assert bid in _list_ids(client, include_retired="true")
    assert bid not in _match_ids(client, tag)
    g = client.get(f"/v1/failure-buckets/{bid}").json()
    assert g["status"] == "retired" and g["retired_at"] and g["retire_reason"]

    # re-register same content: no possible_duplicate_of pointing at the retired one
    r2 = client.post("/v1/failure-buckets", json=_body(tag, created_by="retire-pytest-other@v1"))
    assert r2.status_code == 200
    dup = r2.json().get("possible_duplicate_of")
    assert not dup or dup["bucket_id"] != bid

    # restore -> everything back
    rr = client.post(f"/v1/failure-buckets/{bid}/restore")
    assert rr.status_code == 200 and rr.json()["status"] == "active"
    assert rr.json()["retired_at"] is None and rr.json()["retire_reason"] is None
    assert bid in _list_ids(client)
    assert bid in _match_ids(client, tag)


def test_retire_removes_mirror_from_search_and_restore_brings_back(client):
    from app.db.models import Document
    from app.db.session import session_scope

    tag, b = _register(client)
    bid, doc_id = b["id"], b["document_id"]
    assert doc_id

    def doc_status():
        with session_scope() as s:
            return s.get(Document, doc_id).status

    assert doc_status() == "active"
    client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "검색 제외 확인용"})
    assert doc_status() == "archived"
    client.post(f"/v1/failure-buckets/{bid}/restore")
    assert doc_status() == "active"


def test_retire_removes_bucket_from_kb_search_and_restore_brings_it_back(client):
    tag, b = _register(client)
    bid, doc_id = b["id"], b["document_id"]
    assert doc_id in _search_doc_ids(client, tag), "precondition: active bucket must be searchable"
    client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "검색 제외 확인용"})
    assert doc_id not in _search_doc_ids(client, tag)
    client.post(f"/v1/failure-buckets/{bid}/restore")
    assert doc_id in _search_doc_ids(client, tag)


def test_retire_is_idempotent_and_keeps_original_reason(client):
    _, b = _register(client)
    bid = b["id"]
    first = client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "첫 번째 사유입니다"})
    second = client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "두 번째 사유입니다"})
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["retire_reason"] == "첫 번째 사유입니다"


def test_refine_on_retired_is_409_and_restore_unblocks(client):
    _, b = _register(client)
    bid = b["id"]
    client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "refine 차단 확인"})
    r = client.post(f"/v1/failure-buckets/{bid}/refine", json={"confirm": True})
    assert r.status_code == 409 and "retired" in r.json()["detail"]
    client.post(f"/v1/failure-buckets/{bid}/restore")
    assert client.post(f"/v1/failure-buckets/{bid}/refine", json={"confirm": True}).status_code == 200


def test_short_reason_rejected_and_unknown_404(client):
    _, b = _register(client)
    r = client.post(f"/v1/failure-buckets/{b['id']}/retire", json={"reason": "짧음"})
    assert r.status_code in (400, 422)
    assert client.post("/v1/failure-buckets/nope/retire", json={"reason": "존재하지 않는 건"}).status_code == 404
    assert client.post("/v1/failure-buckets/nope/restore").status_code == 404


def test_retire_batch_dry_run_then_execute_prefix_only(client):
    tag = uuid.uuid4().hex[:8]
    prefix = f"batchpy-{tag}"
    ids_in = []
    for i in range(2):
        r = client.post("/v1/failure-buckets", json=_body(f"{tag}{i}", created_by=f"{prefix}-room{i}@v1"))
        ids_in.append(r.json()["id"])
    other = client.post("/v1/failure-buckets", json=_body(f"{tag}z", created_by=f"keep-{tag}@v1")).json()["id"]

    dry = client.post(
        "/v1/failure-buckets/retire-batch", json={"created_by_prefix": prefix, "reason": "시험 방 정리 dry"}
    )
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True and sorted(dry.json()["ids"]) == sorted(ids_in)
    for i in ids_in:
        assert client.get(f"/v1/failure-buckets/{i}").json()["status"] == "active"

    run = client.post(
        "/v1/failure-buckets/retire-batch",
        json={"created_by_prefix": prefix, "reason": "시험 방 정리 실행", "dry_run": False},
    )
    assert run.status_code == 200 and run.json()["retired"] == 2
    for i in ids_in:
        assert client.get(f"/v1/failure-buckets/{i}").json()["status"] == "retired"
    assert client.get(f"/v1/failure-buckets/{other}").json()["status"] == "active"

    bad = client.post("/v1/failure-buckets/retire-batch", json={"created_by_prefix": "ab", "reason": "너무 짧은 prefix"})
    assert bad.status_code in (400, 422)


def test_migration_defaults_existing_rows_active():
    """Rows inserted without a status (i.e. pre-migration rows) read back as active."""
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        rid = uuid.uuid4().hex
        s.execute(
            text(
                "insert into failure_buckets (id, bucket_name, fb_domain, evidence_ref) "
                "values (:i, 'legacy-row', 'network', 'legacy:pre-migration')"
            ),
            {"i": rid},
        )
        st = s.execute(text("select status from failure_buckets where id=:i"), {"i": rid}).scalar()
        s.execute(text("delete from failure_buckets where id=:i"), {"i": rid})
    assert st == "active"


def test_graph_neighbor_filter_hides_retired_but_keeps_unknown(client):
    from app.routers.graph import _drop_inactive_buckets

    _, a = _register(client)
    _, b = _register(client)
    client.post(f"/v1/failure-buckets/{b['id']}/retire", json={"reason": "그래프 이웃 제외 확인"})
    kept = _drop_inactive_buckets([{"id": a["id"]}, {"id": b["id"]}, {"id": "not-in-pg"}])
    assert [x["id"] for x in kept] == [a["id"], "not-in-pg"]
