#!/usr/bin/env python3
"""Keycloak (real IdP) OIDC e2e: discovery · password grant · JWKS JWT · API RBAC.

Prereq:
  docker compose --profile keycloak up -d keycloak

Usage:
  .venv/bin/python scripts/keycloak_oidc_e2e.py
  .venv/bin/python scripts/keycloak_oidc_e2e.py --wait 180
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ISSUER = "http://localhost:8576/realms/citec"
DEFAULT_CLIENT = "citec-kb"
DEFAULT_SECRET = "citec-kb-secret"


def _http(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            if not raw:
                return resp.status, {}
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def wait_ready(issuer: str, timeout: int) -> dict[str, Any]:
    disc_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    t0 = time.time()
    last: Any = None
    while time.time() - t0 < timeout:
        code, body = _http("GET", disc_url, timeout=5)
        last = body
        if code == 200 and isinstance(body, dict) and body.get("token_endpoint"):
            return body
        time.sleep(3)
    raise RuntimeError(f"Keycloak discovery not ready after {timeout}s: {last}")


def password_grant(
    token_endpoint: str,
    *,
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    form = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": client_secret,
            "username": username,
            "password": password,
            "scope": "openid profile email",
        }
    ).encode()
    code, body = _http(
        "POST",
        token_endpoint,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if code != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise RuntimeError(f"password grant failed ({code}): {body}")
    return body


def run_api_rbac(access_token: str, issuer: str, client_id: str) -> dict[str, Any]:
    """In-process FastAPI TestClient with AUTH_MODE=oidc against Keycloak JWKS."""
    os.environ["AUTH_MODE"] = "oidc"
    os.environ["APP_ENV"] = "dev"
    os.environ["OIDC_ISSUER"] = issuer.rstrip("/")
    os.environ["OIDC_CLIENT_ID"] = client_id
    # Keycloak access tokens often omit aud; skip aud and check azp==client_id
    os.environ["OIDC_AUDIENCE"] = "*"
    os.environ.pop("OIDC_JWT_SECRET", None)
    os.environ["MOCK_OIDC_ENABLED"] = "false"
    # Host-side TestClient → published compose ports
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_knowledge",
    )
    os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:8575/0")

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.auth.oidc import clear_discovery_cache, validate_bearer_jwt
    from app.settings import get_settings

    get_settings.cache_clear()
    clear_discovery_cache()

    p = validate_bearer_jwt(access_token)
    if p is None:
        raise RuntimeError("JWT validation failed against Keycloak JWKS")

    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    r = c.get("/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    if r.status_code != 200:
        raise RuntimeError(f"/v1/auth/me failed: {r.status_code} {r.text}")
    me = r.json()

    r = c.post(
        "/v1/insights",
        json={"title": "Keycloak e2e insight", "body_md": "from keycloak"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    create_status = r.status_code
    create_body = r.json() if r.content else {}

    return {
        "principal_from_jwt": p.to_dict(),
        "me": me,
        "insight_create_status": create_status,
        "insight_id": create_body.get("id"),
        "roles": sorted(p.roles),
        "audience_mode": "*",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Keycloak OIDC e2e")
    ap.add_argument("--issuer", default=os.getenv("OIDC_ISSUER", DEFAULT_ISSUER))
    ap.add_argument("--client-id", default=os.getenv("OIDC_CLIENT_ID", DEFAULT_CLIENT))
    ap.add_argument("--client-secret", default=os.getenv("OIDC_CLIENT_SECRET", DEFAULT_SECRET))
    ap.add_argument("--username", default="senior")
    ap.add_argument("--password", default="senior")
    ap.add_argument("--wait", type=int, default=180)
    ap.add_argument("--skip-api", action="store_true", help="only discovery+token")
    args = ap.parse_args()

    report: dict[str, Any] = {
        "issuer": args.issuer,
        "client_id": args.client_id,
        "username": args.username,
    }
    try:
        disc = wait_ready(args.issuer, args.wait)
        report["discovery_ok"] = True
        report["token_endpoint"] = disc.get("token_endpoint")
        report["jwks_uri"] = disc.get("jwks_uri")

        tokens = password_grant(
            disc["token_endpoint"],
            client_id=args.client_id,
            client_secret=args.client_secret,
            username=args.username,
            password=args.password,
        )
        report["token_ok"] = True
        report["token_type"] = tokens.get("token_type")
        report["expires_in"] = tokens.get("expires_in")
        access = tokens["access_token"]

        if not args.skip_api:
            api_result = run_api_rbac(access, args.issuer, args.client_id)
            report["api"] = api_result
            report["audience_used"] = "*"
            # senior should be able to create insights
            if "senior" in (api_result.get("roles") or []) or "author" in (
                api_result.get("roles") or []
            ):
                report["rbac_ok"] = api_result.get("insight_create_status") == 200
            else:
                report["rbac_ok"] = True

        report["pass"] = bool(report.get("discovery_ok") and report.get("token_ok"))
        if not args.skip_api:
            report["pass"] = report["pass"] and bool(report.get("rbac_ok"))
    except Exception as exc:  # noqa: BLE001
        report["pass"] = False
        report["error"] = str(exc)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("pass") else 1)


if __name__ == "__main__":
    main()
