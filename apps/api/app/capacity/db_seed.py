"""Sync capacity_rules / pricing_rules tables from JSON seed (FAQ 1안)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from sqlalchemy import delete, func, select

from app.capacity.seed import clear_basis_seed_cache, load_basis_seed
from app.db.models import CapacityRule, PricingRule
from app.db.session import session_scope

logger = logging.getLogger("citec.capacity.db_seed")


def _slug(s: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", s.strip()).strip("-").lower()
    return t or "x"


def _load_json_seed_only() -> dict[str, Any]:
    """Read JSON/embedded seed without preferring DB (avoid circular re-seed)."""
    # Temporarily clear cache and skip DB by reading file paths used in seed module.
    from app.capacity import seed as seed_mod

    for p in seed_mod._seed_paths():
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                data["_loaded_from"] = str(p)
                return data
        except OSError:
            continue
    out = dict(seed_mod._EMBEDDED)
    out["_loaded_from"] = "embedded"
    return out


def seed_capacity_db(*, replace: bool = True) -> dict[str, Any]:
    """Upsert capacity + pricing rows from JSON seed file (not from DB)."""
    seed = _load_json_seed_only()
    basis = str(seed.get("basis") or "1안")
    period_days = int(seed.get("period_days") or 7)
    mm = float(seed.get("mm_per_field_week") or 0.25)
    source_ref = seed.get("source_ref")
    fields = seed.get("fields") or []
    pricing = seed.get("pricing") or {}

    with session_scope() as session:
        if replace:
            session.execute(delete(CapacityRule).where(CapacityRule.basis == basis))
            # pricing is global for now
            session.execute(delete(PricingRule))

        cap_n = 0
        for fdef in fields:
            field = str(fdef["field"])
            rid = f"cap:{_slug(basis)}:{_slug(field)}"
            row = session.get(CapacityRule, rid)
            if row is None:
                row = CapacityRule(id=rid)
                session.add(row)
            row.basis = basis
            row.period_days = period_days
            row.field = field
            row.units = int(fdef["units"])
            row.unit_kind = str(fdef.get("unit_kind") or "host")
            row.mm_per_field_week = mm
            row.source_ref = source_ref
            row.metadata_ = {
                "price_group": fdef.get("price_group"),
                "loaded_from": seed.get("_loaded_from"),
            }
            row.is_active = True
            cap_n += 1

        price_n = 0
        for group, pr in pricing.items():
            rid = f"price:{_slug(group)}"
            row = session.get(PricingRule, rid)
            if row is None:
                row = PricingRule(id=rid)
                session.add(row)
            row.field_group = str(group)
            row.unit_kind = str(pr.get("unit_kind") or "host")
            row.unit_price = float(pr["unit_price"])
            row.currency = str(pr.get("currency") or "KRW")
            row.source_ref = source_ref
            row.is_active = True
            price_n += 1

        session.flush()
        cap_total = session.scalar(select(func.count()).select_from(CapacityRule)) or 0
        price_total = session.scalar(select(func.count()).select_from(PricingRule)) or 0

    clear_basis_seed_cache()
    return {
        "capacity_upserted": cap_n,
        "pricing_upserted": price_n,
        "capacity_total": int(cap_total),
        "pricing_total": int(price_total),
        "basis": basis,
        "source": seed.get("_loaded_from"),
        "llm_used": False,
    }


def load_basis_from_db(basis: str = "1안") -> Optional[dict[str, Any]]:
    """Build calculator seed dict from DB rows, or None if empty."""
    with session_scope() as session:
        rules = list(
            session.scalars(
                select(CapacityRule)
                .where(CapacityRule.is_active.is_(True))
                .where(CapacityRule.basis == basis)
            ).all()
        )
        if not rules:
            return None
        prices = list(
            session.scalars(select(PricingRule).where(PricingRule.is_active.is_(True))).all()
        )
        # materialize inside session
        field_rows = [
            {
                "field": r.field,
                "units": r.units,
                "unit_kind": r.unit_kind,
                "price_group": (r.metadata_ or {}).get("price_group"),
            }
            for r in rules
        ]
        period_days = rules[0].period_days
        mm = rules[0].mm_per_field_week
        source_ref = rules[0].source_ref
        pricing_map = {
            p.field_group: {
                "unit_price": p.unit_price,
                "unit_kind": p.unit_kind,
                "currency": p.currency,
            }
            for p in prices
        }

    return {
        "basis": basis,
        "period_days": period_days,
        "mm_per_field_week": mm,
        "source_ref": source_ref,
        "source_note": "loaded from capacity_rules DB",
        "fields": field_rows,
        "pricing": pricing_map,
        "_loaded_from": "database",
    }


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-replace", action="store_true")
    args = p.parse_args()
    report = seed_capacity_db(replace=not args.no_replace)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
