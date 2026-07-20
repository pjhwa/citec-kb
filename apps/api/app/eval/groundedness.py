"""Automatic groundedness-oriented metrics for Fast QA sample set.

Not a substitute for human review — reports citation usage, overlap faithfulness,
abstain correctness, and trust levels for n≈20 pilot queries.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from app.rag.pipeline import run_fast_rag
from app.retrieval.search import SearchFilters
from app.trust.engine import _overlap_faithfulness


def _load_gold(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_gold() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd() / "data/gold/qa_groundedness_20.json",
        here.parents[4] / "data/gold/qa_groundedness_20.json",
        here.parents[3] / "data/gold/qa_groundedness_20.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def evaluate_one(g: dict[str, Any]) -> dict[str, Any]:
    filters = SearchFilters(**(g.get("filters") or {}))
    result = run_fast_rag(g["q"], top_k=6, filters=filters, mode="fast")
    answer = result.get("answer") or ""
    cites_used = result.get("citations_used") or []
    citations = result.get("citations") or []
    blobs = [c.get("snippet") or "" for c in citations]
    overlap = _overlap_faithfulness(answer, blobs)
    has_cite = bool(cites_used) or bool(re.search(r"\[C\d+\]", answer, re.I))
    expect_abstain = bool(g.get("expect_abstain"))
    abstained = bool(result.get("abstained"))

    if expect_abstain:
        ok = abstained or (result.get("trust") or {}).get("level") in {"abstain", "empty", "weak"}
    else:
        ok = bool(answer) and (has_cite or abstained)

    return {
        "id": g["id"],
        "q": g["q"],
        "ok": ok,
        "expect_abstain": expect_abstain,
        "abstained": abstained,
        "has_citation": has_cite,
        "citations_used": cites_used,
        "overlap": overlap,
        "trust_level": (result.get("trust") or {}).get("level"),
        "retrieval_trust": (result.get("retrieval") or {}).get("trust_retrieval"),
        "n_citations": len(citations),
        "answer_len": len(answer),
        "llm_error": result.get("llm_error"),
        "answer_preview": answer[:180],
    }


def run_eval(gold: dict[str, Any]) -> dict[str, Any]:
    rows = [evaluate_one(g) for g in gold.get("queries") or []]
    gate = gold.get("gate") or {}
    answered = [r for r in rows if not r.get("expect_abstain")]
    abstain_rows = [r for r in rows if r.get("expect_abstain")]

    cite_rate = (
        sum(1 for r in answered if r["has_citation"]) / len(answered) if answered else 0.0
    )
    overlap_ok = (
        sum(1 for r in answered if r["overlap"] in {"ok", "medium", "n/a"} and r["has_citation"])
        / len(answered)
        if answered
        else 0.0
    )
    # simpler overlap rate among answered with text
    overlap_rate = (
        sum(1 for r in answered if r["overlap"] in {"ok", "medium"}) / len(answered)
        if answered
        else 0.0
    )
    abstain_ok = (
        sum(1 for r in abstain_rows if r["ok"]) / len(abstain_rows) if abstain_rows else 1.0
    )
    overall_ok = sum(1 for r in rows if r["ok"]) / len(rows) if rows else 0.0

    report = {
        "n": len(rows),
        "n_answered": len(answered),
        "citation_rate": round(cite_rate, 4),
        "overlap_rate": round(overlap_rate, 4),
        "abstain_ok_rate": round(abstain_ok, 4),
        "row_ok_rate": round(overall_ok, 4),
        "gate": gate,
        "pass": (
            len(answered) >= int(gate.get("min_answered") or 10)
            and cite_rate >= float(gate.get("citation_rate_min") or 0.6)
            and overlap_rate >= float(gate.get("overlap_ok_rate_min") or 0.5)
            and abstain_ok >= 0.5
        ),
        "per_query": rows,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    gold_path = Path(args.gold) if args.gold else _default_gold()
    gold = _load_gold(gold_path)
    if args.limit:
        gold = {**gold, "queries": (gold.get("queries") or [])[: args.limit]}
    report = run_eval(gold)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    # compact summary
    print(
        json.dumps(
            {
                "n": report["n"],
                "citation_rate": report["citation_rate"],
                "overlap_rate": report["overlap_rate"],
                "abstain_ok_rate": report["abstain_ok_rate"],
                "row_ok_rate": report["row_ok_rate"],
                "pass": report["pass"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    fails = [r for r in report["per_query"] if not r["ok"]]
    if fails:
        print("FAILS:", [(f["id"], f["trust_level"], f["has_citation"], f["llm_error"]) for f in fails[:8]])
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
