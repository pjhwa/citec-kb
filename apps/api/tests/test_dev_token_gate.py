"""POST /v1/auth/dev/token must not be a role-escalation path."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings

BODY = {"sub": "m", "name": "M", "roles": ["admin"]}


@pytest.fixture
def client(monkeypatch):
    def _make(**env):
        monkeypatch.setenv("OIDC_JWT_SECRET", "unit-test-secret-0123456789abcdef")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        return TestClient(app)

    yield _make
    get_settings.cache_clear()


def test_blocked_in_prod_even_with_secret(client):
    c = client(APP_ENV="prod", AUTH_MODE="off")
    assert c.post("/v1/auth/dev/token", json=BODY).status_code == 403


def test_open_in_dev_when_auth_off(client):
    c = client(APP_ENV="dev", AUTH_MODE="off")
    assert c.post("/v1/auth/dev/token", json=BODY).status_code == 200


@pytest.mark.parametrize("mode", ["apikey", "oidc_stub", "oidc"])
def test_anonymous_cannot_mint_when_auth_enforced(client, mode):
    c = client(APP_ENV="dev", AUTH_MODE=mode)
    assert c.post("/v1/auth/dev/token", json=BODY).status_code == 401


def test_non_admin_cannot_mint_when_auth_enforced(client):
    c = client(
        APP_ENV="dev",
        AUTH_MODE="apikey",
        AUTH_TOKENS_JSON='{"tok-s": {"sub": "s", "roles": ["viewer", "author", "senior"]}}',
    )
    r = c.post("/v1/auth/dev/token", json=BODY, headers={"Authorization": "Bearer tok-s"})
    assert r.status_code == 403


def test_admin_can_mint_when_auth_enforced(client):
    c = client(
        APP_ENV="dev",
        AUTH_MODE="apikey",
        AUTH_TOKENS_JSON='{"tok-a": {"sub": "a", "roles": ["admin"]}}',
    )
    r = c.post("/v1/auth/dev/token", json=BODY, headers={"Authorization": "Bearer tok-a"})
    assert r.status_code == 200
