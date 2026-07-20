"""Load capacity/pricing seed (FAQ 1안). Prefer file; fall back to embedded."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# Embedded fallback matches data/seeds/capacity/basis_1an.json
_EMBEDDED: dict[str, Any] = {
    "basis": "1안",
    "period_days": 7,
    "mm_per_field_week": 0.25,
    "source_ref": "support_history/QRB_품질_인프라구축검증_PISA_FAQ.md",
    "source_note": "진단범위와 공수 · 진단단가 절. 1주 표준표; N주 질의는 period_days/7 환산.",
    "fields": [
        {"field": "AIX", "units": 20, "unit_kind": "host", "price_group": "server"},
        {"field": "HP-UX", "units": 20, "unit_kind": "host", "price_group": "server"},
        {"field": "Solaris", "units": 20, "unit_kind": "host", "price_group": "server"},
        {"field": "Linux", "units": 20, "unit_kind": "host", "price_group": "server"},
        {"field": "Windows", "units": 20, "unit_kind": "host", "price_group": "server"},
        {"field": "Web/WAS", "units": 5, "unit_kind": "instance", "price_group": "db_mw_instance"},
        {"field": "DBMS", "units": 5, "unit_kind": "instance", "price_group": "db_mw_instance"},
        {"field": "가상화", "units": 5, "unit_kind": "host", "price_group": "server"},
        {"field": "스토리지", "units": 5, "unit_kind": "host", "price_group": "storage"},
        {"field": "네트워크", "units": 10, "unit_kind": "host", "price_group": "nw"},
    ],
    "pricing": {
        "server": {"unit_price": 100, "unit_kind": "host"},
        "db_mw_instance": {"unit_price": 500, "unit_kind": "instance"},
        "storage": {"unit_price": 180, "unit_kind": "host"},
        "nw": {"unit_price": 50, "unit_kind": "host"},
    },
}


def _seed_paths() -> list[Path]:
    here = Path(__file__).resolve()
    # Prefer mounted data; then walk up parents for repo checkout.
    candidates: list[Path] = [
        Path("/app/data/seeds/capacity/basis_1an.json"),
        Path("data/seeds/capacity/basis_1an.json"),
    ]
    for i, parent in enumerate(here.parents):
        if i > 6:
            break
        candidates.append(parent / "data" / "seeds" / "capacity" / "basis_1an.json")
    return candidates


@lru_cache(maxsize=1)
def load_basis_seed() -> dict[str, Any]:
    """Prefer DB capacity_rules when seeded; else JSON file; else embedded."""
    try:
        from app.capacity.db_seed import load_basis_from_db

        db = load_basis_from_db("1안")
        if db and db.get("fields"):
            return db
    except Exception:  # noqa: BLE001
        pass

    for p in _seed_paths():
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                data["_loaded_from"] = str(p)
                return data
        except OSError:
            continue
    out = dict(_EMBEDDED)
    out["_loaded_from"] = "embedded"
    return out


def clear_basis_seed_cache() -> None:
    load_basis_seed.cache_clear()
