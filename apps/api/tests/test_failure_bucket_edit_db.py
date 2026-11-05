"""Admin curation of failure buckets: PATCH edit, status filter, retire/restore round trip (scratch DB only).

Run: CONFLUENCE_SYNC_TEST_DATABASE_URL=postgresql+psycopg://... pytest tests/test_failure_bucket_edit_db.py
"""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to run")
if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

TAG = "edit-pytest-"


def _purge():
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("delete from documents where id in (select document_id from failure_buckets where created_by like :p)"), {"p": TAG + "%"})
        s.execute(text("delete from failure_buckets where created_by like :p"), {"p": TAG + "%"})


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


def _make(client, name="edit-me"):
    r = client.post("/v1/failure-buckets", json={
        "bucket_name": name, "fb_domain": "tcp", "symptom": "원래 증상", "discriminating_signals": ["sig-a"],
        "root_cause": "원인", "recommended_action": "조치", "evidence_ref": "confluence:1",
        "created_by": TAG + uuid.uuid4().hex[:6]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_patch_edits_only_sent_fields_and_reports_changes(client):
    bid = _make(client)
    r = client.patch(f"/v1/failure-buckets/{bid}", json={"symptom": "고친 증상", "discriminating_signals": ["sig-a", "sig-b", "sig-b", " "], "environment": "CSP"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["symptom"] == "고친 증상" and d["root_cause"] == "원인"            # untouched field kept
    assert d["discriminating_signals"] == ["sig-a", "sig-b"] and d["environment"] == "csp"
    assert set(d["changed_fields"]) == {"symptom", "discriminating_signals", "environment"}
    assert "index" in d                                                         # search mirror rebuilt
    again = client.patch(f"/v1/failure-buckets/{bid}", json={"symptom": "고친 증상"}).json()
    assert again["changed_fields"] == [] and "index" not in again               # no-op does not re-index


def test_patch_validation_and_missing(client):
    bid = _make(client)
    assert client.patch(f"/v1/failure-buckets/{bid}", json={"bucket_name": "  "}).status_code == 422
    assert client.patch(f"/v1/failure-buckets/{bid}", json={"environment": "mars"}).status_code == 422
    assert client.patch(f"/v1/failure-buckets/{bid}", json={"discriminating_signals": []}).status_code == 422
    assert client.patch("/v1/failure-buckets/nope", json={"symptom": "x"}).status_code == 404
    warn = client.patch(f"/v1/failure-buckets/{bid}", json={"fb_domain": "totally-new-domain"}).json()
    assert "fb_domain_warning" in warn


def test_retired_cannot_be_edited_until_restored_and_status_filter(client):
    bid = _make(client)
    assert client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "관리자 정리: 중복 항목"}).status_code == 200
    assert client.patch(f"/v1/failure-buckets/{bid}", json={"symptom": "x"}).status_code == 409
    ids = lambda q: {i["id"] for i in client.get(f"/v1/failure-buckets?limit=200{q}").json()["items"]}  # noqa: E731
    assert bid not in ids("") and bid in ids("&status=retired") and bid not in ids("&status=active")
    assert client.post(f"/v1/failure-buckets/{bid}/restore").status_code == 200
    assert bid in ids("&status=active")
    assert client.patch(f"/v1/failure-buckets/{bid}", json={"symptom": "복구 후 수정"}).status_code == 200
