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
]


def _pool_rank(c: dict[str, Any]) -> int:
    ranks = [v for k, v in (c.get("arms") or {}).items() if k != "seed"]
    return min(ranks) if ranks else 99


def candidates_for_review(query: dict[str, Any], max_n: int = 8) -> list[dict[str, Any]]:
    """What the reviewer sees: seed doc, LLM positives, items needing a human (unverified/missing/empty),
    and the pool's top-3 regardless of the LLM. Capped at max_n; the seed doc is
    priority 0 so it survives the cap (lowest-priority others are dropped first). Pool order is kept."""
    cands = query["candidates"]
    top3 = {c["external_id"] for c in sorted(cands, key=_pool_rank)[:3] if _pool_rank(c) < 99}

    def priority(c: dict[str, Any]) -> int:
        llm = c.get("llm") or {}
        if "seed" in (c.get("arms") or {}):
            return 0
        if llm.get("grade") in (1, 2):
            return 1
        if llm.get("status") in ("unverified", "missing", "empty", "invalid"):
            return 2
        if c["external_id"] in top3:
            return 3
        return 9

    picked = [c for c in cands if priority(c) < 9]
    picked.sort(key=lambda c: (priority(c), _pool_rank(c), c["external_id"]))  # total order -> deterministic
    keep = {c["external_id"] for c in picked[:max_n]}
    return [c for c in cands if c["external_id"] in keep]


def validate_review(judged: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if not str(review.get("reviewer") or "").strip():
        errs.append("reviewer is empty")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(review.get("reviewed_on") or "")):
        errs.append("reviewed_on must be YYYY-MM-DD")
    qmap = {q["id"]: {c["external_id"] for c in q["candidates"]} for q in judged["queries"]}
    for qid, marks in (review.get("labels") or {}).items():
        if qid not in qmap:
            errs.append(f"unknown query id {qid}")
            continue
        for eid, mark in (marks or {}).items():
            if eid not in qmap[qid]:
                errs.append(f"{qid}: unknown candidate {eid}")
            if mark not in MARKS:
                errs.append(f"{qid}/{eid}: bad mark {mark!r}")
    return errs


def _split(q: str) -> str:
    return "frozen" if int(hashlib.sha1(qf.norm(q).encode("utf-8")).hexdigest(), 16) % 2 else "dev"


def build_evalsets(judged: dict[str, Any], review: dict[str, Any], *, evalset_id: str) -> dict[str, Any]:
    errs = validate_review(judged, review)
    if errs:
        raise ValueError("invalid review: " + "; ".join(errs))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cases = {"dev": [], "frozen": []}
    report = {"kept": 0, "no_answer": 0, "unreviewed": 0}
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
        split = _split(q["q"])
        cases[split].append({
            "id": q["id"], "group": f"v2_{q['slice']}", "kind": "search", "q": q["q"], "filters": {},
            "expect": expect, "also_ok": also, "k": 10, "source": q["source"], "split": split,
            "reviewed_on": review["reviewed_on"],
        })
        report["kept"] += 1
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
