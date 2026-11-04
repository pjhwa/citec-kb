"""D — token table cache: no per-request parse, edits apply without restart."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.auth.principal as pr


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    state = SimpleNamespace(config_dir=tmp_path, auth_tokens_json=None, auth_mode="apikey")
    monkeypatch.setattr(pr, "get_settings", lambda: state)
    monkeypatch.setattr(pr, "_token_cache", None)
    return state


def _write(cfg, tokens: dict):
    p = cfg.config_dir / "auth.json"
    p.write_text(json.dumps({"tokens": tokens}))
    return p


def _tok(name="sys@v1", roles=("author",)):
    return {"sub": name, "name": name, "roles": list(roles)}


def test_new_token_is_accepted_without_restart(cfg):
    _write(cfg, {"tokA": _tok("a@v1")})
    assert pr.resolve_principal(api_key="tokA").name == "a@v1"
    assert pr.resolve_principal(api_key="tokB").auth_via == "anonymous"
    _write(cfg, {"tokA": _tok("a@v1"), "tokB-longer": _tok("b@v1")})
    assert pr.resolve_principal(api_key="tokB-longer").name == "b@v1"


def test_revoked_token_is_rejected_on_next_request(cfg):
    _write(cfg, {"tokA": _tok("a@v1"), "tokB": _tok("b@v1")})
    assert pr.resolve_principal(api_key="tokB").auth_via == "apikey"
    _write(cfg, {"tokA": _tok("a@v1")})
    assert pr.resolve_principal(api_key="tokB").auth_via == "anonymous"
    assert pr.resolve_principal(api_key="tokA").auth_via == "apikey"


def test_corrupt_file_rejects_all_tokens_no_stale_cache(cfg):
    p = _write(cfg, {"tokA": _tok()})
    assert pr.resolve_principal(api_key="tokA").auth_via == "apikey"
    p.write_text("{ this is not json")
    assert pr.resolve_principal(api_key="tokA").auth_via == "anonymous"


def test_missing_file_rejects_all_tokens(cfg):
    p = _write(cfg, {"tokA": _tok()})
    assert pr.resolve_principal(api_key="tokA").auth_via == "apikey"
    p.unlink()
    assert pr.resolve_principal(api_key="tokA").auth_via == "anonymous"


def test_unchanged_config_is_not_reparsed(cfg):
    _write(cfg, {"tokA": _tok()})
    before = pr._token_table_builds
    for _ in range(25):
        pr.resolve_principal(api_key="tokA")
    assert pr._token_table_builds - before == 1


def test_env_tokens_change_invalidates_cache(cfg):
    _write(cfg, {})
    assert pr.resolve_principal(api_key="envtok").auth_via == "anonymous"
    cfg.auth_tokens_json = json.dumps({"envtok": _tok("e@v1")})
    assert pr.resolve_principal(api_key="envtok").name == "e@v1"


def test_auth_off_never_reads_token_table_without_credential(cfg):
    cfg.auth_mode = "off"
    before = pr._token_table_builds
    assert pr.resolve_principal().auth_via == "anonymous"
    assert pr._token_table_builds == before
