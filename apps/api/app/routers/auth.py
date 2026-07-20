"""Auth status / whoami (SSO scaffolding)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import get_principal
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
    return {
        "auth_mode": mode,
        "enforced": mode not in ("", "off", "disabled", "none"),
        "oidc": {
            "issuer": settings.oidc_issuer or None,
            "client_id": settings.oidc_client_id or None,
            "audience": settings.oidc_audience or None,
            "configured": bool(settings.oidc_issuer and settings.oidc_client_id),
            "note": "Full OIDC redirect/JWT validation planned; use apikey or stub: tokens for now",
        },
        "roles": ["viewer", "author", "senior", "admin"],
        "protected_when_enforced": [
            "POST /v1/insights (author+)",
            "POST /v1/insights/*/submit (author+)",
            "POST /v1/insights/*/approve|reject|reopen|reindex (senior+)",
            "POST /v1/jobs (admin)",
        ],
    }
