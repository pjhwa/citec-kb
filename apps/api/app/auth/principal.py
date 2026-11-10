"""Principal model + token table (API keys / OIDC JWT / stub)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
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
    auth_via: str = "anonymous"  # anonymous | apikey | oidc | oidc_stub

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


# Token-table cache. Keyed by (auth.json mtime_ns+size+inode or None, AUTH_TOKENS_JSON raw)
# so issuing/revoking a token (editing the file) takes effect on the next request
# without a restart, and unchanged config is never re-read/re-parsed per request.
# A missing/unparseable file never falls back to an older cache entry: the key is
# different, the rebuild yields an empty file contribution, so a revoked token can't
# survive a corrupted file.
_token_cache_lock = threading.Lock()
_token_cache: tuple[object, dict[str, Principal]] | None = None
_token_table_builds = 0  # test instrumentation: how many times the table was rebuilt


ADMIN_TOKENS_FILE = "auth_tokens.json"
HASH_PREFIX = "sha256:"


def admin_tokens_path() -> Path:
    """Tokens issued from the admin page: <data_dir>/auth_tokens.json, keyed by sha256 digest."""
    return Path(getattr(get_settings(), "data_dir", "data")) / ADMIN_TOKENS_FILE


def _admin_tokens_key() -> object:
    path = admin_tokens_path()
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return (str(path), None, None, None)


def token_digest(raw: str) -> str:
    return HASH_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lookup_token(table: dict[str, Principal], cred: str) -> Optional[Principal]:
    """Find a credential in the table: raw entries (config/env) or hashed admin-issued ones.

    A credential that itself starts with "sha256:" never matches a raw key, so presenting a
    stored digest as if it were the token cannot authenticate.
    """
    if not cred.startswith(HASH_PREFIX) and cred in table:
        return table[cred]
    return table.get(token_digest(cred))


def _token_cache_key() -> tuple[object, str]:
    settings = get_settings()
    path = Path(settings.config_dir) / "auth.json"
    try:
        st = path.stat()
        file_key: object = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        file_key = (str(path), None, None, None)
    return (file_key, _admin_tokens_key()), getattr(settings, "auth_tokens_json", None) or ""


def _load_token_table() -> dict[str, Principal]:
    """Map raw token string → Principal (cached; see _token_cache above)."""
    global _token_cache
    key = _token_cache_key()
    with _token_cache_lock:
        if _token_cache is not None and _token_cache[0] == key:
            return _token_cache[1]
        table = _build_token_table()
        _token_cache = (key, table)
        return table


def _build_token_table() -> dict[str, Principal]:
    """Map raw token string → Principal.

    Sources (later overrides earlier):
      1. config/auth.json  { "tokens": { "<token>": {sub,name,roles} } }
      2. AUTH_TOKENS_JSON env (same shape or flat token→roles)
    """
    global _token_table_builds
    _token_table_builds += 1
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

    admin_path = admin_tokens_path()
    if admin_path.is_file():
        try:
            for tok, meta in (json.loads(admin_path.read_text(encoding="utf-8")).get("tokens") or {}).items():
                if not str(tok).startswith(HASH_PREFIX) or not isinstance(meta, dict):
                    continue  # only digests are accepted from this file
                roles = frozenset(str(r) for r in (meta.get("roles") or []) if r in ALL_ROLES)
                table[str(tok)] = Principal(
                    sub=str(meta.get("sub") or "user"),
                    name=str(meta.get("name") or meta.get("sub") or "user"),
                    roles=roles or frozenset({ROLE_VIEWER}),
                    auth_via="apikey",
                )
        except Exception:  # noqa: BLE001
            logger.exception("failed to load %s", admin_path)

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

    cred = parse_bearer_or_key(authorization, api_key)

    if mode in ("", "off", "disabled", "none"):
        if cred:
            # Prefer OIDC JWT identity even in open mode (roles not enforced)
            try:
                from app.auth.oidc import validate_bearer_jwt

                p = validate_bearer_jwt(cred)
                if p:
                    return Principal(
                        sub=p.sub,
                        name=p.name,
                        roles=ALL_ROLES,
                        auth_via=p.auth_via,
                    )
            except Exception:  # noqa: BLE001
                pass
            table = _load_token_table()
            p = lookup_token(table, cred)
            if p is not None:
                return Principal(
                    sub=p.sub,
                    name=p.name,
                    roles=ALL_ROLES,
                    auth_via=p.auth_via,
                )
        return ANON_OPEN

    if not cred:
        return ANON_RESTRICTED

    # API keys always accepted in enforced modes (service accounts)
    found = lookup_token(_load_token_table(), cred)
    if found is not None:
        return found

    # oidc_stub opaque helper: test mode only (never under real "oidc", never outside dev envs)
    if (
        mode == "oidc_stub"
        and getattr(settings, "app_env", "dev") in ("dev", "local", "test")
        and cred.startswith("stub:")
    ):
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

    # Real JWT validation (oidc / oidc_stub / apikey hybrid)
    if mode in ("oidc", "oidc_stub", "apikey"):
        try:
            from app.auth.oidc import validate_bearer_jwt

            p = validate_bearer_jwt(cred)
            if p:
                return p
        except Exception:  # noqa: BLE001
            logger.exception("JWT validation error")

    return ANON_RESTRICTED
