"""OIDC JWT mint/validate + role claim mapping (local HS256)."""

import time

import jwt
import pytest

from app.auth.oidc import mint_local_jwt, principal_from_claims, validate_bearer_jwt
from app.auth.principal import resolve_principal
from app.settings import get_settings


def setup_function():
    get_settings.cache_clear()


def teardown_function():
    get_settings.cache_clear()


def test_principal_from_claims_roles():
    p = principal_from_claims(
        {"sub": "u1", "name": "User One", "roles": ["senior", "author"]}
    )
    assert p.sub == "u1"
    assert p.has_any("senior")
    assert p.has_any("author")
    assert not p.has_any("admin")


def test_principal_keycloak_realm_access():
    p = principal_from_claims(
        {
            "sub": "kc1",
            "preferred_username": "alice",
            "realm_access": {"roles": ["kb-admin", "offline_access"]},
        }
    )
    assert p.name == "alice"
    assert p.has_any("admin")


def test_mint_and_validate_hs256(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_JWT_SECRET", "unit-test-secret-32chars-minimum!!")
    monkeypatch.setenv("OIDC_ISSUER", "http://localhost/realms/test")
    monkeypatch.setenv("OIDC_CLIENT_ID", "citec-kb")
    monkeypatch.setenv("OIDC_AUDIENCE", "citec-kb")
    get_settings.cache_clear()

    tok = mint_local_jwt(
        sub="senior1",
        name="Senior One",
        roles=["viewer", "author", "senior"],
        expires_sec=600,
    )
    p = validate_bearer_jwt(tok)
    assert p is not None
    assert p.sub == "senior1"
    assert p.auth_via == "oidc"
    assert p.has_any("senior")

    p2 = resolve_principal(authorization=f"Bearer {tok}")
    assert p2.sub == "senior1"
    assert p2.has_any("senior")


def test_expired_jwt_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_JWT_SECRET", "unit-test-secret-32chars-minimum!!")
    monkeypatch.setenv("OIDC_ISSUER", "http://localhost/realms/test")
    monkeypatch.setenv("OIDC_CLIENT_ID", "citec-kb")
    get_settings.cache_clear()
    now = int(time.time())
    payload = {
        "sub": "x",
        "name": "x",
        "roles": ["admin"],
        "iat": now - 120,
        "exp": now - 60,
        "iss": "http://localhost/realms/test",
        "aud": "citec-kb",
    }
    tok = jwt.encode(payload, "unit-test-secret-32chars-minimum!!", algorithm="HS256")
    assert validate_bearer_jwt(tok) is None


def test_mint_requires_secret(monkeypatch):
    monkeypatch.delenv("OIDC_JWT_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        mint_local_jwt(sub="a", name="a", roles=["viewer"])
