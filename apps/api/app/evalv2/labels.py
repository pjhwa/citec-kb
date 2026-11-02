"""Review-candidate selection, label validation and evalset (dev/frozen) construction."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from app.evalv2 import qfilter as qf

MARKS = {"O": "expect", "△": "also_ok", "X": None}
CAVEATS = [
    "풀링은 현재 시스템이 올린 문서 위주로 후보를 만들어 절대 recall이 부풀려질 수 있다 — 전/후 상대 비교로 본다.",
    "합성 질의는 제목 겹침률 필터로 편향을 완화했을 뿐 완전히 제거하지 못했다.",
    "LLM 판정은 후보 제안일 뿐이고 최종 판정은 사람의 O/△/X다.",
    "검수하지 않은(표시 안 됨·무표시) 후보는 비관련으로 간주된다 — 풀링 판정의 한계로 recall이 과대평가될 수 있다.",
    "△(부분 관련)만 있고 O가 없는 질의는 정답 없음으로 제외된다.",
]


def _pool_rank(c: dict[str, Any]) -> int:
    ranks = [v for k, v in (c.get("arms") or {}).items() if k != "seed"]
    return min(ranks) if ranks else 99


def candidates_for_review(query: dict[str, Any], max_n: int = 8) -> list[dict[str, Any]]:
    """What the reviewer sees. Always shown: the seed doc and the pool's top-3 (by pool rank), regardless of the
    LLM. Then LLM positives, then items needing a human (unverified/missing/empty/invalid), capped at max_n
    (the cap counts only these remainder items, so the size is at most max_n + 4). Pool order is kept."""
    cands = query["candidates"]
    ranked = sorted((c for c in cands if _pool_rank(c) < 99), key=lambda c: (_pool_rank(c), c["external_id"]))
    guaranteed = {c["external_id"] for c in cands if "seed" in (c.get("arms") or {})}
    guaranteed |= {c["external_id"] for c in ranked[:3]}

    def priority(c: dict[str, Any]) -> int:
        llm = c.get("llm") or {}
        if llm.get("grade") in (1, 2):
            return 1
        if llm.get("status") in ("unverified", "missing", "empty", "invalid"):
            return 2
        return 9

    rest = [c for c in cands if c["external_id"] not in guaranteed and priority(c) < 9]
    rest.sort(key=lambda c: (priority(c), _pool_rank(c), c["external_id"]))  # total order -> deterministic
    keep = guaranteed | {c["external_id"] for c in rest[:max_n]}
    return [c for c in cands if c["external_id"] in keep]


def validate_review(judged: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if not str(review.get("reviewer") or "").strip():
        errs.append("reviewer is empty")
    try:
        datetime.strptime(str(review.get("reviewed_on") or ""), "%Y-%m-%d")
    except ValueError:
        errs.append("reviewed_on must be a real date YYYY-MM-DD")
    ids = [q["id"] for q in judged["queries"]]
    for qid in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append(f"duplicate query id in judged: {qid}")
    qmap = {q["id"]: {c["external_id"] for c in q["candidates"]} for q in judged["queries"]}
    lab = review.get("labels")
    if lab is None:
        lab = {}
    if not isinstance(lab, dict):
        return errs + ["labels must be an object of query id -> {candidate id: mark}"]
    for qid, marks in lab.items():
        if not isinstance(marks, dict):
            errs.append(f"{qid}: marks must be an object of candidate id -> mark")
            continue
        if qid not in qmap:
            errs.append(f"unknown query id {qid}")
            continue
        for eid, mark in marks.items():
            if eid not in qmap[qid]:
                errs.append(f"{qid}: unknown candidate {eid}")
            if mark not in MARKS:
                errs.append(f"{qid}/{eid}: bad mark {mark!r}")
    return errs


def _split(q: dict[str, Any]) -> str:
    """Keyed by seed doc when present (queries on one doc stay in one split), else by normalised query text."""
    key = ((q.get("seed_doc") or {}).get("external_id")) or qf.norm(q["q"])
    return "frozen" if int(hashlib.sha1(str(key).encode("utf-8")).hexdigest(), 16) % 2 else "dev"


def build_evalsets(judged: dict[str, Any], review: dict[str, Any], *, evalset_id: str, frozen_at: str | None = None) -> dict[str, Any]:
    errs = validate_review(judged, review)
    if errs:
        raise ValueError("invalid review: " + "; ".join(errs))
    now = frozen_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    cases = {"dev": [], "frozen": []}
    report: dict[str, Any] = {"kept": 0, "no_answer": 0, "unreviewed": 0, "unlabeled_candidates": 0, "kept_by_slice": {}}
    for q in judged["queries"]:
        marks = (review.get("labels") or {}).get(q["id"])
        if not marks:
            report["unreviewed"] += 1
            continue
        expect = [e for e in (c["external_id"] for c in q["candidates"]) if MARKS.get(marks.get(e)) == "expect"]
        also = [e for e in (c["external_id"] for c in q["candidates"]) if MARKS.get(marks.get(e)) == "also_ok"]
        if not expect:
            report["no_answer"] += 1
            continue
        split = _split(q)
        cases[split].append({
            "id": q["id"], "group": f"v2_{q['slice']}", "kind": "search", "q": q["q"], "filters": {},
            "expect": expect, "also_ok": also, "k": 10, "source": q["source"], "split": split,
            "reviewed_on": review["reviewed_on"],
            **({"seed_doc": q["seed_doc"]} if q.get("seed_doc") else {}),
            **({"count": q["count"]} if q.get("count") is not None else {}),
        })
        report["kept"] += 1
        report["kept_by_slice"][q["slice"]] = report["kept_by_slice"].get(q["slice"], 0) + 1
        report["unlabeled_candidates"] += sum(1 for c in q["candidates"] if c["external_id"] not in marks)
    base = {
        "provenance": f"실사용 질의(운영 mcp_access_log) + 합성 질의, 풀링 + LLM 보조 판정, 검수자 {review['reviewer']} "
                      f"({review['reviewed_on']}). 모델: {judged.get('model')}",
        "caveats": CAVEATS,
    }
    return {
        "dev": {"evalset_id": f"{evalset_id}-dev", **base, "cases": cases["dev"]},
        "frozen": {"evalset_id": f"{evalset_id}-frozen", **base, "frozen_at_utc": now, "cases": cases["frozen"]},
        "report": report,
    }
