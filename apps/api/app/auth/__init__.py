"""Auth principals, API-key gate, OIDC config stubs."""

from app.auth.principal import Principal, parse_bearer_or_key
from app.auth.deps import get_principal, require_roles

__all__ = [
    "Principal",
    "get_principal",
    "parse_bearer_or_key",
    "require_roles",
]
