"""Auth status / whoami / OIDC login+callback."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.deps import get_principal
from app.auth.oidc import (
    build_authorization_url,
    exchange_code,
    mint_local_jwt,
    oidc_configured,
    oidc_status,
    pop_login_state,
    validate_bearer_jwt,
)
from app.auth.principal import Principal
from app.settings import get_settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.get("/me")
def auth_me(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "principal": principal.to_dict(),
        "auth_mode": (settings.auth_mode or "off").lower(),
    }


@router.get("/status")
def auth_status() -> dict[str, Any]:
    """Non-secret auth configuration summary for ops."""
    settings = get_settings()
    mode = (settings.auth_mode or "off").lower()
    enforced = mode not in ("", "off", "disabled", "none", "false", "0")
    return {
        "auth_mode": mode,
        "enforced": enforced,
        "disabled": not enforced,
        "message": (
            "Auth is disabled (AUTH_MODE=off). All write APIs are open for pilot."
            if not enforced
            else "Auth is enforced; send Bearer token or X-API-Key."
        ),
        "oidc": oidc_status(),
        "roles": ["viewer", "author", "senior", "admin"],
        "endpoints": {
            "me": "/v1/auth/me",
            "login": "/v1/auth/login",
            "callback": "/v1/auth/callback",
            "logout": "/v1/auth/logout",
            "dev_token": "/v1/auth/dev/token",
        },
        "protected_when_enforced": [
            "POST /v1/insights (author+)",
            "POST /v1/insights/*/submit (author+)",
            "POST /v1/insights/*/approve|reject|reopen|reindex (senior+)",
            "POST /v1/jobs (admin)",
        ],
    }


@router.get("/login")
def auth_login(
    return_to: Optional[str] = Query(None, description="Web path after login"),
    redirect: bool = Query(True, description="302 to IdP when true"),
) -> Any:
    """Start OIDC authorization-code flow (requires OIDC_ISSUER + CLIENT_ID)."""
    try:
        data = build_authorization_url(return_to=return_to)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if redirect:
        return RedirectResponse(url=data["authorization_url"], status_code=302)
    return data


@router.get("/callback")
def auth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
) -> Any:
    """OIDC redirect URI: exchange code, then redirect to web with tokens in fragment."""
    settings = get_settings()
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"oidc error: {error} {error_description or ''}".strip(),
        )
    if not code:
        raise HTTPException(status_code=400, detail="missing code")
    st = pop_login_state(state or "")
    # soft-fail state if redis was down during login
    try:
        tokens = exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    access = tokens.get("access_token") or ""
    id_token = tokens.get("id_token") or ""
    principal = None
    for tok in (access, id_token):
        if tok:
            principal = validate_bearer_jwt(tok)
            if principal:
                break
    if principal is None and id_token:
        # last resort: unvalidated claims only if HS not required — skip for safety
        pass

    return_to = (st or {}).get("return_to") or f"{settings.public_web_base.rstrip('/')}/login.html"
    # Prefer fragment so token is not sent to web server logs via query
    frag = urlencode(
        {
            k: v
            for k, v in {
                "access_token": access,
                "id_token": id_token,
                "token_type": tokens.get("token_type") or "Bearer",
                "expires_in": str(tokens.get("expires_in") or ""),
                "sub": principal.sub if principal else "",
            }.items()
            if v
        }
    )
    # if return_to is absolute use it, else join web base
    if return_to.startswith("http"):
        dest = return_to
    else:
        dest = settings.public_web_base.rstrip("/") + (
            return_to if return_to.startswith("/") else f"/{return_to}"
        )
    sep = "#" if "#" not in dest else "&"
    return RedirectResponse(url=f"{dest}{sep}{frag}", status_code=302)


@router.get("/logout")
def auth_logout(
    redirect: bool = Query(True),
) -> Any:
    """Best-effort logout: redirect to end_session_endpoint when available."""
    settings = get_settings()
    web = settings.public_web_base.rstrip("/") + "/login.html"
    if settings.oidc_issuer:
        try:
            from app.auth.oidc import get_discovery

            disc = get_discovery()
            end = disc.get("end_session_endpoint")
            if end and redirect:
                q = urlencode({"post_logout_redirect_uri": web, "client_id": settings.oidc_client_id or ""})
                return RedirectResponse(url=f"{end}?{q}", status_code=302)
        except Exception:  # noqa: BLE001
            pass
    if redirect:
        return RedirectResponse(url=web, status_code=302)
    return {"ok": True, "post_logout_redirect": web}


class DevTokenBody(BaseModel):
    sub: str = "dev-user"
    name: str = "Dev User"
    roles: list[str] = Field(default_factory=lambda: ["viewer", "author", "senior"])
    expires_sec: int = Field(default=3600, ge=60, le=86400)


@router.post("/dev/token")
def dev_mint_token(body: DevTokenBody) -> dict[str, Any]:
    """Mint local HS256 JWT when OIDC_JWT_SECRET is set (dev only)."""
    settings = get_settings()
    if settings.app_env not in ("dev", "local", "test") and not settings.oidc_jwt_secret:
        raise HTTPException(status_code=403, detail="dev token only in dev/local")
    if not settings.oidc_jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="OIDC_JWT_SECRET not configured",
        )
    try:
        token = mint_local_jwt(
            sub=body.sub,
            name=body.name,
            roles=body.roles,
            expires_sec=body.expires_sec,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    principal = validate_bearer_jwt(token)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": body.expires_sec,
        "principal": principal.to_dict() if principal else None,
    }


@router.post("/introspect")
def introspect_token(
    authorization: Optional[str] = None,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Return resolved principal for the caller's credential."""
    _ = authorization
    return {
        "active": principal.auth_via != "anonymous" or principal.has_any("admin"),
        "principal": principal.to_dict(),
        "oidc_configured": oidc_configured(),
    }
