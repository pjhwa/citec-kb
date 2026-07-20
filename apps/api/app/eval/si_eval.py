"""Similar-incident gold evaluation (G01–G10 style + false-apply)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_gold(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_si_eval(gold: dict[str, Any], *, top_k: int = 3) -> dict[str, Any]:
    from app.si.retrieve import similar_incidents

    queries = gold.get("queries") or []
    gate = gold.get("gate") or {}
    min_pass = float(gate.get("min_pass_rate") or 0.7)

    per: list[dict[str, Any]] = []
    scored = 0
    hits = 0
    false_apply_fail = 0

    for g in queries:
        if g.get("optional"):
            continue
        kind = g.get("kind") or "true_similar"
        result = similar_incidents(
            g["symptom"],
            top_k=top_k,
            product=g.get("product"),
            service=g.get("service"),
            environment=g.get("environment"),
        )
        cases = result.get("cases") or []
        top_ids = [c.get("external_id") for c in cases]
        top1 = top_ids[0] if top_ids else None
        app1 = (cases[0].get("applicability") or {}).get("label") if cases else None

        row: dict[str, Any] = {
            "id": g["id"],
            "kind": kind,
            "top_ids": top_ids,
            "top1": top1,
            "applicability_top1": app1,
        }

        if kind == "false_apply":
            bad = set(g.get("false_apply_external_ids") or [])
            # Fail if a forbidden case is top1 with 가능
            fail = bool(top1 in bad and app1 == "가능")
            row["pass"] = not fail
            if fail:
                false_apply_fail += 1
            scored += 1
            if row["pass"]:
                hits += 1
        else:
            relevant = set(g.get("relevant_external_ids") or [])
            if not relevant:
                row["pass"] = None
                row["skip"] = "no relevant labels"
            else:
                ok = any(x in relevant for x in top_ids[:top_k])
                row["pass"] = ok
                scored += 1
                if ok:
                    hits += 1
        per.append(row)

    rate = (hits / scored) if scored else 0.0
    true_similar = [r for r in per if r.get("kind") in {"true_similar", "exact_key_hint"} and r.get("pass") is not None]
    true_hits = sum(1 for r in true_similar if r.get("pass"))
    g3_min = int(gate.get("g3_min_true_similar_pass") or 8)
    fa_clean = false_apply_fail == 0 if gate.get("require_false_apply_clean", True) else True
    report = {
        "n_scored": scored,
        "hits": hits,
        "pass_rate": round(rate, 4),
        "true_similar_n": len(true_similar),
        "true_similar_hits": true_hits,
        "false_apply_failures": false_apply_fail,
        "gate_min_pass_rate": min_pass,
        "g3_min_true_similar_pass": g3_min,
        "g3_style_pass": true_hits >= g3_min and fa_clean,
        "pass": (
            rate >= min_pass
            and scored >= int(gate.get("min_cases") or 8)
            and fa_clean
            and true_hits >= g3_min
        ),
        "per_query": per,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--gold",
        default="",
        help="Path to si gold json (default: repo data/gold/si_g01_g10.json)",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.gold:
        gold_path = Path(args.gold)
    else:
        # apps/api/app/eval -> repo root
        here = Path(__file__).resolve()
        candidates = [
            Path.cwd() / "data/gold/si_g01_g10.json",
            here.parents[4] / "data/gold/si_g01_g10.json",
            here.parents[3] / "data/gold/si_g01_g10.json",
            Path("/app/data/gold/si_g01_g10.json"),
        ]
        gold_path = next((p for p in candidates if p.is_file()), candidates[0])

    gold = load_gold(gold_path)
    report = run_si_eval(gold)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text[:3000])
    print(
        f"\nSUMMARY n={report['n_scored']} pass_rate={report['pass_rate']} "
        f"true_hits={report.get('true_similar_hits')}/{report.get('true_similar_n')} "
        f"false_apply_fail={report['false_apply_failures']} "
        f"g3_style={report.get('g3_style_pass')} pass={report['pass']}"
    )
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
