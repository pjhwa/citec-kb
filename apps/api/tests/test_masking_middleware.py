"""MaskingMiddleware on a mini app. Principal/settings/audit are patched; no DB."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.masking.middleware as MW
from app.auth.principal import ANON_OPEN, ANON_RESTRICTED, Principal
from app.masking.dictionary import Dictionary

BODY = "call 031-123-4567 host 10.0.0.1 end"


def _p(*roles):
    return Principal(sub="u1", name="u1", roles=frozenset(roles), auth_via="apikey")


KEYS = {"viewer": _p("viewer"), "author": _p("author"), "senior": _p("senior"), "admin": _p("admin")}


def harness(monkeypatch):
    """Mini app behind MaskingMiddleware. Other masking test modules import this."""
    audit: list = []
    state = {"mode": "apikey", "audit_fail": False}

    class S:
        @property
        def auth_mode(self):
            return state["mode"]

    monkeypatch.setattr(MW, "get_settings", lambda: S())
    monkeypatch.setattr(MW, "get_dictionary", lambda: Dictionary.empty("missing"))
    monkeypatch.setattr(
        MW, "resolve_principal",
        lambda authorization=None, api_key=None: KEYS.get(api_key or "", ANON_RESTRICTED if state["mode"] != "off" else ANON_OPEN),
    )

    def fake_audit(**kw):
        if state["audit_fail"]:
            raise RuntimeError("db down")
        audit.append(kw)

    monkeypatch.setattr(MW, "write_unmask_audit", fake_audit)

    app = FastAPI()
    app.add_middleware(MW.MaskingMiddleware)
    return app, audit, state


@pytest.fixture
def env(monkeypatch):
    app, audit, state = harness(monkeypatch)

    @app.get("/v1/doc")
    def doc():
        return {"external_id": "T-1", "body_md": BODY}

    @app.get("/v1/list")
    def lst():
        return [{"body_md": BODY}]

    @app.get("/v1/health")
    def health():
        return {"note": BODY}

    @app.get("/v1/health/llm")
    def health_llm():
        return {"note": BODY}

    @app.get("/v1/healthcheck-x")
    def healthcheck_x():
        return {"note": BODY}

    @app.get("/other")
    def other():
        return {"note": BODY}

    @app.get("/v1/boom")
    def boom():
        return {"body_md": BODY}

    @app.get("/v1/missing")
    def missing():
        raise HTTPException(status_code=404, detail=f"not found: {BODY}")

    return TestClient(app), audit, state


def test_viewer_masked_with_meta_and_header(env):
    c, audit, _ = env
    r = c.get("/v1/doc", headers={"X-API-Key": "viewer"})
    assert r.json()["body_md"] == "call [PHONE-1] host [IP-1] end"
    assert r.json()["masking"]["counts"] == {"pii": 1, "net": 1}
    assert r.headers["x-citec-masking"] == "net=1,pii=1" and not audit


def test_array_response_masked_header_only(env):
    c, _, _ = env
    r = c.get("/v1/list", headers={"X-API-Key": "author"})
    assert r.json() == [{"body_md": "call [PHONE-1] host [IP-1] end"}]
    assert r.headers["x-citec-masking"] == "net=1,pii=1"


def test_author_cannot_unmask_denied_and_audited(env):
    c, audit, _ = env
    r = c.get("/v1/doc?unmask=1", headers={"X-API-Key": "author"})
    assert "[PHONE-1]" in r.json()["body_md"]
    assert r.json()["masking"]["unmask_denied"] is True
    assert audit[0]["outcome"] == "denied"


def test_senior_default_masked_toggle_reveals_and_audits(env):
    c, audit, _ = env
    assert "[PHONE-1]" in c.get("/v1/doc", headers={"X-API-Key": "senior"}).json()["body_md"]
    assert not audit
    r = c.get("/v1/doc", headers={"X-API-Key": "admin", "X-Citec-Unmask": "1"})
    assert r.json()["body_md"] == BODY
    assert audit[0]["outcome"] == "granted" and audit[0]["counts"] == {"pii": 1, "net": 1}
    assert audit[0]["doc_refs"] == ["T-1"]


def test_audit_failure_keeps_masked(env):
    c, _, state = env
    state["audit_fail"] = True
    r = c.get("/v1/doc?unmask=1", headers={"X-API-Key": "admin"})
    assert "[PHONE-1]" in r.json()["body_md"] and r.json()["masking"]["audit_failed"] is True
    assert "031-123-4567" not in r.text and "10.0.0.1" not in r.text


def test_auth_off_nobody_unmasks(env):
    c, audit, state = env
    state["mode"] = "off"
    r = c.get("/v1/doc?unmask=1")
    assert "[PHONE-1]" in r.json()["body_md"] and r.json()["masking"]["unmask_denied"] is True
    assert "031-123-4567" not in r.text
    assert audit[0]["outcome"] == "denied"


def test_out_of_scope_and_skipped_paths_untouched(env):
    c, _, _ = env
    assert c.get("/other").json()["note"] == BODY
    assert c.get("/v1/health").json()["note"] == BODY


def test_engine_error_is_fail_closed(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setattr(MW, "mask_json", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = c.get("/v1/boom", headers={"X-API-Key": "viewer"})
    assert r.status_code == 500 and BODY not in r.text
    assert "031-123-4567" not in r.text and "10.0.0.1" not in r.text


def test_http_error_json_is_masked_and_keeps_status(env):
    c, _, _ = env
    r = c.get("/v1/missing", headers={"X-API-Key": "viewer"})
    assert r.status_code == 404
    assert "031-123-4567" not in r.text and "10.0.0.1" not in r.text
    assert "[PHONE-1]" in r.json()["detail"]


def test_skip_list_matches_whole_segments_only(env):
    c, _, _ = env
    assert c.get("/v1/health").json()["note"] == BODY
    assert c.get("/v1/health/llm").json()["note"] == BODY
    r = c.get("/v1/healthcheck-x", headers={"X-API-Key": "viewer"})
    assert "031-123-4567" not in r.text and "[PHONE-1]" in r.json()["note"]


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/v1/health", False), ("/v1/health/llm", False), ("/v1/healthcheck-x", True),
        ("/v1/ops/mcp", False), ("/v1/opsx", True), ("/v1/auth/me", False), ("/v1/auth", False),
        ("/v1/authz", True), ("/api/docs", False), ("/api/docs/x", False), ("/api/docsearch", True),
        ("/api/openapi", False), ("/api/openapix", True), ("/v1/documents", True), ("/other", False),
    ],
)
def test_in_scope_prefix_matching(path, expected):
    assert MW.in_scope(path) is expected
