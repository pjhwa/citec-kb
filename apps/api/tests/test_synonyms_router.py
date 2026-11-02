import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.synonym_candidates as r
from app.settings import get_settings
from app.synonyms import runtime as rt, store


def _client():
    app = FastAPI()
    app.include_router(r.router)
    return TestClient(app)


def _fake_set_status(result=None, exc=None, seen=None):
    # Mirrors the REAL signature: set_status(cid, action, *, decided_by, note=None)
    def fake(cid, action, *, decided_by, note=None):
        if seen is not None:
            seen.update(cid=cid, action=action, decided_by=decided_by, note=note)
        if exc:
            raise exc
        return result if result is not None else {"id": cid, "status": "approved"}

    return fake


def test_list_passes_filters(monkeypatch):
    seen = {}

    def fake(*, status=None, limit=100, offset=0):
        seen.update(status=status, limit=limit, offset=offset)
        return {"total": 0, "items": []}

    monkeypatch.setattr(store, "list_candidates", fake)
    res = _client().get("/v1/lexicon/synonym-candidates?status=proposed&limit=5")
    assert res.status_code == 200 and seen == {"status": "proposed", "limit": 5, "offset": 0}


def test_list_rejects_bad_status():
    assert _client().get("/v1/lexicon/synonym-candidates?status=bogus").status_code == 422


def test_approve_resets_runtime_cache(monkeypatch):
    called, seen = {}, {}
    monkeypatch.setattr(store, "set_status", _fake_set_status(seen=seen))
    monkeypatch.setattr(rt, "reset_cache", lambda: called.setdefault("reset", True))
    res = _client().post("/v1/lexicon/synonym-candidates/7/approve", json={"note": "ok"})
    assert res.status_code == 200 and res.json()["status"] == "approved" and called["reset"]
    assert seen == {"cid": 7, "action": "approve", "decided_by": "anonymous", "note": "ok"}


def test_transition_and_not_found_errors(monkeypatch):
    monkeypatch.setattr(store, "set_status", _fake_set_status(exc=store.TransitionError("x")))
    assert _client().post("/v1/lexicon/synonym-candidates/1/disable", json={}).status_code == 409
    monkeypatch.setattr(store, "set_status", _fake_set_status(exc=store.NotFound("nope")))
    assert _client().post("/v1/lexicon/synonym-candidates/1/approve", json={}).status_code == 404


@pytest.mark.parametrize(
    "exc,code", [(store.NotFound("n"), 404), (store.TransitionError("t"), 409)]
)
def test_error_does_not_reset_cache(monkeypatch, exc, code):
    called = {}
    monkeypatch.setattr(store, "set_status", _fake_set_status(exc=exc))
    monkeypatch.setattr(rt, "reset_cache", lambda: called.setdefault("reset", True))
    res = _client().post("/v1/lexicon/synonym-candidates/1/reject", json={})
    assert res.status_code == code and "reset" not in called


@pytest.mark.parametrize("action", ["approve", "reject", "disable"])
def test_decision_without_body_ok(monkeypatch, action):
    seen = {}
    monkeypatch.setattr(store, "set_status", _fake_set_status(seen=seen))
    monkeypatch.setattr(rt, "reset_cache", lambda: None)
    res = _client().post(f"/v1/lexicon/synonym-candidates/3/{action}")
    assert res.status_code == 200 and seen["note"] is None and seen["action"] == action


def _fake_upsert(seen=None):
    # Mirrors the REAL signature: upsert_candidates(items, *, run_id, model)
    def fake(items, *, run_id, model):
        if seen is not None:
            seen.update(items=items, run_id=run_id, model=model)
        return {"inserted": len(items), "skipped": 0, "invalid": 0}

    return fake


