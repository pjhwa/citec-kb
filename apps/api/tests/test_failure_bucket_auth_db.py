"""D — AUTH_MODE=apikey behaviour of the failure-bucket API (scratch DB)."""

from __future__ import annotations

import json
import os
import uuid

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL")
if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

TOKENS = {
    "tok-viewer": {"sub": "v", "name": "viewer1", "roles": ["viewer"]},
    "tok-author": {"sub": "a", "name": "authpy-sys@v1", "roles": ["author"]},
    "tok-senior": {"sub": "s", "name": "authpy-senior", "roles": ["senior"]},
}


def _make_client(monkeypatch, mode: str):
    from fastapi.testclient import TestClient

    from app.db import session as db_session
    from app.main import app
    from app.settings import get_settings

    monkeypatch.setenv("AUTH_MODE", mode)
    monkeypatch.setenv("AUTH_TOKENS_JSON", json.dumps(TOKENS))
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kw: {"embedded": 0})
    return TestClient(app)


def _teardown(monkeypatch):
    _cleanup()
    # Restore env first, then drop the cached Settings that captured AUTH_MODE/tokens —
    # otherwise later tests in the same process would inherit apikey mode.
    monkeypatch.undo()
    from app.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture()
def apikey_client(monkeypatch):
    yield _make_client(monkeypatch, "apikey")
    _teardown(monkeypatch)


@pytest.fixture()
def off_client(monkeypatch):
    yield _make_client(monkeypatch, "off")
    _teardown(monkeypatch)


def _cleanup():
    from sqlalchemy import text

    from app.db.session import session_scope
    from app.settings import get_settings

    get_settings.cache_clear()
    with session_scope() as s:
        s.execute(text("delete from documents where id in (select document_id from failure_buckets where created_by like 'authpy-%')"))
        s.execute(text("delete from failure_buckets where created_by like 'authpy-%'"))


def _body(**over):
    tag = uuid.uuid4().hex[:8]
    b = {
        "bucket_name": f"auth-test {tag}", "fb_domain": "network", "symptom": f"zzau{tag}",
        "discriminating_signals": [f"zzau{tag} sig"], "root_cause": "rc", "recommended_action": "ra",
        "evidence_ref": "confluence:1", "created_by": "authpy-sys@v1",
    }
    b.update(over)
    return b


def _h(tok):
    return {"X-API-Key": tok}


def test_post_without_token_is_401(apikey_client):
    assert apikey_client.post("/v1/failure-buckets", json=_body()).status_code == 401


def test_post_with_viewer_token_is_403(apikey_client):
    assert apikey_client.post("/v1/failure-buckets", json=_body(), headers=_h("tok-viewer")).status_code == 403


def test_author_can_register_but_not_retire_senior_can(apikey_client):
    r = apikey_client.post("/v1/failure-buckets", json=_body(), headers=_h("tok-author"))
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert apikey_client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "author는 못 지움"}, headers=_h("tok-author")).status_code == 403
    assert apikey_client.post(f"/v1/failure-buckets/{bid}/restore", headers=_h("tok-author")).status_code == 403
    ok = apikey_client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "senior는 지울 수 있음"}, headers=_h("tok-senior"))
    assert ok.status_code == 200 and ok.json()["retired_by"] == "authpy-senior"
    # retire-batch is admin-only
    assert apikey_client.post("/v1/failure-buckets/retire-batch", json={"created_by_prefix": "authpy-", "reason": "senior는 batch 불가"}, headers=_h("tok-senior")).status_code == 403


def test_reads_and_match_are_open_without_token(apikey_client):
    assert apikey_client.get("/v1/failure-buckets").status_code == 200
    r = apikey_client.post("/v1/failure-buckets/match", json={"observed_signals": ["x"]})
    assert r.status_code == 200


def test_created_by_differing_from_token_name_warns_not_rejects(apikey_client):
    r = apikey_client.post("/v1/failure-buckets", json=_body(created_by="authpy-other@v1"), headers=_h("tok-author"))
    assert r.status_code == 200 and "토큰 이름" in r.json()["created_by_warning"]
    same = apikey_client.post("/v1/failure-buckets", json=_body(created_by="authpy-sys@v1"), headers=_h("tok-author"))
    assert "토큰 이름" not in (same.json().get("created_by_warning") or "")
    omitted = apikey_client.post("/v1/failure-buckets", json=_body(created_by=None), headers=_h("tok-author"))
    assert omitted.json()["created_by"] == "authpy-sys@v1"


def test_auth_off_passes_everything_as_before(off_client):
    r = off_client.post("/v1/failure-buckets", json=_body())
    assert r.status_code == 200
    bid = r.json()["id"]
    assert off_client.post(f"/v1/failure-buckets/{bid}/retire", json={"reason": "off 모드는 통과"}).status_code == 200
    assert off_client.post(f"/v1/failure-buckets/{bid}/restore").status_code == 200
    # a token is not required, and a bogus one does not break open mode
    assert off_client.get("/v1/failure-buckets", headers=_h("bogus")).status_code == 200
