"""Synchronous unmask audit insert. Raises on failure — the caller must then NOT send the original."""

from __future__ import annotations

from typing import Any, Optional

from app.auth.principal import Principal
from app.db.models import UnmaskAuditLog
from app.db.session import session_scope


def build_row(
    *, principal: Principal, level: str, method: str, path: str, doc_refs: list, counts: dict,
    client_ip: Optional[str], outcome: str,
) -> dict[str, Any]:
    return {
        "sub": principal.sub[:128],
        "name": (principal.name or "")[:128],
        "roles": ",".join(sorted(principal.roles))[:128],
        "level": level,
        "method": method[:8],
        "path": path[:256],
        "doc_refs": [str(r)[:128] for r in doc_refs[:50]],
        "counts": {k: int(v) for k, v in counts.items()},
        "client_ip": client_ip[:64] if client_ip else None,
        "outcome": outcome,
    }


def write_unmask_audit(**kw: Any) -> None:
    row = build_row(**kw)
    with session_scope() as session:
        session.add(UnmaskAuditLog(**row))
