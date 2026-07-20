"""Dev/local mock OIDC IdP (authorization-code + RS256 JWKS).

Issuer base: ``{public_api}/v1/mock-idp``
Only active when MOCK_OIDC_ENABLED is true (default on for APP_ENV=dev).
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode

import jwt
import redis
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.settings import get_settings

logger = logging.getLogger("citec.auth.mock_idp")

# Deterministic RSA key for JWKS e2e (dev only — not a production secret).
_PRIVATE_PEM = b"""-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCR+9zFEK5PTHXh
UUrm4zyNEDPNJSmzBWKxefczbfnrVr5TUSpTuYKICsIdDDEw0H2X61iv5mkwccxI
zyyFrPewUdiRoz2iGoKa55taF/wqBOyBPfbyMmNZ8WyjZBoKPgF4qYqtLnzFd3gd
ST6LBplonk0PCKuvePpBLotYLPsHB2gQiXA886HuJAyux/vzACISBUyA5L/F2T6q
a4HU2jWD9Xq02uhu2vf++cFhXzMND9w9pLfKMchdHNJS1ZmCpj7gicK0wM9xe6yH
GJLtzU7Px/cNzpmWaIJE3opOikGiIxpqYxZ5O9gmFL34528foKWpl7QaDu/Hk1sY
W/P/CsCbAgMBAAECggEABUAOp5O7ASUZ1DmtPPKNOfGMO2OLxWF7NDDTwCjDZUBg
ZfS0VgCE/kmMw6itmDjXW4BeVy0tOU3OcAvraP9YhSHcbRh23f6gFdwgjTPxoL0+
mENXP0yqoBB7vMCb4yRpvyIx15qlaCCs1DjPJAbfu5B96v1/1za9oVyALHKpsI34
uy92L9xDf+5Ri0/oxkg+3Ch51UVgBcagiMVjNvA8T+3BnQebd5HVeB3NnMPJ0pln
E8j+vtsLwP+m/4SOvQJEsHU9mebQZzAoe61dOVj2ObvXPAWLnRwFqFZpRc4W45Y+
w1GRpZRzMzVtRd+AP80sK5KM3B6oNUMdugWamr95QQKBgQDDXvzZMjlX9TmPbgQx
5cu8nkAMMHWRyfnLXdcUlsM+F23tNUvqr2qVImMDJ8ffNWyJA7akb/VJ5D6fE3Ak
hCcuMAxSFFn7yhXVRCRRTh0Z1gxMvo7AUpSoboP1tPBGSCGrJueYHvMEliOC0S7M
0dSIGzf1fypf3JY9V0yC+rikoQKBgQC/SV5johWzoZx0K21uy9FTfL+2gyI//0ck
S2S/UXuDu88tndZnhG3+ExUymJVq03x6FtcZxt8hK0ZAXw0EvFm8N/AFFaiBG6k3
ZtAZXBxudvTMiZkq+qmEbcCcLe1SKxIDrQSRBJQebvbva2lVX5BhrxjGBswyhRMq
cmOMWVsfuwKBgQCdjxxROVzfn6fFEU+WwiE1w1YZvncClSW7qblMJG3exFxlweaw
pLlK/ollQQ7C5z3ZncINCTGDXuxVtAJroJxMdnlpNHqBQi+rZ6H2ZA26CVKwDbno
RnEXCNGpNTvVIlTsx5pcpxELsN2AoZyhl9NT1MejV+PfnXEYlS/iLbr9IQKBgH5N
qpZs6pluZ4jJN/vFdpUCtO+FDLNnEoljgsVUvxKPis/a/Tvi1GHEJeX/nAEqXXGb
7TGm/6O+GCfe2xC6cSH3aXNiBp4hLo1XRKbKDDfgMelwHYOkeRPpCBnXtXDg4Yct
0esTM94YdNJHgQiPDh2B6QCwcloVRj9rwlFkmueLAoGBAKkM7zKC4AQyQ15SKdzi
7Ce6txziAaOnHIIBrwHOmY66nX9OizV74NClwmY5zGx/mtlKTNWN4c9hcG4Yu0vA
wn2ToZYtsZRFd+/ceaVoUEYgA8k8fAR35pmsgtOZ1zQB+7hdlabcr+DvzpGd4klV
KKb8VZTBDGfvK7fTE0VD7OJ1
-----END PRIVATE KEY-----
"""

_KID = "citec-mock-idp-1"
_CODE_KEY = "citec:mock-idp:code:{code}"
_MEMORY_CODES: dict[str, str] = {}  # fallback if redis down

router = APIRouter(prefix="/v1/mock-idp", tags=["mock-idp"])

_private_key = serialization.load_pem_private_key(_PRIVATE_PEM, password=None)
_public_key = _private_key.public_key()


def mock_idp_enabled() -> bool:
    s = get_settings()
    flag = getattr(s, "mock_oidc_enabled", None)
    if flag is None:
        return (s.app_env or "dev").lower() in ("dev", "local", "test")
    return bool(flag)


def is_mock_issuer(issuer: Optional[str]) -> bool:
    if not issuer:
        return False
    u = issuer.rstrip("/")
    return u.endswith("/v1/mock-idp") or "/mock-idp" in u


def default_mock_issuer() -> str:
    """Public issuer URL used in tokens and discovery."""
    s = get_settings()
    base = (getattr(s, "public_api_base", None) or "http://localhost:8573").rstrip("/")
    return f"{base}/v1/mock-idp"


def mock_discovery_document(issuer: Optional[str] = None) -> dict[str, Any]:
    iss = (issuer or default_mock_issuer()).rstrip("/")
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/authorize",
        "token_endpoint": f"{iss}/token",
        "jwks_uri": f"{iss}/jwks",
        "end_session_endpoint": f"{iss}/logout",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
        "scopes_supported": ["openid", "profile", "email"],
        "claims_supported": ["sub", "name", "email", "roles"],
    }


def _b64url_uint(val: int) -> str:
    length = (val.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(val.to_bytes(length, "big")).rstrip(b"=").decode()


def jwks_document() -> dict[str, Any]:
    numbers = _public_key.public_numbers()  # type: ignore[union-attr]
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": _KID,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


def public_key_pem() -> bytes:
    return _public_key.public_bytes(  # type: ignore[union-attr]
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _redis() -> Optional[redis.Redis]:
    try:
        s = get_settings()
        r = redis.from_url(s.redis_url, decode_responses=True, socket_connect_timeout=2)
        r.ping()
        return r
    except Exception:  # noqa: BLE001
        return None


def _store_code(code: str, payload: dict[str, Any], ttl: int = 120) -> None:
    raw = json.dumps(payload)
    r = _redis()
    if r is not None:
        r.setex(_CODE_KEY.format(code=code), ttl, raw)
        return
    _MEMORY_CODES[code] = raw


def _pop_code(code: str) -> Optional[dict[str, Any]]:
    r = _redis()
    if r is not None:
        key = _CODE_KEY.format(code=code)
        raw = r.get(key)
        if raw:
            r.delete(key)
            return json.loads(raw)
        return None
    raw = _MEMORY_CODES.pop(code, None)
    return json.loads(raw) if raw else None


def mint_rs256_token(
    *,
    issuer: str,
    audience: str,
    sub: str,
    name: str,
    roles: list[str],
    expires_sec: int = 3600,
) -> str:
    now = int(time.time())
    payload = {
        "iss": issuer.rstrip("/"),
        "aud": audience,
        "sub": sub,
        "name": name,
        "preferred_username": sub,
        "roles": roles,
        "iat": now,
        "exp": now + expires_sec,
    }
    return jwt.encode(
        payload,
        _private_key,
        algorithm="RS256",
        headers={"kid": _KID},
    )


def exchange_mock_code(
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    client_secret: Optional[str] = None,
) -> dict[str, Any]:
    data = _pop_code(code)
    if not data:
        raise ValueError("invalid or expired code")
    if data.get("client_id") != client_id:
        raise ValueError("client_id mismatch")
    if data.get("redirect_uri") != redirect_uri:
        raise ValueError("redirect_uri mismatch")
    expected_secret = data.get("client_secret") or "mock-secret"
    # accept missing secret in pure public clients
    if client_secret is not None and client_secret != "" and client_secret != expected_secret:
        # still allow if configured secret matches settings
        s = get_settings()
        if s.oidc_client_secret and client_secret == s.oidc_client_secret:
            pass
        elif client_secret != "mock-secret":
            raise ValueError("invalid client_secret")

    issuer = data.get("issuer") or default_mock_issuer()
    access = mint_rs256_token(
        issuer=issuer,
        audience=client_id,
        sub=str(data.get("sub") or "mock-user"),
        name=str(data.get("name") or data.get("sub") or "Mock User"),
        roles=list(data.get("roles") or ["viewer", "author", "senior"]),
    )
    return {
        "access_token": access,
        "id_token": access,
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "openid profile email",
    }


def _require_enabled() -> None:
    if not mock_idp_enabled():
        raise HTTPException(status_code=404, detail="mock IdP disabled")


@router.get("/.well-known/openid-configuration")
def discovery(request: Request) -> dict[str, Any]:
    _require_enabled()
    # Prefer request-derived issuer so TestClient/host match
    base = str(request.base_url).rstrip("/")
    issuer = f"{base}/v1/mock-idp"
    return mock_discovery_document(issuer)


@router.get("/jwks")
def jwks() -> dict[str, Any]:
    _require_enabled()
    return jwks_document()


@router.get("/authorize")
def authorize(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query("code"),
    state: str = Query(""),
    scope: str = Query("openid"),
    sub: str = Query("mock-senior"),
    name: str = Query("Mock Senior"),
    roles: str = Query("viewer,author,senior"),
) -> RedirectResponse:
    _require_enabled()
    if response_type != "code":
        raise HTTPException(status_code=400, detail="only response_type=code supported")
    base = str(request.base_url).rstrip("/")
    issuer = f"{base}/v1/mock-idp"
    code = secrets.token_urlsafe(24)
    _store_code(
        code,
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "client_secret": "mock-secret",
            "sub": sub,
            "name": name,
            "roles": [r.strip() for r in roles.split(",") if r.strip()],
            "scope": scope,
            "issuer": issuer,
            "ts": int(time.time()),
        },
    )
    sep = "&" if "?" in redirect_uri else "?"
    loc = f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(url=loc, status_code=302)


@router.post("/token")
async def token(request: Request) -> JSONResponse:
    """OAuth token endpoint (application/x-www-form-urlencoded or JSON)."""
    _require_enabled()
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        raw = await request.json()
        data = {str(k): str(v) if v is not None else "" for k, v in dict(raw).items()}
    else:
        body = (await request.body()).decode("utf-8", errors="ignore")
        parsed = parse_qs(body, keep_blank_values=True)
        data = {k: (v[0] if v else "") for k, v in parsed.items()}

    grant_type = data.get("grant_type") or ""
    code = data.get("code") or ""
    redirect_uri = data.get("redirect_uri") or ""
    client_id = data.get("client_id") or ""
    client_secret = data.get("client_secret") or None

    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="unsupported grant_type")
    if not code or not client_id or not redirect_uri:
        raise HTTPException(status_code=400, detail="code, client_id, redirect_uri required")
    try:
        tokens = exchange_mock_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret=client_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(tokens)


@router.get("/logout")
def logout(post_logout_redirect_uri: Optional[str] = None) -> RedirectResponse:
    _require_enabled()
    dest = post_logout_redirect_uri or "http://localhost:8572/login.html"
    return RedirectResponse(url=dest, status_code=302)
