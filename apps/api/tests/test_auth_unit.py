"""Auth principal + role gate unit tests (no HTTP stack)."""

from app.auth.principal import (
    ALL_ROLES,
    ANON_OPEN,
    resolve_principal,
)
from app.settings import get_settings


def setup_function():
    get_settings.cache_clear()


def teardown_function():
    get_settings.cache_clear()


def test_auth_mode_off_grants_all_roles(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    get_settings.cache_clear()
    p = resolve_principal()
    assert p.sub == "anonymous"
    assert p.roles == ALL_ROLES
    assert p.has_any("admin")


def test_auth_mode_apikey_anonymous_restricted(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    get_settings.cache_clear()
    p = resolve_principal()
    assert p.auth_via == "anonymous"
    assert not p.has_any("author", "senior", "admin")
    assert p.has_any("viewer")


def test_auth_mode_apikey_token_roles(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv(
        "AUTH_TOKENS_JSON",
        '{"tok-s": {"sub": "s1", "name": "S", "roles": ["viewer", "author", "senior"]}}',
    )
    get_settings.cache_clear()
    p = resolve_principal(authorization="Bearer tok-s")
    assert p.sub == "s1"
    assert p.has_any("senior")
    assert not p.has_any("admin")
    p2 = resolve_principal(api_key="tok-s")
    assert p2.sub == "s1"


def test_oidc_stub_token(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "oidc_stub")
    get_settings.cache_clear()
    p = resolve_principal(authorization="Bearer stub:alice:senior,admin")
    assert p.sub == "alice"
    assert p.auth_via == "oidc_stub"
    assert p.has_any("senior", "admin")


def test_anon_open_constant():
    assert ANON_OPEN.has_any("admin")