def test_import_validates_and_forwards(monkeypatch):
    seen = {}
    monkeypatch.setattr(store, "upsert_candidates", _fake_upsert(seen))
    body = {"run_id": "r", "model": "m", "items": [{"terms": ["OOM2x", "메모리 고갈"], "evidence": {}}]}
    res = _client().post("/v1/lexicon/synonym-candidates/import", json=body)
    assert res.status_code == 200 and res.json()["inserted"] == 1
    assert seen["run_id"] == "r" and seen["model"] == "m"
    assert seen["items"][0]["terms"] == ["OOM2x", "메모리 고갈"]
    assert _client().post("/v1/lexicon/synonym-candidates/import", json={"items": "x"}).status_code == 422


def test_import_too_many_items_422(monkeypatch):
    monkeypatch.setattr(store, "upsert_candidates", _fake_upsert())
    items = [{"terms": ["aaa", "bbb"]}] * 2001
    assert _client().post("/v1/lexicon/synonym-candidates/import", json={"items": items}).status_code == 422


def test_import_invalid_items_reported_not_error(monkeypatch):
    # Real store.clean_terms path is DB-free for invalid items (no session opened for them)
    from contextlib import contextmanager

    @contextmanager
    def no_db():
        yield object()

    monkeypatch.setattr(store, "session_scope", no_db)
    res = _client().post(
        "/v1/lexicon/synonym-candidates/import",
        json={"items": [{"terms": ["행", "펜싱"]}]},
    )
    assert res.status_code == 200
    assert res.json() == {"inserted": 0, "skipped": 0, "invalid": 1}


@pytest.mark.parametrize(
    "item",
    [
        {"terms": [f"term{i}x" for i in range(21)]},
        {"terms": ["a" * 101, "bbb"]},
        {"terms": ["aaa", "bbb"], "evidence": {"k": "가" * 9000}},
    ],
)
def test_import_item_limits_422(monkeypatch, item):
    monkeypatch.setattr(store, "upsert_candidates", _fake_upsert())
    res = _client().post("/v1/lexicon/synonym-candidates/import", json={"items": [item]})
    assert res.status_code == 422


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv(
        "AUTH_TOKENS_JSON",
        '{"tok-v": {"sub": "v", "name": "V", "roles": ["viewer"]},'
        ' "tok-s": {"sub": "s", "name": "Sen", "roles": ["viewer", "senior"]},'
        ' "tok-a": {"sub": "a", "name": "Adm", "roles": ["viewer", "admin"]}}',
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_auth_enforced(monkeypatch, enforced):
    seen = {}
    monkeypatch.setattr(store, "set_status", _fake_set_status(seen=seen))
    monkeypatch.setattr(store, "upsert_candidates", _fake_upsert())
    monkeypatch.setattr(store, "list_candidates", lambda *, status=None, limit=100, offset=0: {"total": 0, "items": []})
    monkeypatch.setattr(rt, "reset_cache", lambda: None)
    c = _client()
    base = "/v1/lexicon/synonym-candidates"
    imp = {"items": []}
    for path, body in (
        (f"{base}/1/approve", {}),
        (f"{base}/1/reject", {}),
        (f"{base}/1/disable", {}),
        (f"{base}/import", imp),
    ):
        assert c.post(path, json=body).status_code == 401
        assert c.post(path, json=body, headers={"Authorization": "Bearer tok-v"}).status_code == 403
        assert c.post(path, json=body, headers={"Authorization": "Bearer tok-a"}).status_code == 200
    assert seen["decided_by"] == "Adm"
    res = c.post(f"{base}/1/approve", json={}, headers={"Authorization": "Bearer tok-s"})
    assert res.status_code == 200 and seen["decided_by"] == "Sen"
    assert c.get(base).status_code == 200  # list stays readable


def test_auth_off_allows_writes(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    get_settings.cache_clear()
    monkeypatch.setattr(store, "upsert_candidates", _fake_upsert())
    assert _client().post("/v1/lexicon/synonym-candidates/import", json={"items": []}).status_code == 200
    get_settings.cache_clear()
