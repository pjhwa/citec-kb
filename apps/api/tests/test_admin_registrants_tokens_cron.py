"""Admin page backends: system-registrant allowlist, token issuing, cron status (no DB needed)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth.principal as principal_mod
import app.failure_buckets.service as svc
from app.routers.auth_tokens import router as tokens_router
from app.routers.failure_buckets import router as fb_router
from app.routers.ops import router as ops_router
from app.settings import get_settings

ADMIN = {"Authorization": "Bearer boot-admin"}
BOOT = '{"boot-admin": {"sub": "boot", "name": "boot-admin", "roles": ["admin"]}, "tok-a": {"sub": "a", "name": "plain-author", "roles": ["author"]}}'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg, data, logs = tmp_path / "config", tmp_path / "data", tmp_path / "logs"
    for d in (cfg, data, logs):
        d.mkdir()
    (cfg / "system_registrants.json").write_text(json.dumps({"systems": ["insight-ops", "agent-room"]}))
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv("AUTH_TOKENS_JSON", BOOT)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("LOG_DIR", str(logs))
    get_settings.cache_clear()
    s = get_settings()
    s.config_dir = cfg
    monkeypatch.setattr(svc, "_registrants_cache", (None, frozenset()))
    monkeypatch.setattr(principal_mod, "_token_cache", None)
    app = FastAPI()
    for r in (tokens_router, fb_router, ops_router):
        app.include_router(r)
    yield TestClient(app), tmp_path
    get_settings.cache_clear()


# ---- system registrants -----------------------------------------------------------

def test_registrants_default_from_config_and_override_after_edit(env, monkeypatch):
    c, tmp = env
    monkeypatch.setattr("app.routers.failure_buckets._registrant_counts_24h", lambda: {"insight-ops": 3})
    d = c.get("/v1/failure-buckets/system-registrants").json()
    assert d["systems"] == ["agent-room", "insight-ops"] and d["source"] == "config"
    assert d["counts_24h"] == {"insight-ops": 3}

    r = c.post("/v1/failure-buckets/system-registrants", json={"name": "toolbox-x"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["source"] == "override"
    assert "toolbox-x" in r.json()["systems"] and "insight-ops" in r.json()["systems"]
    assert svc.classify_registrant("toolbox-x@v1") == ("system", None)  # effective immediately
    stored = json.loads((tmp / "data" / "system_registrants.json").read_text())
    assert stored["updated_by"] == "boot-admin"

    r = c.delete("/v1/failure-buckets/system-registrants/toolbox-x", headers=ADMIN)
    assert r.status_code == 200 and "toolbox-x" not in r.json()["systems"]
    assert svc.classify_registrant("toolbox-x@v1")[0] == "human"
    assert c.delete("/v1/failure-buckets/system-registrants/nope", headers=ADMIN).status_code == 404


def test_registrants_validation_and_auth(env):
    c, _ = env
    assert c.post("/v1/failure-buckets/system-registrants", json={"name": "Bad Name"}, headers=ADMIN).status_code == 422
    assert c.post("/v1/failure-buckets/system-registrants", json={"name": "x"}).status_code == 401
    author = {"Authorization": "Bearer tok-a"}
    assert c.post("/v1/failure-buckets/system-registrants", json={"name": "x"}, headers=author).status_code == 403
    assert c.get("/v1/failure-buckets/system-registrants").status_code == 200  # read stays open


def test_corrupt_override_falls_back_to_config(env):
    c, tmp = env
    (tmp / "data" / "system_registrants.json").write_text("{not json")
    assert set(c.get("/v1/failure-buckets/system-registrants").json()["systems"]) == {"insight-ops", "agent-room"}


# ---- tokens -----------------------------------------------------------------------

def test_issue_use_list_revoke_token(env):
    c, tmp = env
    r = c.post("/v1/auth/tokens", json={"name": "insight-ops@v1.4"}, headers=ADMIN)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    tok = r.json()["token"]
    assert r.json()["roles"] == ["author"]

    stored = (tmp / "data" / "auth_tokens.json").read_text()
    assert tok not in stored and "sha256:" in stored  # only the digest is persisted

    # the issued token authenticates as author (can't reach admin routes)
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/v1/auth/tokens", headers=h).status_code == 403
    # a stored digest is not itself a credential
    digest = next(iter(json.loads(stored)["tokens"]))
    assert c.get("/v1/auth/tokens", headers={"Authorization": f"Bearer {digest}"}).status_code in (401, 403)

    listing = c.get("/v1/auth/tokens", headers=ADMIN).json()
    assert tok not in json.dumps(listing)
    names = {t["name"]: t for t in listing["tokens"]}
    assert names["insight-ops@v1.4"]["source"] == "admin" and names["boot-admin"]["source"] == "config"

    assert c.post("/v1/auth/tokens", json={"name": "insight-ops@v1.4"}, headers=ADMIN).status_code == 409
    r2 = c.post("/v1/auth/tokens", json={"name": "insight-ops@v1.4", "rotate": True}, headers=ADMIN)
    assert r2.status_code == 200 and r2.json()["token"] != tok and r2.json()["rotated"] is True
    old = {"Authorization": f"Bearer {tok}"}
    assert c.get("/v1/failure-buckets/system-registrants", headers=old).status_code == 200  # open read, but:
    assert principal_mod.lookup_token(principal_mod._load_token_table(), tok) is None  # old token revoked

    assert c.delete("/v1/auth/tokens/insight-ops@v1.4", headers=ADMIN).status_code == 200
    assert c.delete("/v1/auth/tokens/insight-ops@v1.4", headers=ADMIN).status_code == 404


def test_token_guards(env):
    c, _ = env
    post = lambda body: c.post("/v1/auth/tokens", json=body, headers=ADMIN)  # noqa: E731
    assert post({"name": "h", "roles": ["admin"]}).status_code == 422                       # needs allow_privileged
    assert post({"name": "x@v1", "roles": ["admin"], "allow_privileged": True}).status_code == 422  # system name
    assert post({"name": "ok-human", "roles": ["admin"], "allow_privileged": True}).status_code == 200
    assert post({"name": "bad name"}).status_code == 422
    assert post({"name": "plain-author"}).status_code == 409                                 # exists in env/config
    assert c.delete("/v1/auth/tokens/plain-author", headers=ADMIN).status_code == 409        # host-managed
    assert c.delete("/v1/auth/tokens/boot-admin", headers=ADMIN).status_code == 409          # self-revoke guard


def test_issue_refused_when_auth_off(env, monkeypatch):
    c, _ = env
    monkeypatch.setenv("AUTH_MODE", "off")
    get_settings.cache_clear()
    r = c.post("/v1/auth/tokens", json={"name": "anyone"})
    assert r.status_code == 409 and "AUTH_MODE=off" in r.json()["detail"]
    assert c.delete("/v1/auth/tokens/anyone").status_code == 409


# ---- cron endpoint ----------------------------------------------------------------

def test_cron_endpoint_missing_then_present(env):
    c, tmp = env
    assert c.get("/v1/ops/cron", headers=ADMIN).json()["available"] is False
    (tmp / "logs" / "cron_status.json").write_text(json.dumps({"generated_at": "x", "jobs": [{"status": "ok"}]}))
    d = c.get("/v1/ops/cron", headers=ADMIN).json()
    assert d["available"] is True and d["snapshot_stale"] is False and d["jobs"][0]["status"] == "ok"
    assert c.get("/v1/ops/cron").status_code == 401
