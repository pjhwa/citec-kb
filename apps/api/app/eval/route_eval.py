"""Eval intent routing for time_list + analytics gold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.query.planner import plan_query


def route_intent(q: str) -> dict[str, Any]:
    return plan_query(q)


def run_eval(gold_path: Path) -> dict[str, Any]:
    data = json.loads(gold_path.read_text(encoding="utf-8"))
    queries = data.get("queries") or []
    results = []
    hits = 0
    for item in queries:
        q = item["q"]
        got = route_intent(q)
        intent = got.get("intent")
        ok = intent == item.get("expect_intent")
        detail_ok = True
        if ok and item.get("expect_group_by") and got.get("group_by") != item["expect_group_by"]:
            detail_ok = False
        if ok and item.get("expect_component") and got.get("component") != item["expect_component"]:
            detail_ok = False
        if ok and item.get("expect_mode") and got.get("mode") != item["expect_mode"]:
            detail_ok = False
        if ok and item.get("expect_entity") and got.get("entity") != item["expect_entity"]:
            detail_ok = False
        if ok and item.get("expect_range_label") and got.get("range_label") != item["expect_range_label"]:
            detail_ok = False
        if ok and item.get("expect_period_days") is not None and got.get("period_days") != item["expect_period_days"]:
            detail_ok = False
        if ok and item.get("expect_fields"):
            got_fields = set(got.get("fields") or [])
            exp_fields = set(item["expect_fields"])
            if not exp_fields.issubset(got_fields):
                detail_ok = False
        passed = ok and detail_ok
        if passed:
            hits += 1
        results.append(
            {
                "id": item.get("id"),
                "q": q,
                "expect": item.get("expect_intent"),
                "got": intent,
                "detail": {
                    k: got.get(k)
                    for k in (
                        "group_by",
                        "mode",
                        "component",
                        "entity",
                        "range_label",
                        "period_days",
                        "fields",
                    )
                },
                "pass": passed,
            }
        )
    n = len(queries)
    return {
        "n": n,
        "hits": hits,
        "pass_rate": hits / n if n else 0.0,
        "pass": hits == n,
        "per_query": results,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--gold", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    report = run_eval(Path(args.gold))
    print(json.dumps({k: report[k] for k in ("n", "hits", "pass_rate", "pass")}, ensure_ascii=False))
    for r in report["per_query"]:
        mark = "OK" if r["pass"] else "FAIL"
        print(f"  {mark} {r['id']} expect={r['expect']} got={r['got']} {r['detail']}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
