"""Principal model + token table (API keys / OIDC stub claims)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.settings import get_settings

logger = logging.getLogger("citec.auth")

# role hierarchy helpers
ROLE_VIEWER = "viewer"
ROLE_AUTHOR = "author"
ROLE_SENIOR = "senior"
ROLE_ADMIN = "admin"
ALL_ROLES = frozenset({ROLE_VIEWER, ROLE_AUTHOR, ROLE_SENIOR, ROLE_ADMIN})


@dataclass(frozen=True)
class Principal:
    sub: str
    name: str
    roles: frozenset[str] = field(default_factory=frozenset)
    auth_via: str = "anonymous"  # anonymous | apikey | oidc_stub

    def has_any(self, *needed: str) -> bool:
        return bool(self.roles.intersection(needed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "name": self.name,
            "roles": sorted(self.roles),
            "auth_via": self.auth_via,
        }


ANON_OPEN = Principal(
    sub="anonymous",
    name="anonymous",
    roles=ALL_ROLES,  # auth_mode=off → full access for local pilot
    auth_via="anonymous",
)

ANON_RESTRICTED = Principal(
    sub="anonymous",
    name="anonymous",
    roles=frozenset({ROLE_VIEWER}),
    auth_via="anonymous",
)


def _load_token_table() -> dict[str, Principal]:
    """Map raw token string → Principal.

    Sources (later overrides earlier):
      1. config/auth.json  { "tokens": { "<token>": {sub,name,roles} } }
      2. AUTH_TOKENS_JSON env (same shape or flat token→roles)
    """
    settings = get_settings()
    table: dict[str, Principal] = {}

    path = Path(settings.config_dir) / "auth.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tokens = data.get("tokens") or {}
            for tok, meta in tokens.items():
                if not tok or not isinstance(meta, dict):
                    continue
                roles = frozenset(str(r) for r in (meta.get("roles") or []) if r in ALL_ROLES)
                if not roles:
                    roles = frozenset({ROLE_VIEWER})
                table[str(tok)] = Principal(
                    sub=str(meta.get("sub") or "user"),
                    name=str(meta.get("name") or meta.get("sub") or "user"),
                    roles=roles,
                    auth_via="apikey",
                )
        except Exception:  # noqa: BLE001
            logger.exception("failed to load %s", path)

    raw = getattr(settings, "auth_tokens_json", None) or ""
    if raw.strip():
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict) and "tokens" in extra:
                extra = extra["tokens"]
            if isinstance(extra, dict):
                for tok, meta in extra.items():
                    if isinstance(meta, dict):
                        roles = frozenset(
                            str(r) for r in (meta.get("roles") or []) if r in ALL_ROLES
                        )
                        table[str(tok)] = Principal(
                            sub=str(meta.get("sub") or "user"),
                            name=str(meta.get("name") or "user"),
                            roles=roles or frozenset({ROLE_VIEWER}),
                            auth_via="apikey",
                        )
                    elif isinstance(meta, list):
                        roles = frozenset(str(r) for r in meta if r in ALL_ROLES)
                        table[str(tok)] = Principal(
                            sub="user",
                            name="user",
                            roles=roles or frozenset({ROLE_VIEWER}),
                            auth_via="apikey",
                        )
        except Exception:  # noqa: BLE001
            logger.exception("AUTH_TOKENS_JSON parse failed")

    return table


def parse_bearer_or_key(
    authorization: Optional[str],
    api_key: Optional[str],
) -> Optional[str]:
    """Extract raw credential from Authorization: Bearer … or X-API-Key."""
    if api_key and api_key.strip():
        return api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def resolve_principal(
    authorization: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Principal:
    """Resolve request principal according to AUTH_MODE."""
    settings = get_settings()
    mode = (getattr(settings, "auth_mode", None) or "off").lower().strip()

    if mode in ("", "off", "disabled", "none"):
        # optional identity even when open (for audit / UI), but full roles
        cred = parse_bearer_or_key(authorization, api_key)
        if cred:
            table = _load_token_table()
            if cred in table:
                p = table[cred]
                return Principal(
                    sub=p.sub,
                    name=p.name,
                    roles=ALL_ROLES,  # mode off ignores role restrictions
                    auth_via=p.auth_via,
                )
        return ANON_OPEN

    # apikey | oidc_stub (JWT not fully validated yet — same token table for stub)
    cred = parse_bearer_or_key(authorization, api_key)
    if not cred:
        return ANON_RESTRICTED

    table = _load_token_table()
    if cred in table:
        return table[cred]

    # oidc_stub: accept opaque tokens with role claim prefix "stub:<sub>:<roles>"
    # e.g. stub:alice:senior,admin
    if mode in ("oidc_stub", "oidc") and cred.startswith("stub:"):
        parts = cred.split(":")
        if len(parts) >= 3:
            sub = parts[1] or "oidc-user"
            roles = frozenset(r for r in parts[2].split(",") if r in ALL_ROLES)
            return Principal(
                sub=sub,
                name=sub,
                roles=roles or frozenset({ROLE_VIEWER}),
                auth_via="oidc_stub",
            )

    return ANON_RESTRICTED
