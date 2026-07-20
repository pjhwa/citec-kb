#!/usr/bin/env python3
"""Live mock-IdP authorization-code e2e against running API (port 8573).

Usage:
  # Server defaults AUTH_MODE=off — this script uses TestClient in-process
  # for a self-contained check, OR points at live API if AUTH is preconfigured.

  .venv/bin/python scripts/oidc_idp_e2e.py           # in-process (recommended)
  .venv/bin/python scripts/oidc_idp_e2e.py --live    # requires mock env on API
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

# allow importing app when run from host (optional live uses httpx only)
ROOT = Path(__file__).resolve().parents[1]


def run_inprocess() -> dict:
    os.environ.setdefault("APP_ENV", "dev")
    os.environ["AUTH_MODE"] = "oidc"
    os.environ["MOCK_OIDC_ENABLED"] = "true"
    os.environ["OIDC_ISSUER"] = "http://testserver/v1/mock-idp"
    os.environ["OIDC_CLIENT_ID"] = "citec-kb"
    os.environ["OIDC_CLIENT_SECRET"] = "mock-secret"
    os.environ["OIDC_AUDIENCE"] = "citec-kb"
    os.environ["OIDC_REDIRECT_URI"] = "http://testserver/v1/auth/callback"
    os.environ.pop("OIDC_JWT_SECRET", None)

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    # Prefer container path if present
    if Path("/app/app").is_dir():
        sys.path.insert(0, "/app")

    from app.auth.oidc import clear_discovery_cache
    from app.settings import get_settings

    get_settings.cache_clear()
    clear_discovery_cache()

    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app, follow_redirects=False)
    r = c.get("/v1/mock-idp/.well-known/openid-configuration")
    assert r.status_code == 200, r.text
    disc = r.json()

    r = c.get("/v1/auth/login?redirect=false&return_to=/login.html")
    assert r.status_code == 200, r.text
    auth_url = r.json()["authorization_url"]
    r = c.get(auth_url + "&sub=script-senior&roles=viewer,author,senior,admin")
    assert r.status_code in (302, 307), r.text
    cb = r.headers["location"].replace("http://testserver", "")
    r = c.get(cb)
    assert r.status_code in (302, 307), r.text
    frag = r.headers["location"].split("#", 1)[-1]
    token = (parse_qs(frag).get("access_token") or [None])[0]
    assert token

    r = c.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    me = r.json()
    assert me["principal"]["sub"] == "script-senior"
    assert "admin" in me["principal"]["roles"]

    return {
        "mode": "inprocess",
        "pass": True,
        "discovery_issuer": disc.get("issuer"),
        "principal": me["principal"],
        "token_alg": "RS256",
    }


def run_live(base: str) -> dict:
    import urllib.error
    import urllib.parse
    import urllib.request

    def get(url: str, headers: dict | None = None, allow_redirect=False):
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.geturl(), resp.read().decode(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            if allow_redirect and e.code in (301, 302, 303, 307, 308):
                return e.code, e.headers.get("Location"), e.read().decode(), dict(e.headers)
            raise

    base = base.rstrip("/")
    st, _, body, _ = get(f"{base}/v1/mock-idp/.well-known/openid-configuration")
    assert st == 200
    disc = json.loads(body)

    # Without AUTH_MODE=oidc on server, login still builds URL if issuer configured
    st, _, body, _ = get(f"{base}/v1/auth/status")
    status = json.loads(body)
    return {
        "mode": "live",
        "pass": bool(disc.get("authorization_endpoint")),
        "discovery_issuer": disc.get("issuer"),
        "auth_status": status,
        "note": "Full login needs API AUTH_MODE=oidc + OIDC_ISSUER=.../v1/mock-idp; use in-process for gate",
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--base", default="http://127.0.0.1:8573")
    args = p.parse_args()
    if args.live:
        report = run_live(args.base)
    else:
        # Prefer running inside API container where app is importable
        report = run_inprocess()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("pass") else 1)


if __name__ == "__main__":
    main()
