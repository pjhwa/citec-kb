"""FastAPI dependencies for auth."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.auth.principal import Principal, resolve_principal
from app.settings import get_settings


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    principal = resolve_principal(authorization=authorization, api_key=x_api_key)
    request.state.principal = principal
    return principal


def require_roles(*roles: str):
    """Dependency factory: 401 if auth enforced + anonymous; 403 if role miss."""

    needed = frozenset(roles)

    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> Principal:
        principal = get_principal(
            request, authorization=authorization, x_api_key=x_api_key
        )
        settings = get_settings()
        mode = (getattr(settings, "auth_mode", None) or "off").lower().strip()
        # Explicit disable aliases
        enforced = mode not in ("", "off", "disabled", "none", "false", "0")

        if not enforced:
            # Auth disabled: never 401/403 on role gates
            return principal

        if principal.auth_via == "anonymous":
            raise HTTPException(
                status_code=401,
                detail="authentication required (Bearer token or X-API-Key)",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not principal.has_any(*needed):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"requires one of roles: {sorted(needed)}; "
                    f"have {sorted(principal.roles)}"
                ),
            )
        return principal

    return dependency
