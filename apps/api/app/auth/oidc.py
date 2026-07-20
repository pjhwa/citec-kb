"""OIDC discovery, JWT validation (JWKS RS* / local HS256), auth-code helpers."""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import jwt
import redis
from jwt import PyJWKClient

from app.auth.principal import ALL_ROLES, ROLE_VIEWER, Principal
from app.settings import get_settings

logger = logging.getLogger("citec.auth.oidc")

_STATE_KEY = "citec:auth:oidc:state:{state}"
_DISCOVERY_CACHE: dict[str, Any] = {"at": 0.0, "issuer": None, "doc": None}
_DISCOVERY_TTL = 3600.0


def oidc_configured() -> bool:
    s = get_settings()
    # Local JWT path OR full issuer+client
    if s.oidc_jwt_secret:
        return True
    return bool(s.oidc_issuer and s.oidc_client_id)


def oidc_status() -> dict[str, Any]:
    s = get_settings()
    disc = None
    err = None
    if s.oidc_issuer:
        try:
            disc = get_discovery(force=False)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
    return {
        "issuer": s.oidc_issuer,
        "client_id": s.oidc_client_id,
        "audience": s.oidc_audience or s.oidc_client_id,
        "redirect_uri": s.oidc_redirect_uri,
        "scopes": s.oidc_scopes,
        "jwt_secret_configured": bool(s.oidc_jwt_secret),
        "client_secret_configured": bool(s.oidc_client_secret),
        "configured": oidc_configured(),
        "discovery_ok": bool(disc and not err),
        "discovery_error": err,
        "authorization_endpoint": (disc or {}).get("authorization_endpoint"),
        "token_endpoint": (disc or {}).get("token_endpoint"),
        "jwks_uri": (disc or {}).get("jwks_uri"),
        "role_claim": s.oidc_role_claim,
        "note": (
            "AUTH_MODE=oidc accepts validated JWT (+ API keys). "
            "Set OIDC_ISSUER+CLIENT_ID for IdP, or OIDC_JWT_SECRET for local HS256."
        ),
    }


def get_discovery(*, force: bool = False) -> dict[str, Any]:
    s = get_settings()
    issuer = (s.oidc_issuer or "").rstrip("/")
    if not issuer:
        raise RuntimeError("OIDC_ISSUER not set")
    now = time.time()
    if (
        not force
        and _DISCOVERY_CACHE["doc"]
        and _DISCOVERY_CACHE["issuer"] == issuer
        and now - float(_DISCOVERY_CACHE["at"]) < _DISCOVERY_TTL
    ):
        return dict(_DISCOVERY_CACHE["doc"])  # type: ignore[arg-type]

    url = f"{issuer}/.well-known/openid-configuration"
    with httpx.Client(timeout=8.0) as client:
        r = client.get(url)
        r.raise_for_status()
        doc = r.json()
    _DISCOVERY_CACHE["at"] = now
    _DISCOVERY_CACHE["issuer"] = issuer
    _DISCOVERY_CACHE["doc"] = doc
    return dict(doc)


def _roles_from_claims(claims: dict[str, Any]) -> frozenset[str]:
    s = get_settings()
    claim = (s.oidc_role_claim or "roles").strip()
    raw: list[Any] = []

    # dotted path e.g. realm_access.roles
    if "." in claim:
        cur: Any = claims
        for part in claim.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if isinstance(cur, list):
            raw = cur
        elif isinstance(cur, str):
            raw = [cur]
    else:
        v = claims.get(claim)
        if isinstance(v, list):
            raw = v
        elif isinstance(v, str):
            raw = [v]
        # Keycloak common fallbacks
        if not raw and isinstance(claims.get("realm_access"), dict):
            raw = list(claims["realm_access"].get("roles") or [])
        if not raw and isinstance(claims.get("groups"), list):
            raw = list(claims["groups"])

    roles = {str(x).lower().lstrip("/") for x in raw if x}
    # map common IdP role names
    mapped: set[str] = set()
    for r in roles:
        if r in ALL_ROLES:
            mapped.add(r)
        elif r in ("kb-admin", "citec-admin", "administrator"):
            mapped.add("admin")
        elif r in ("kb-senior", "citec-senior", "reviewer"):
            mapped.add("senior")
        elif r in ("kb-author", "citec-author", "editor", "writer"):
            mapped.add("author")
        elif r in ("kb-viewer", "citec-viewer", "user", "default-roles-citec"):
            mapped.add("viewer")
    if not mapped:
        mapped.add(ROLE_VIEWER)
    # hierarchy soft-expand: admin includes lower? No — explicit grants only;
    # ops can assign multiple roles in token.
    return frozenset(mapped)


def principal_from_claims(claims: dict[str, Any], *, auth_via: str = "oidc") -> Principal:
    sub = str(claims.get("sub") or claims.get("preferred_username") or "unknown")
    name = str(
        claims.get("name")
        or claims.get("preferred_username")
        or claims.get("email")
        or sub
    )
    return Principal(
        sub=sub,
        name=name,
        roles=_roles_from_claims(claims),
        auth_via=auth_via,
    )


