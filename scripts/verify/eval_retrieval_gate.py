#!/usr/bin/env python3
"""검색·도구 개선(B 단계)의 머지 게이트 하네스 — REST만 호출하는 **읽기 전용** 측정.

두 가지를 한 번에 잰다.
  1) 동결 홀드아웃(30문항)  — recall@5/10, MRR, 상위 5의 pointer-only 비율, 지연, 응답 크기.
     multi_query on/off 둘 다(MCP kb_search 기본은 on, measure_f06_holdout.py 는 off).
  2) 추가 평가셋(evalset JSON) — 군(group)별 hit@k + 불변식(invariant) 검사:
       search            정답 external_id 가 상위 k 안에 드는가
       similar_incident  /v1/similar-incident 상위 k
       missing_identifier / present_identifier   없는/있는 문서 ID 질의의 경고·trust
       inv_entity_share  matched <= total, kb_analytics(entity=) 와 matched 일치
       inv_query_period  /v1/query 가 기간 목록으로 분기하고 건수가 /v1/tickets 와 같은가
       inv_query_entity_count  "작년 SCP 건수" 류가 /v1/analytics/tickets 와 같은 수인가
       tickets_relative / recent_changes   스킬 스모크의 서버 쪽 대응(200 + 건수)
       probe             정답 없이 상위 5의 pointer-only 비율·지연만 측정(B2)

사용법
  # 측정 (dev 에 대해, 변경 전 기준선)
  python3 scripts/verify/eval_retrieval_gate.py run \\
      --base-url http://localhost:8573 \\
      --holdout artifacts/citec-graph-final/20261014-f06-holdout-freeze/evaluation/holdout-2026-10-14.json \\
      --evalset artifacts/citec-kb-retrieval-fix-20261028/evalset-2026-10-28.json \\
      --label baseline-A --out artifacts/citec-kb-retrieval-fix-20261028/baseline-A.json

  # 변경 전/후 비교 — 게이트 판정(종료코드 1 = 머지 금지)
  python3 scripts/verify/eval_retrieval_gate.py compare before.json after.json

게이트(compare): 홀드아웃 recall@10 이 multi_query on/off 어느 쪽에서든 하락하면 실패.
이전에 통과하던 evalset 불변식·정답 케이스가 실패로 바뀌면 "회귀"로 표시하고 실패.
(개선은 표로만 보여주고 통과/실패 판정에 쓰지 않는다 — "개선됨"은 수치로 말한다.)

정답 ID 를 코드에 하드코딩하지 않는다 — 모두 evalset/holdout 파일에 있다."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

TIMEOUT = 90.0


# ── HTTP ────────────────────────────────────────────────────────────────
def _http(method: str, base: str, path: str, *, body: Optional[dict] = None,
          params: Optional[dict] = None) -> tuple[Any, float, int, int]:
    """(json|None, latency_ms, status, response_bytes)"""
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, (time.monotonic() - t0) * 1000, 0, 0
    ms = (time.monotonic() - t0) * 1000
    try:
        return json.loads(raw), ms, status, len(raw)
    except json.JSONDecodeError:
        return None, ms, status, len(raw)


def _search(base: str, q: str, *, filters: Optional[dict] = None, multi_query: bool = True,
            top_k: int = 10) -> tuple[Any, float, int, int]:
    f = {"status": "active"}
    f.update(filters or {})
    return _http("POST", base, "/v1/search", body={"q": q, "top_k": top_k, "filters": f, "multi_query": multi_query})


def _eids(data: Any) -> list[str]:
    return [r.get("external_id") or "" for r in (data or {}).get("results", [])]


def _rank(expect: set[str], eids: list[str]) -> Optional[int]:
    for i, e in enumerate(eids, 1):
        if e in expect or e.split("#")[0] in expect:
            return i
    return None


def _pointer_ratio_top5(data: Any) -> Optional[float]:
    res = (data or {}).get("results", [])[:5]
    if not res:
        return None
    return sum(1 for r in res if r.get("evidence_eligible") is False) / len(res)


# ── 집계 ────────────────────────────────────────────────────────────────
def _pct(vals: list[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 1)


def _summarize(rows: list[dict]) -> dict:
    n = len(rows)
    ranks = [r["rank"] for r in rows]
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    ptr = [r["pointer_top5"] for r in rows if r.get("pointer_top5") is not None]
    size = [r["bytes"] for r in rows if r.get("bytes")]
    return {
        "n": n,
        "hit_at_3": round(sum(1 for r in ranks if r and r <= 3) / n, 4) if n else None,
        "recall_at_5": round(sum(1 for r in ranks if r and r <= 5) / n, 4) if n else None,
        "recall_at_10": round(sum(1 for r in ranks if r and r <= 10) / n, 4) if n else None,
        "mrr": round(sum((1.0 / r) if r else 0.0 for r in ranks) / n, 4) if n else None,
        "pointer_only_top5_ratio": round(statistics.mean(ptr), 4) if ptr else None,
        "p50_latency_ms": _pct(lat, 0.5),
        "p95_latency_ms": _pct(lat, 0.95),
        "median_bytes": int(statistics.median(size)) if size else None,
        "errors": sum(1 for r in rows if r.get("status") != 200),
    }


# ── 홀드아웃 ────────────────────────────────────────────────────────────
def run_holdout(base: str, path: Path, multi_query: bool) -> dict:
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    rows = []
    for it in items:
        expect = {c["document_id"] for c in it["candidate_documents"]}  # 실제로는 bare external_id
        data, ms, status, nbytes = _search(base, it["question"], multi_query=multi_query)
        rows.append({"id": it["id"], "domain": it["domain"], "rank": _rank(expect, _eids(data)),
                     "latency_ms": round(ms, 1), "status": status, "bytes": nbytes,
                     "pointer_top5": _pointer_ratio_top5(data)})
    by_domain: dict[str, list[dict]] = {}
    for r in rows:
        by_domain.setdefault(r["domain"], []).append(r)
    return {"summary": _summarize(rows),
            "by_domain": {d: _summarize(v) for d, v in sorted(by_domain.items())},
            "per_item": rows}


# ── evalset ─────────────────────────────────────────────────────────────
def _case_search(base: str, c: dict) -> dict:
    data, ms, status, nbytes = _search(base, c["q"], filters=c.get("filters"),
                                       multi_query=c.get("multi_query", True), top_k=c.get("top_k", 10))
    eids = _eids(data)
    rank = _rank(set(c["expect"]), eids)
    k = c.get("k", 10)
    forbid = set(c.get("forbid_above", []))
    forbid_rank = _rank(forbid, eids) if forbid else None
    ok = rank is not None and rank <= k and (forbid_rank is None or (rank < forbid_rank))
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "rank": rank, "pass": ok, "k": k,
            "top": eids[:5], "forbid_rank": forbid_rank, "latency_ms": round(ms, 1),
            "status": status, "bytes": nbytes, "pointer_top5": _pointer_ratio_top5(data)}


def _case_similar(base: str, c: dict) -> dict:
    body = {"symptom": c["q"], "top_k": c.get("top_k", 5)}
    for k in ("product", "environment", "service"):
        if c.get(k):
            body[k] = c[k]
    data, ms, status, nbytes = _http("POST", base, "/v1/similar-incident", body=body)
    eids = [x.get("external_id") or "" for x in (data or {}).get("cases", [])]
    rank = _rank(set(c["expect"]), eids)
    k = c.get("k", 5)
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "rank": rank,
            "pass": rank is not None and rank <= k, "k": k, "top": eids[:5],
            "latency_ms": round(ms, 1), "status": status, "bytes": nbytes, "pointer_top5": None}


def _case_identifier(base: str, c: dict) -> dict:
    data, ms, status, nbytes = _search(base, c["q"], multi_query=c.get("multi_query", True), top_k=5)
    missing = (data or {}).get("missing_identifiers")
    trust = (data or {}).get("trust_retrieval")
    if c["kind"] == "missing_identifier":
        ok = bool(missing) and all(m.lower() in {x.lower() for x in missing} for m in c["expect_missing"]) \
            and trust in {"weak", "empty"}
    else:  # present_identifier
        ok = missing == [] and _rank(set(c["expect"]), _eids(data)) is not None \
            and (_rank(set(c["expect"]), _eids(data)) or 99) <= c.get("k", 3)
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "pass": ok, "rank": None,
            "missing_identifiers": missing, "trust": trust, "top": _eids(data)[:3],
            "latency_ms": round(ms, 1), "status": status, "bytes": nbytes, "pointer_top5": None}


def _case_inv_entity_share(base: str, c: dict) -> dict:
    es, ms, status, nbytes = _http("GET", base, "/v1/analytics/entity_share",
                                   params={"entity": c["entity"], "relative": c["relative"]})
    an, _, st2, _ = _http("GET", base, "/v1/analytics/tickets",
                          params={"group_by": "total", "entity": c["entity"], "relative": c["relative"]})
    es = es or {}
    detail = {"matched": es.get("matched"), "total": es.get("total"), "share": es.get("share"),
              "method": es.get("method"), "analytics_total": (an or {}).get("total")}
    ok = (status == 200 and st2 == 200 and es.get("matched") is not None
          and es["matched"] <= es["total"] and es["matched"] == (an or {}).get("total"))
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "pass": ok, "rank": None,
            "detail": detail, "latency_ms": round(ms, 1), "status": status, "bytes": nbytes, "pointer_top5": None}


def _case_inv_query_period(base: str, c: dict) -> dict:
    q, ms, status, nbytes = _http("POST", base, "/v1/query", body={"q": c["q"], "include_search": False})
    # The planner may narrow by an explicit Component word ("기술지원"); the
    # reference count must apply the same component or it compares different sets.
    comp = ((q or {}).get("params") or {}).get("component")
    tparams = {"source_type": c["source_type"], "date_from": c["date_from"],
               "date_to": c["date_to"], "limit": 1}
    if comp:
        tparams["component"] = comp
    tk, _, _, _ = _http("GET", base, "/v1/tickets", params=tparams)
    intent = (q or {}).get("intent")
    total = ((q or {}).get("result") or {}).get("total")
    expected = (tk or {}).get("total")
    detail = {"intent": intent, "query_total": total, "tickets_total": expected, "component": comp}
    ok = intent == "time_scoped_list" and total is not None and total == expected
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "pass": ok, "rank": None,
            "detail": detail, "latency_ms": round(ms, 1), "status": status, "bytes": nbytes, "pointer_top5": None}


def _case_inv_query_entity_count(base: str, c: dict) -> dict:
    q, ms, status, nbytes = _http("POST", base, "/v1/query", body={"q": c["q"], "include_search": False})
    an, _, _, _ = _http("GET", base, "/v1/analytics/tickets",
                        params={"group_by": "total", "entity": c["entity"], "relative": c["relative"]})
    res = (q or {}).get("result") or {}
    # entity_share 모드면 분자(matched), 집계 모드면 total 이 "그 엔티티의 건수"다.
    got = res["matched"] if "matched" in res else res.get("total")
    want = (an or {}).get("total")
    detail = {"intent": (q or {}).get("intent"), "reported_count": got, "analytics_total": want,
              "result_keys": sorted(res.keys())[:8]}
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "pass": got is not None and got == want,
            "rank": None, "detail": detail, "latency_ms": round(ms, 1), "status": status, "bytes": nbytes,
            "pointer_top5": None}


def _case_tickets_relative(base: str, c: dict) -> dict:
    d, ms, status, nbytes = _http("GET", base, "/v1/tickets",
                                  params={"relative": c["relative"], "source_type": "support_history", "limit": 5})
    total = (d or {}).get("total")
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "pass": status == 200 and (total or 0) >= c.get("min_total", 1),
            "rank": None, "detail": {"total": total}, "latency_ms": round(ms, 1), "status": status,
            "bytes": nbytes, "pointer_top5": None}


def _case_recent_changes(base: str, c: dict) -> dict:
    d, ms, status, nbytes = _http("GET", base, "/v1/documents/recent-changes",
                                  params={"relative": c["relative"], "space_key": c.get("space_key"), "limit": 5})
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "pass": status == 200 and "total" in (d or {}),
            "rank": None, "detail": {"total": (d or {}).get("total")}, "latency_ms": round(ms, 1),
            "status": status, "bytes": nbytes, "pointer_top5": None}


def _case_probe(base: str, c: dict) -> dict:
    """정답 없이 상위 5의 pointer-only 비율·지연만 잰다(B2 점유 측정용). 항상 pass."""
    data, ms, status, nbytes = _search(base, c["q"], filters=c.get("filters"),
                                       multi_query=c.get("multi_query", True), top_k=c.get("top_k", 5))
    types = [r.get("source_type") for r in (data or {}).get("results", [])[:5]]
    return {"id": c["id"], "group": c["group"], "kind": c["kind"], "pass": status == 200, "rank": None,
            "top_source_types": types, "top": _eids(data)[:5], "latency_ms": round(ms, 1),
            "status": status, "bytes": nbytes, "pointer_top5": _pointer_ratio_top5(data)}


_HANDLERS = {
    "probe": _case_probe,
    "search": _case_search,
    "similar_incident": _case_similar,
    "missing_identifier": _case_identifier,
    "present_identifier": _case_identifier,
    "inv_entity_share": _case_inv_entity_share,
    "inv_query_period": _case_inv_query_period,
    "inv_query_entity_count": _case_inv_query_entity_count,
    "tickets_relative": _case_tickets_relative,
    "recent_changes": _case_recent_changes,
}


def run_evalset(base: str, path: Path, only_groups: Optional[set[str]] = None) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    cases = [c for c in spec["cases"] if not only_groups or c["group"] in only_groups]
    rows = [_HANDLERS[c["kind"]](base, c) for c in cases]
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["group"], []).append(r)
    gsum = {}
    for g, v in sorted(groups.items()):
        s = _summarize([x for x in v])
        s["passed"] = sum(1 for x in v if x["pass"])
        s["cases"] = len(v)
        gsum[g] = s
    return {"evalset_id": spec.get("evalset_id"), "groups": gsum, "cases": rows}


# ── 실행 / 비교 ─────────────────────────────────────────────────────────
def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def cmd_run(a: argparse.Namespace) -> int:
    out: dict[str, Any] = {
        "label": a.label, "base_url": a.base_url, "git_sha": _git_sha(),
        "measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    api_build, _, _, _ = _http("GET", a.base_url, "/v1/health")
    out["api_health"] = {k: (api_build or {}).get(k) for k in ("status", "api_build_sha") if api_build}
    if a.holdout and not a.only_groups:
        out["holdout"] = {}
        for mq in (True, False):
            key = "multi_query_on" if mq else "multi_query_off"
            out["holdout"][key] = run_holdout(a.base_url, Path(a.holdout), mq)
            s = out["holdout"][key]["summary"]
            print(f"[holdout {key}] recall@5={s['recall_at_5']:.1%} recall@10={s['recall_at_10']:.1%} "
                  f"mrr={s['mrr']} pointer_top5={s['pointer_only_top5_ratio']} "
                  f"p50={s['p50_latency_ms']}ms p95={s['p95_latency_ms']}ms bytes~{s['median_bytes']} errors={s['errors']}")
    if a.evalset:
        only = set(a.only_groups.split(",")) if a.only_groups else None
        out["evalset"] = run_evalset(a.base_url, Path(a.evalset), only)
        if only and Path(a.out).exists():
            # 일부 군만 다시 쟀으면 기존 결과 파일에 그 군만 덮어써 합친다(전체 재측정 18분 절약).
            prev = json.loads(Path(a.out).read_text(encoding="utf-8"))
            ev = prev.get("evalset") or {"groups": {}, "cases": []}
            ev["cases"] = [c for c in ev["cases"] if c["group"] not in only] + out["evalset"]["cases"]
            ev["groups"].update(out["evalset"]["groups"])
            prev["evalset"] = ev
            prev.setdefault("merged_groups", []).append({"groups": sorted(only), "git_sha": out["git_sha"],
                                                         "measured_at_utc": out["measured_at_utc"]})
            out = prev
        for g, s in out["evalset"]["groups"].items():
            print(f"[evalset {g}] pass {s['passed']}/{s['cases']} hit@3={s['hit_at_3']} p50={s['p50_latency_ms']}ms")
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {a.out}")
    return 0


def _fmt(v: Any) -> str:
    return "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def cmd_compare(a: argparse.Namespace) -> int:
    before = json.loads(Path(a.before).read_text(encoding="utf-8"))
    after = json.loads(Path(a.after).read_text(encoding="utf-8"))
    print(f"before: {before.get('label')} @{before.get('git_sha')}   after: {after.get('label')} @{after.get('git_sha')}\n")
    failed: list[str] = []

    print("== 홀드아웃 ==")
    print(f"{'모드':<18}{'지표':<26}{'before':>9}{'after':>9}{'Δ':>9}")
    for mode in ("multi_query_on", "multi_query_off"):
        b = (before.get("holdout") or {}).get(mode, {}).get("summary")
        c = (after.get("holdout") or {}).get(mode, {}).get("summary")
        if not b or not c:
            continue
        for m in ("recall_at_5", "recall_at_10", "mrr", "pointer_only_top5_ratio", "p50_latency_ms", "p95_latency_ms", "median_bytes"):
            bv, cv = b.get(m), c.get(m)
            d = (cv - bv) if isinstance(bv, (int, float)) and isinstance(cv, (int, float)) else None
            print(f"{mode:<18}{m:<26}{_fmt(bv):>9}{_fmt(cv):>9}{_fmt(d):>9}")
        if (c.get("recall_at_10") or 0) < (b.get("recall_at_10") or 0):
            failed.append(f"홀드아웃 recall@10 하락 ({mode}: {b['recall_at_10']} → {c['recall_at_10']})")
        # 도메인별 악화 표시(판정에는 쓰지 않고 보고용)
        bd = before["holdout"][mode].get("by_domain", {})
        cd = after["holdout"][mode].get("by_domain", {})
        worse = [d for d in cd if d in bd and (cd[d]["recall_at_10"] or 0) < (bd[d]["recall_at_10"] or 0)]
        if worse:
            print(f"  ↳ recall@10 이 낮아진 도메인({mode}): {', '.join(worse)}")

    print("\n== evalset (군별 통과 수) ==")
    bg = (before.get("evalset") or {}).get("groups", {})
    cg = (after.get("evalset") or {}).get("groups", {})
    print(f"{'군':<28}{'before':>10}{'after':>10}")
    for g in sorted(set(bg) | set(cg)):
        bs = f"{bg[g]['passed']}/{bg[g]['cases']}" if g in bg else "-"
        cs = f"{cg[g]['passed']}/{cg[g]['cases']}" if g in cg else "-"
        print(f"{g:<28}{bs:>10}{cs:>10}")

    bc = {c["id"]: c for c in (before.get("evalset") or {}).get("cases", [])}
    cc = {c["id"]: c for c in (after.get("evalset") or {}).get("cases", [])}
    reg = [i for i in cc if i in bc and bc[i]["pass"] and not cc[i]["pass"]]
    fixed = [i for i in cc if i in bc and not bc[i]["pass"] and cc[i]["pass"]]
    if fixed:
        print(f"\n새로 통과: {', '.join(fixed)}")
    if reg:
        print(f"\n회귀(통과 → 실패): {', '.join(reg)}")
        failed.append("evalset 회귀: " + ", ".join(reg))
    for i in cc:
        if i in bc and bc[i].get("rank") != cc[i].get("rank") and (bc[i].get("rank") or cc[i].get("rank")):
            print(f"  rank {i}: {bc[i].get('rank')} → {cc[i].get('rank')}")

    print()
    if failed:
        print("게이트 실패 — 머지 금지:\n  - " + "\n  - ".join(failed))
        return 1
    print("게이트 통과(회귀 없음). 개선 여부는 위 수치로만 판단한다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base-url", required=True)
    r.add_argument("--holdout")
    r.add_argument("--evalset")
    r.add_argument("--only-groups", help="쉼표로 구분한 군만 측정해 기존 --out 파일에 병합(홀드아웃은 건너뜀)")
    r.add_argument("--label", required=True)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.set_defaults(fn=cmd_compare)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
