"""API usage log: caller classification, filtering, middleware capture (no DB) and aggregation (scratch DB)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import app.audit.api_access as aa
from app.auth.deps import require_roles

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")


class H(dict):
    def get(self, k, default=None):
        return super().get(k.lower(), default)


def hd(**kw):
    return H({k.replace("_", "-"): v for k, v in kw.items()})


# ---- classification ---------------------------------------------------------------

def test_internal_vs_external_by_ip():
    assert aa.classify(hd(user_agent="curl/8.5.0"), "172.18.0.5", None) == ("internal", "curl")
    assert aa.classify(hd(user_agent="curl/8.5.0"), "127.0.0.1", None)[0] == "internal"
    assert aa.classify(hd(user_agent="python-requests/2.31"), "10.20.30.40", None) == ("external", "python-requests")
    assert aa.classify(hd(), "192.168.1.9", None)[0] == "external"
    assert aa.classify(hd(), None, None)[0] == "external"           # unknown origin is not trusted as internal


def test_browser_is_web_regardless_of_ip():
    assert aa.classify(hd(sec_fetch_site="same-origin", user_agent="Mozilla/5.0"), "10.1.1.1", None)[0] == "web"
    assert aa.classify(hd(user_agent="Mozilla/5.0 X11", referer="http://h/search.html"), "10.1.1.1", None)[0] == "web"
    assert aa.classify(hd(user_agent="Mozilla/5.0"), "10.1.1.1", None)[0] == "external"      # Mozilla UA alone (a script) is not a browser


def test_name_priority_explicit_header_then_principal_then_ua():
    assert aa.classify(hd(x_citec_client="toolbox-sos", user_agent="curl/8"), "10.0.0.1", "tok")[1] == "toolbox-sos"
    assert aa.classify(hd(user_agent="curl/8"), "10.0.0.1", "insight-ops@v1.4")[1] == "insight-ops@v1.4"
    assert aa.classify(hd(user_agent="curl/8"), "10.0.0.1", None)[1] == "curl"


def test_custom_internal_cidrs(monkeypatch):
    monkeypatch.setenv("API_INTERNAL_CIDRS", "10.0.0.0/8")
    assert aa.classify(hd(), "10.2.3.4", None)[0] == "internal"
    assert aa.classify(hd(), "172.18.0.5", None)[0] == "external"
    monkeypatch.setenv("API_INTERNAL_CIDRS", "garbage,10.0.0.0/8")           # a bad entry never breaks classification
    assert aa.classify(hd(), "10.2.3.4", None)[0] == "internal"


def test_client_ip_prefers_real_ip_then_forwarded_for():
    assert aa.client_ip(hd(x_real_ip="203.0.113.7", x_forwarded_for="1.1.1.1"), "172.18.0.2") == "203.0.113.7"
    assert aa.client_ip(hd(x_forwarded_for="198.51.100.2, 172.18.0.2"), "172.18.0.2") == "198.51.100.2"
    assert aa.client_ip(hd(), "172.18.0.2") == "172.18.0.2"


def test_should_log_filters():
    ok = aa.should_log
    assert ok("/v1/search", "GET", hd())
    assert not ok("/v1/ops/dashboard", "GET", hd())
    assert not ok("/v1/health", "GET", hd())
    assert not ok("/v1/auth/me", "GET", hd())
    assert not ok("/docs", "GET", hd())
    assert not ok("/v1/search", "OPTIONS", hd())
    assert not ok("/v1/search", "GET", hd(x_citec_client="mcp-server"))                 # MCP has its own table
    assert not ok("/v1/lexicon/synonym-candidates", "GET", hd(referer="http://h/admin.html"))   # admin console polling


def test_path_normalization():
    assert aa.normalize_path("/v1/failure-buckets/0ca76661-8a98-422b-b16f-4fa2d9001419/refine") == "/v1/failure-buckets/{id}/refine"
    assert aa.normalize_path("/v1/tickets/12345") == "/v1/tickets/{id}"
    assert aa.normalize_path("/v1/search") == "/v1/search"


# ---- middleware (capture only; DB write replaced) ----------------------------------

@pytest.fixture()
def captured(monkeypatch):
    rows = []
    monkeypatch.setattr(aa, "enqueue", rows.append)
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv("AUTH_TOKENS_JSON", '{"tk": {"sub":"s","name":"insight-ops@v1.4","roles":["author"]}}')
    from app.settings import get_settings
    import app.auth.principal as pr
    get_settings.cache_clear()
    monkeypatch.setattr(pr, "_token_cache", None)
    app = FastAPI()
    app.add_middleware(aa.ApiAccessLogMiddleware)

    @app.get("/v1/items/{item_id}")
    def item(item_id: str, p=Depends(require_roles("author"))):
        return {"ok": item_id}

    @app.get("/v1/ops/status")
    def ops():
        return {}

    yield TestClient(app), rows
    get_settings.cache_clear()


def test_middleware_records_route_template_principal_and_kind(captured):
    c, rows = captured
    r = c.get("/v1/items/42", headers={"Authorization": "Bearer tk", "X-Real-IP": "10.9.8.7"})
    assert r.status_code == 200
    (row,) = rows
    assert row["path"] == "/v1/items/{item_id}"                       # template, so cardinality stays low
    assert (row["client_kind"], row["client_name"], row["client_ip"]) == ("external", "insight-ops@v1.4", "10.9.8.7")
    assert row["status_code"] == 200 and row["method"] == "GET" and row["latency_ms"] >= 0


def test_middleware_records_failures_and_skips_noise(captured):
    c, rows = captured
    assert c.get("/v1/items/1").status_code == 401                    # unauthenticated: still logged
    c.get("/v1/ops/status")
    assert [r["status_code"] for r in rows] == [401]
    c.get("/v1/items/1", headers={"X-Citec-Client": "mcp-server"})
    assert len(rows) == 1


def test_kill_switch(captured, monkeypatch):
    c, rows = captured
    monkeypatch.setenv("API_ACCESS_LOG", "off")
    c.get("/v1/items/1", headers={"Authorization": "Bearer tk"})
    assert rows == []


# ---- aggregation against a scratch DB ---------------------------------------------

@pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to run")
def test_stats_flush_and_prune():
    os.environ["DATABASE_URL"] = _TEST_DSN
    from app.db import session as dbs
    from app.db.models import ApiAccessLog
    from app.settings import get_settings
    from sqlalchemy import delete

    dbs.get_engine.cache_clear(); dbs.get_session_factory.cache_clear(); get_settings.cache_clear()
    with dbs.session_scope() as s:
        s.execute(delete(ApiAccessLog))
    now = datetime.now(timezone.utc)
    rows = [
        dict(client_kind="web", client_name=None, client_ip="10.0.0.5", method="GET", path="/v1/search", status_code=200, latency_ms=40),
        dict(client_kind="web", client_name=None, client_ip="10.0.0.5", method="GET", path="/v1/search", status_code=200, latency_ms=60),
        dict(client_kind="internal", client_name="worker", client_ip="172.18.0.3", method="POST", path="/v1/ingest", status_code=500, latency_ms=300),
        dict(client_kind="external", client_name="toolbox-sos", client_ip="10.2.2.2", method="POST", path="/v1/failure-buckets", status_code=422, latency_ms=20),
    ]
    for r in rows:
        aa.enqueue(r)
    assert aa.flush() == 4
    with dbs.session_scope() as s:
        s.add(ApiAccessLog(client_kind="external", client_name="old", method="GET", path="/v1/x", status_code=200, latency_ms=1,
                           created_at=now - timedelta(days=40)))
        d = aa.api_usage_stats(s, "24h")
    assert d["total"] == 4 and d["by_kind"] == {"web": 2, "internal": 1, "external": 1}
    assert d["errors_5xx"] == 1 and d["errors_4xx"] == 1
    top = d["endpoints"][0]
    assert (top["path"], top["count"]) == ("/v1/search", 2)
    names = {(c["kind"], c["name"]) for c in d["clients"]}
    assert ("external", "toolbox-sos") in names and ("internal", "worker") in names
    assert d["recent_errors"][0]["status"] in (500, 422) and sum(sum(v for k, v in b.items() if k != "t") for b in d["series"]) == 4
    assert aa.api_usage_stats(dbs.get_session_factory()(), "7d")["total"] == 4          # 40-day-old row is outside the window
    assert aa.prune() == 1                                                              # …and gets pruned (30d default)
    with dbs.session_scope() as s:
        s.execute(delete(ApiAccessLog))


def test_endpoint_auth_and_window_validation(monkeypatch):
    from app.routers.ops import router
    from app.settings import get_settings
    import app.auth.principal as pr

    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv("AUTH_TOKENS_JSON", '{"adm": {"sub":"a","name":"boss","roles":["admin"]}, "sen": {"sub":"s","name":"sen","roles":["senior"]}}')
    get_settings.cache_clear()
    monkeypatch.setattr(pr, "_token_cache", None)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    assert c.get("/v1/ops/api-usage").status_code == 401
    assert c.get("/v1/ops/api-usage", headers={"Authorization": "Bearer sen"}).status_code == 403
    assert c.get("/v1/ops/api-usage?window=1y", headers={"Authorization": "Bearer adm"}).status_code == 422
    get_settings.cache_clear()