def _looks_like_jwt(token: str) -> bool:
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def validate_bearer_jwt(token: str) -> Optional[Principal]:
    """Validate access/id token. Returns Principal or None if not a valid OIDC JWT."""
    if not token or not _looks_like_jwt(token):
        return None
    s = get_settings()

    # 1) Local HS256 (dev / automated tests)
    if s.oidc_jwt_secret:
        try:
            opts = {"require": ["exp", "sub"]}
            kwargs: dict[str, Any] = {
                "algorithms": ["HS256"],
                "options": opts,
            }
            if s.oidc_issuer:
                kwargs["issuer"] = s.oidc_issuer.rstrip("/")
            aud = s.oidc_audience or s.oidc_client_id
            if aud:
                kwargs["audience"] = aud
            claims = jwt.decode(token, s.oidc_jwt_secret, **kwargs)
            return principal_from_claims(claims, auth_via="oidc")
        except jwt.PyJWTError as exc:
            logger.debug("HS256 JWT reject: %s", exc)
            # fall through to JWKS if issuer configured

    # 2) RS* via JWKS discovery
    if not s.oidc_issuer:
        return None
    try:
        disc = get_discovery()
        jwks_uri = disc.get("jwks_uri")
        if not jwks_uri:
            return None
        jwks_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        kwargs = {
            "algorithms": ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            "issuer": s.oidc_issuer.rstrip("/"),
            "options": {"require": ["exp", "sub"]},
        }
        aud = s.oidc_audience or s.oidc_client_id
        if aud:
            kwargs["audience"] = aud
        claims = jwt.decode(token, signing_key.key, **kwargs)
        return principal_from_claims(claims, auth_via="oidc")
    except Exception as exc:  # noqa: BLE001
        logger.debug("JWKS JWT reject: %s", exc)
        return None


def mint_local_jwt(
    *,
    sub: str,
    name: str,
    roles: list[str],
    expires_sec: int = 3600,
) -> str:
    """Mint HS256 JWT for local tests (requires OIDC_JWT_SECRET)."""
    s = get_settings()
    if not s.oidc_jwt_secret:
        raise RuntimeError("OIDC_JWT_SECRET required to mint local JWT")
    now = int(time.time())
    payload = {
        "sub": sub,
        "name": name,
        "roles": roles,
        "iat": now,
        "exp": now + expires_sec,
    }
    if s.oidc_issuer:
        payload["iss"] = s.oidc_issuer.rstrip("/")
    aud = s.oidc_audience or s.oidc_client_id
    if aud:
        payload["aud"] = aud
    return jwt.encode(payload, s.oidc_jwt_secret, algorithm="HS256")


def _redis() -> redis.Redis:
    s = get_settings()
    return redis.from_url(s.redis_url, decode_responses=True, socket_connect_timeout=3)


def create_login_state(*, return_to: Optional[str] = None) -> str:
    state = secrets.token_urlsafe(24)
    payload = json.dumps({"return_to": return_to or "", "ts": int(time.time())})
    try:
        r = _redis()
        r.setex(_STATE_KEY.format(state=state), 600, payload)
    except Exception:  # noqa: BLE001
        logger.exception("redis state store failed; state will not be verified")
    return state


def pop_login_state(state: str) -> Optional[dict[str, Any]]:
    if not state:
        return None
    try:
        r = _redis()
        key = _STATE_KEY.format(state=state)
        raw = r.get(key)
        if raw:
            r.delete(key)
            return json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.exception("redis state load failed")
    return None


def build_authorization_url(*, return_to: Optional[str] = None) -> dict[str, Any]:
    s = get_settings()
    if not (s.oidc_issuer and s.oidc_client_id):
        raise RuntimeError("OIDC_ISSUER and OIDC_CLIENT_ID required for login redirect")
    disc = get_discovery()
    auth_ep = disc.get("authorization_endpoint")
    if not auth_ep:
        raise RuntimeError("discovery missing authorization_endpoint")
    state = create_login_state(return_to=return_to)
    params = {
        "client_id": s.oidc_client_id,
        "response_type": "code",
        "scope": s.oidc_scopes or "openid profile email",
        "redirect_uri": s.oidc_redirect_uri,
        "state": state,
    }
    url = f"{auth_ep}?{urlencode(params)}"
    return {
        "authorization_url": url,
        "state": state,
        "redirect_uri": s.oidc_redirect_uri,
    }


def exchange_code(code: str) -> dict[str, Any]:
    """Authorization-code → tokens at token_endpoint."""
    s = get_settings()
    if not s.oidc_client_id:
        raise RuntimeError("OIDC_CLIENT_ID required")
    disc = get_discovery()
    token_ep = disc.get("token_endpoint")
    if not token_ep:
        raise RuntimeError("discovery missing token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": s.oidc_redirect_uri,
        "client_id": s.oidc_client_id,
    }
    if s.oidc_client_secret:
        data["client_secret"] = s.oidc_client_secret
    with httpx.Client(timeout=15.0) as client:
        r = client.post(token_ep, data=data)
        if r.status_code >= 400:
            raise RuntimeError(f"token exchange failed: {r.status_code} {r.text[:300]}")
        tokens = r.json()
    return tokens
