# apps/api/tests/test_ops_dashboard_auth.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.ops import router as ops_router
from app.settings import get_settings


def setup_function():
    get_settings.cache_clear()


def teardown_function():
    get_settings.cache_clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ops_router)
    return TestClient(app)


def test_dashboard_401_when_anonymous_and_auth_enforced(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    get_settings.cache_clear()
    r = _client().get("/v1/ops/dashboard")
    assert r.status_code == 401


def test_dashboard_403_when_role_insufficient(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv(
        "AUTH_TOKENS_JSON",
        '{"tok-v": {"sub": "v1", "name": "V", "roles": ["viewer"]}}',
    )
    get_settings.cache_clear()
    r = _client().get(
        "/v1/ops/dashboard", headers={"Authorization": "Bearer tok-v"}
    )
    assert r.status_code == 403


def test_dashboard_allowed_when_auth_off_reaches_db_call(monkeypatch):
    """AUTH_MODE=off grants admin, so the request passes the auth gate and the
    route body runs. Its DB-touching sections (ingest_progress/query_stats)
    each wrap their session_scope() call in try/except and degrade to an
    {"error": ...} value instead of raising, so the response is still 200 —
    but with error strings in ingest_progress/queries against this test's
    unreachable DATABASE_URL. That distinguishes "passed the auth gate and
    ran" from the 401/403 cases above, without needing a live database."""
    monkeypatch.setenv("AUTH_MODE", "off")
    get_settings.cache_clear()
    r = _client().get("/v1/ops/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body["ingest_progress"]
    assert "error" in body["queries"]
    # resources has no DB dependency, so it should succeed normally.
    assert "error" not in body["resources"]
