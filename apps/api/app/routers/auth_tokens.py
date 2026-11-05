"""Admin API for API-token issuing (hashed store; see app.auth.admin_tokens)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import admin_tokens
from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.settings import get_settings

router = APIRouter(prefix="/v1/auth/tokens", tags=["auth"])
logger = logging.getLogger("citec.auth.tokens")


class TokenIssue(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["author"])
    allow_privileged: bool = False
    note: Optional[str] = Field(default=None, max_length=300)
    rotate: bool = False


def _enforced() -> bool:
    mode = (getattr(get_settings(), "auth_mode", None) or "off").lower().strip()
    return mode not in ("", "off", "disabled", "none", "false", "0")


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except admin_tokens.TokenError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e


@router.get("")
def list_tokens(principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    return {"enforced": _enforced(), "tokens": _wrap(admin_tokens.list_tokens)}


@router.post("")
def issue_token(
    body: TokenIssue, response: Response, principal: Principal = Depends(require_roles("admin"))
) -> dict[str, Any]:
    if not _enforced():
        # With auth off this endpoint is open to anyone who can reach it: minting credentials
        # there would let them pre-create tokens (even admin ones) for the day auth is enabled.
        raise HTTPException(
            status_code=409,
            detail="AUTH_MODE=off 에서는 발급할 수 없습니다. 첫 admin 토큰은 호스트에서 "
            "scripts/issue_system_token.py --allow-privileged로 발급하세요(docs/AUTH_ENABLEMENT.md).",
        )
    out = _wrap(admin_tokens.issue, body.name, body.roles, body.allow_privileged, body.note,
                body.rotate, principal.name)
    logger.info("token issued name=%s roles=%s by=%s rotated=%s fp=%s", out["name"], out["roles"],
                principal.name, out["rotated"], out["fingerprint"])  # never the token itself
    response.headers["Cache-Control"] = "no-store"
    return out


@router.delete("/{name}")
def revoke_token(name: str, principal: Principal = Depends(require_roles("admin"))) -> dict[str, Any]:
    if not _enforced():
        raise HTTPException(status_code=409, detail="AUTH_MODE=off 에서는 회수할 수 없습니다.")
    if name == principal.name:
        raise HTTPException(status_code=409, detail="현재 사용 중인 자기 토큰은 회수할 수 없습니다(잠김 방지).")
    n = _wrap(admin_tokens.revoke, name)
    logger.info("token revoked name=%s by=%s count=%d", name, principal.name, n)
    return {"revoked": name, "count": n}
