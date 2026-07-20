"""Auth principals, API-key gate, OIDC JWT validation."""

from app.auth.deps import get_principal, require_roles
from app.auth.principal import Principal, parse_bearer_or_key

__all__ = [
    "Principal",
    "get_principal",
    "parse_bearer_or_key",
    "require_roles",
]
