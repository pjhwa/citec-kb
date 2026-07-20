"""Capacity calculator — rules only (no LLM)."""

from __future__ import annotations

from typing import Any, Optional

from app.capacity.seed import load_basis_seed

# Canonical field name aliases
_FIELD_ALIASES: dict[str, str] = {
    "aix": "AIX",
    "hp-ux": "HP-UX",
    "hpux": "HP-UX",
    "solaris": "Solaris",
    "linux": "Linux",
    "리눅스": "Linux",
    "windows": "Windows",
    "윈도우": "Windows",
    "web/was": "Web/WAS",
    "web": "Web/WAS",
    "was": "Web/WAS",
    "web_was": "Web/WAS",
    "dbms": "DBMS",
    "db": "DBMS",
    "database": "DBMS",
    "가상화": "가상화",
    "virtualization": "가상화",
    "스토리지": "스토리지",
    "storage": "스토리지",
    "네트워크": "네트워크",
    "network": "네트워크",
    "nw": "네트워크",
}


def normalize_field(name: str) -> Optional[str]:
    t = (name or "").strip()
    if not t:
        return None
    if t in {f["field"] for f in load_basis_seed()["fields"]}:
        return t
    return _FIELD_ALIASES.get(t.lower())


def list_capacity_rules(*, basis: str = "1안") -> dict[str, Any]:
    seed = load_basis_seed()
    if basis and basis != seed.get("basis"):
        # only 1안 seeded for now
        pass
    return {
        "basis": seed["basis"],
        "period_days": seed["period_days"],
        "mm_per_field_week": seed["mm_per_field_week"],
        "source_ref": seed.get("source_ref"),
        "source_note": seed.get("source_note"),
        "fields": list(seed["fields"]),
        "pricing": dict(seed.get("pricing") or {}),
        "loaded_from": seed.get("_loaded_from"),
        "llm_used": False,
    }


def estimate_capacity(
    *,
    period_days: int = 7,
    basis: str = "1안",
    fields: Optional[list[str]] = None,
    include_pricing: bool = True,
) -> dict[str, Any]:
    """Scale 1-week standard units/M/M by period_days/7.

    Numbers come only from seed rules — never from an LLM.
    """
    seed = load_basis_seed()
    base_days = int(seed.get("period_days") or 7)
    period_days = max(1, min(int(period_days), 365))
    scale = period_days / base_days
    mm_week = float(seed.get("mm_per_field_week") or 0.25)
    pricing = seed.get("pricing") or {}

    wanted: Optional[set[str]] = None
    if fields:
        wanted = set()
        unknown: list[str] = []
        for f in fields:
            n = normalize_field(f)
            if n:
                wanted.add(n)
            else:
                unknown.append(f)
    else:
        unknown = []

    rows: list[dict[str, Any]] = []
    total_units = 0
    total_mm = 0.0
    total_price = 0.0

    for fdef in seed["fields"]:
        fname = fdef["field"]
        if wanted is not None and fname not in wanted:
            continue
        base_u = int(fdef["units"])
        units = int(round(base_u * scale))
        mm = round(mm_week * scale, 4)
        row: dict[str, Any] = {
            "field": fname,
            "base_units": base_u,
            "units": units,
            "unit_kind": fdef.get("unit_kind") or "host",
            "mm": mm,
            "mm_per_field_week": mm_week,
        }
        if include_pricing:
            pg = fdef.get("price_group")
            pr = pricing.get(pg) if pg else None
            if pr:
                unit_price = float(pr["unit_price"])
                price = round(unit_price * units, 2)
                row["price_group"] = pg
                row["unit_price"] = unit_price
                row["price"] = price
                total_price += price
            else:
                row["unit_price"] = None
                row["price"] = None
        rows.append(row)
        total_units += units
        total_mm += mm

    scale_note = None
    if abs(scale - 1.0) > 1e-9:
        scale_note = (
            f"{base_days}일(1주) 표준×{scale:g} 환산 "
            f"(원문에 {period_days}일 표 없음 — rules 계산)"
        )

    return {
        "basis": seed.get("basis") or basis,
        "period_days": period_days,
        "base_period_days": base_days,
        "scale": scale,
        "scale_note": scale_note,
        "source_ref": seed.get("source_ref"),
        "fields": rows,
        "totals": {
            "field_count": len(rows),
            "units": total_units,
            "mm": round(total_mm, 4),
            "price": round(total_price, 2) if include_pricing else None,
            "currency": "KRW" if include_pricing else None,
        },
        "unknown_fields": unknown,
        "method": "capacity_rules",
        "llm_used": False,
        "note": "FAQ 1안 기준. 분야별 0.25M/M·1주 표준 대수. 숫자는 Rules 계산만.",
    }
