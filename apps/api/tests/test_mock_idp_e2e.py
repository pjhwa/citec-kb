"""Full OIDC authorization-code e2e against in-process mock IdP (RS256 JWKS)."""

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.auth.oidc import clear_discovery_cache
from app.settings import get_settings


def setup_function():
    get_settings.cache_clear()
    clear_discovery_cache()


def teardown_function():
    get_settings.cache_clear()
    clear_discovery_cache()


def _configure_mock_oidc(monkeypatch, base: str = "http://testserver"):
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("MOCK_OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER", f"{base}/v1/mock-idp")
    monkeypatch.setenv("OIDC_CLIENT_ID", "citec-kb")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "mock-secret")
    monkeypatch.setenv("OIDC_AUDIENCE", "citec-kb")
    monkeypatch.setenv("OIDC_REDIRECT_URI", f"{base}/v1/auth/callback")
    monkeypatch.setenv("PUBLIC_WEB_BASE", "http://localhost:8572")
    monkeypatch.delenv("OIDC_JWT_SECRET", raising=False)
    get_settings.cache_clear()
    clear_discovery_cache()


def test_mock_discovery_and_jwks(monkeypatch):
    _configure_mock_oidc(monkeypatch)
    from app.main import app

    c = TestClient(app)
    r = c.get("/v1/mock-idp/.well-known/openid-configuration")
    assert r.status_code == 200
    disc = r.json()
    assert disc["issuer"].endswith("/v1/mock-idp")
    assert "authorize" in disc["authorization_endpoint"]
    r = c.get("/v1/mock-idp/jwks")
    assert r.status_code == 200
    assert r.json()["keys"][0]["kty"] == "RSA"


def test_full_login_callback_rs256_flow(monkeypatch):
    _configure_mock_oidc(monkeypatch)
    from app.main import app

    c = TestClient(app, follow_redirects=False)

    # 1) start login (JSON)
    r = c.get("/v1/auth/login?redirect=false&return_to=/login.html")
    assert r.status_code == 200, r.text
    auth_url = r.json()["authorization_url"]
    assert "/v1/mock-idp/authorize" in auth_url

    # 2) authorize → redirect with code to callback
    r = c.get(auth_url + "&sub=e2e-senior&roles=viewer,author,senior")
    assert r.status_code in (302, 307), r.text
    loc = r.headers["location"]
    assert "code=" in loc
    assert "/v1/auth/callback" in loc

    # 3) callback exchanges code → redirect to web with fragment token
    path = loc.replace("http://testserver", "")
    r = c.get(path)
    assert r.status_code in (302, 307), r.text
    final = r.headers["location"]
    assert "access_token=" in final or "id_token=" in final
    # extract token from fragment
    frag = final.split("#", 1)[-1]
    qs = parse_qs(frag)
    token = (qs.get("access_token") or qs.get("id_token") or [None])[0]
    assert token and token.count(".") == 2

    # 4) JWT works for me + write
    r = c.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["principal"]["auth_via"] == "oidc"
    assert body["principal"]["sub"] == "e2e-senior"
    assert "senior" in body["principal"]["roles"]

    r = c.post(
        "/v1/insights",
        json={"title": "mock-idp-e2e", "body_md": "from mock idp"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text


def test_login_redirect_mode(monkeypatch):
    _configure_mock_oidc(monkeypatch)
    from app.main import app

    c = TestClient(app, follow_redirects=False)
    r = c.get("/v1/auth/login?redirect=true")
    assert r.status_code in (302, 307)
    assert "/v1/mock-idp/authorize" in r.headers["location"]
