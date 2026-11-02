"""Approved synonym groups from the DB, TTL-cached; never breaks search."""

from __future__ import annotations

import logging
import os
import time

from app.synonyms.rules import clean_terms

logger = logging.getLogger("citec.synonyms.runtime")

def _parse_ttl(raw: str | None) -> float:
    try:
        v = float(raw) if raw is not None and raw.strip() != "" else 60.0
    except ValueError:
        return 60.0
    return v if v >= 0 else 60.0


_TTL_S = _parse_ttl(os.environ.get("SEARCH_SYNONYMS_TTL_S"))
_cache: tuple[float, list[list[str]]] | None = None


def _include_proposed() -> bool:
    # Dev/gate only: lets the eval gate measure proposed candidates before approval.
    return os.environ.get("SEARCH_SYNONYMS_INCLUDE_PROPOSED", "").strip() in ("1", "true", "yes")


def _load() -> list[list[str]]:
    from app.synonyms import store

    return store.load_groups(include_proposed=_include_proposed())


def reset_cache() -> None:
    global _cache
    _cache = None


def runtime_groups() -> list[list[str]]:
    """Valid DB groups. On any DB error: previous good value, else []."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL_S:
        return _cache[1]
    prev = _cache[1] if _cache is not None else []
    # Stale-while-revalidate: concurrent/re-entrant callers during a slow reload
    # get the previous value instead of all hitting the DB.
    _cache = (now, prev)
    try:
        groups = [g for g in (clean_terms(g) for g in _load()) if len(g) >= 2]
    except Exception as exc:  # noqa: BLE001 — search must never fail because of this
        logger.warning("synonym runtime load failed: %s", exc)
        groups = prev
    _cache = (now, groups)
    return groups
