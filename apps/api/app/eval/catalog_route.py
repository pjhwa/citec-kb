"""Catalog-100 routing evaluation (intent vs qtype)."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.query.planner import plan_query

# qtype → acceptable router intents
QTYPE_ALLOWED: dict[str, set[str]] = {
    "capacity_lookup": {"capacity", "hybrid_search"},  # 사례 조회는 hybrid 허용
    "aggregate_stats": {"analytics", "hybrid_search"},
    "checklist": {"checklist", "hybrid_search"},
    "similar_incident": {"similar_incident"},
    "entity_aggregate": {"entity_aggregate", "analytics", "hybrid_search"},
    # PISA 항목 목록 factoid → checklist 허용
    "factoid": {"hybrid_search", "factoid", "checklist"},
    "technical_synthesize": {"hybrid_search", "synthesize", "exhaustive"},
    "prevention_map": {"hybrid_search", "prevention", "similar_incident", "analytics"},
    "risk": {"hybrid_search"},
}

# Preferred (primary) intent for reporting
QTYPE_PRIMARY: dict[str, str] = {
    "capacity_lookup": "capacity",
    "aggregate_stats": "analytics",
    "checklist": "checklist",
    "similar_incident": "similar_incident",
    "entity_aggregate": "entity_aggregate",
    "factoid": "hybrid_search",
    "technical_synthesize": "exhaustive",  # 전부/패턴 수집 질의 우선
    "prevention_map": "prevention",
    "risk": "hybrid_search",
}


def eval_catalog(gold_path: Path) -> dict[str, Any]:
    items = json.loads(gold_path.read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("queries") or items.get("items") or []

    per: list[dict[str, Any]] = []
    hits = 0
    primary_hits = 0
    by_qtype: dict[str, Counter] = defaultdict(Counter)

    for item in items:
        q = item.get("q") or ""
        qtype = item.get("qtype") or "unknown"
        plan = plan_query(q)
        intent = plan.get("intent") or "error"
        allowed = QTYPE_ALLOWED.get(qtype, {"hybrid_search"})
        primary = QTYPE_PRIMARY.get(qtype, "hybrid_search")
        ok = intent in allowed
        primary_ok = intent == primary
        if ok:
            hits += 1
        if primary_ok:
            primary_hits += 1
        by_qtype[qtype]["n"] += 1
        by_qtype[qtype]["hits"] += int(ok)
        by_qtype[qtype]["primary"] += int(primary_ok)
        per.append(
            {
                "id": item.get("id"),
                "qtype": qtype,
                "q": q,
                "intent": intent,
                "allowed": sorted(allowed),
                "primary": primary,
                "pass": ok,
                "primary_pass": primary_ok,
            }
        )

    n = len(items)
    qtype_summary = {
        qt: {
            "n": c["n"],
            "hits": c["hits"],
            "pass_rate": round(c["hits"] / c["n"], 3) if c["n"] else 0.0,
            "primary_rate": round(c["primary"] / c["n"], 3) if c["n"] else 0.0,
        }
        for qt, c in sorted(by_qtype.items())
    }
    return {
        "n": n,
        "hits": hits,
        "pass_rate": round(hits / n, 4) if n else 0.0,
        "primary_hits": primary_hits,
        "primary_rate": round(primary_hits / n, 4) if n else 0.0,
        "gate_min_pass_rate": 0.95,
        "pass": (hits / n >= 0.95) if n else False,
        "by_qtype": qtype_summary,
        "failures": [p for p in per if not p["pass"]],
        "primary_misses": [p for p in per if p["pass"] and not p["primary_pass"]],
        "per_query": per,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--gold", default="data/gold/query_catalog_100.json")
    p.add_argument("--out", default=None)
    args = p.parse_args()
    report = eval_catalog(Path(args.gold))
    summary = {
        k: report[k]
        for k in (
            "n",
            "hits",
            "pass_rate",
            "primary_hits",
            "primary_rate",
            "pass",
            "by_qtype",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["failures"]:
        print(f"\nFAILURES ({len(report['failures'])}):")
        for f in report["failures"][:30]:
            print(f"  {f['id']} qtype={f['qtype']} got={f['intent']} allowed={f['allowed']}")
            print(f"    {f['q'][:90]}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
