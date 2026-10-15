#!/usr/bin/env python3
"""F06(R05) 홀드아웃 baseline-A/비교군-B 측정 — dev와 운영을 같은 방법론으로
비교할 수 있도록, MCP 클라이언트 없이 REST API(POST /v1/search,
POST /v1/graph/explore)만 직접 호출한다. kb_search/kb_graph_explore(MCP)가
내부적으로 호출하는 것과 정확히 같은 엔드포인트·바디다.

**읽기 전용** — 아무것도 쓰지 않는다. 어느 환경(dev/운영)에 대고 돌려도 안전.

사용법:
    python3 scripts/verify/measure_f06_holdout.py \\
        --base-url http://localhost:8573 \\
        --holdout artifacts/citec-graph-final/20261014-f06-holdout-freeze/evaluation/holdout-2026-10-14.json \\
        --out /tmp/f06-measurement-prod.json

운영에서 돌리려면:
    1. holdout-2026-10-14.json 파일을 운영 서버로 복사(scp 등) — 이 파일은
       "어떤 질문에 어떤 정답 문서가 맞는지"를 고정해 둔 것으로, 쓰기 작업이
       전혀 없으니 운영에 올려도 안전하다.
    2. --base-url을 운영 API 주소로(예: http://localhost:8573, nginx를 거치려면
       http://localhost:8572) 바꿔서 그대로 실행.
    3. 결과 JSON을 dev에서 만든 것과 나란히 비교(diff 또는 육안)하면 된다 —
       같은 질문·같은 정답 기준·같은 측정 방법이라 환경 차이만 드러난다.

출력 JSON은 dev에서 쓴 baseline-cost-2026-10-14.json/comparison-B-cost-2026-10-14.json
과 같은 모양(aggregate 필드명 동일)이라 바로 비교 가능하다."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _post(base_url: str, path: str, body: dict[str, Any], *, timeout: float = 30.0) -> tuple[dict[str, Any] | None, float, int]:
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    except urllib.error.URLError as e:
        return None, (time.monotonic() - t0) * 1000, 0
    latency_ms = (time.monotonic() - t0) * 1000
    try:
        return json.loads(raw), latency_ms, status
    except json.JSONDecodeError:
        return None, latency_ms, status


def _search(base_url: str, question: str) -> tuple[list[dict[str, str]], float, int]:
    body = {"q": question, "top_k": 10, "filters": {"status": "active"}, "multi_query": False}
    data, latency_ms, status = _post(base_url, "/v1/search", body)
    # F05/F06 2026-10-15 실측으로 발견한 버그 — holdout의 candidate_documents[].document_id
    # 는 실제로는 bare external_id다(예: "19072632884"), 반면 /v1/search 응답의
    # "document_id" 필드는 "source_type:external_id" 합성 id("confluence_docs:623659946"
    # 같은)다. 처음엔 둘을 그대로 비교해서 항상 매칭 실패 → recall@10=0%가 나왔다
    # (원래 MCP 기반 baseline-A 측정은 kb_search의 텍스트 렌더링에 찍힌 external_id로
    # 비교해서 이 문제를 안 겪었다). 여기서는 명시적으로 external_id 필드로 비교한다.
    docs = [
        {"id": r["document_id"], "external_id": r.get("external_id")}
        for r in (data or {}).get("results", [])
    ] if data else []
    return docs, latency_ms, status


def _graph_explore(base_url: str, anchor_doc_id: str) -> tuple[list[dict[str, str]], float, int, str | None]:
    body = {"anchor_type": "document", "anchor_value": anchor_doc_id}
    data, latency_ms, status = _post(base_url, "/v1/graph/explore", body)
    if not data:
        return [], latency_ms, status, None
    docs = [
        {"id": d["id"], "external_id": d.get("external_id")}
        for d in (data.get("documents") or [])
    ]
    return docs, latency_ms, status, data.get("status")


def _rank_of(candidate_external_ids: set[str], merged_docs: list[dict[str, str]]) -> int | None:
    for i, doc in enumerate(merged_docs, start=1):
        if doc.get("external_id") in candidate_external_ids:
            return i
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="예: http://localhost:8573 (api 직접) 또는 http://localhost:8572 (web/nginx 경유)")
    parser.add_argument("--holdout", required=True, help="holdout-2026-10-14.json 경로")
    parser.add_argument("--out", required=True, help="결과를 저장할 경로")
    parser.add_argument("--skip-graph", action="store_true", help="baseline-A(검색만)만 측정하고 비교군 B(그래프 포함)는 생략")
    args = parser.parse_args()

    holdout = json.loads(Path(args.holdout).read_text(encoding="utf-8"))
    items = holdout["items"]

    per_item_a: list[dict[str, Any]] = []
    per_item_b: list[dict[str, Any]] = []
    latencies_a: list[float] = []
    latencies_b: list[float] = []

    for item in items:
        question = item["question"]
        # holdout의 "document_id" 필드는 이름과 달리 실제로는 bare external_id다
        # (F06 2026-10-14 홀드아웃 작성 당시 명명 — 지금 와서 바꾸면 동결 파일
        # 자체를 건드리게 되니 여기서는 읽는 쪽만 그 사실에 맞춘다).
        candidate_external_ids = {c["document_id"] for c in item["candidate_documents"]}

        search_docs, search_ms, search_status = _search(args.base_url, question)
        rank_a = _rank_of(candidate_external_ids, search_docs)
        per_item_a.append({
            "id": item["id"], "domain": item["domain"],
            "latency_ms": round(search_ms, 1), "http_status": search_status,
            "found_in_top10": rank_a is not None, "rank": rank_a,
        })
        latencies_a.append(search_ms)

        if args.skip_graph:
            continue

        total_ms = search_ms
        graph_docs: list[dict[str, str]] = []
        graph_status = None
        if search_docs:
            anchor = search_docs[0]["id"]
            graph_docs, graph_ms, http_status, graph_status = _graph_explore(args.base_url, anchor)
            total_ms += graph_ms
        else:
            anchor = None

        merged: list[dict[str, str]] = []
        seen = set()
        for doc in search_docs + graph_docs:
            if doc["id"] not in seen:
                seen.add(doc["id"])
                merged.append(doc)
        rank_b = _rank_of(candidate_external_ids, merged)

        if rank_a is not None:
            classification = "found_by_search_alone"
        elif rank_b is not None:
            classification = "found_by_graph_only"
        elif anchor is None:
            classification = "no_anchor_available"
        else:
            classification = "found_by_neither"

        per_item_b.append({
            "id": item["id"], "domain": item["domain"],
            "anchor_used": anchor, "graph_status": graph_status,
            "latency_ms": round(total_ms, 1), "tool_calls": 1 if anchor is None else 2,
            "found_in_merged": rank_b is not None, "rank": rank_b,
            "classification": classification,
        })
        latencies_b.append(total_ms)

    def _pct(vals: list[float], p: float) -> float | None:
        if not vals:
            return None
        s = sorted(vals)
        idx = min(len(s) - 1, int(round(p * (len(s) - 1))))
        return round(s[idx], 1)

    def _mrr(ranked_items: list[dict[str, Any]]) -> float:
        total = 0.0
        for it in ranked_items:
            r = it.get("rank")
            total += (1.0 / r) if r else 0.0
        return round(total / len(ranked_items), 4) if ranked_items else 0.0

    result: dict[str, Any] = {
        "base_url": args.base_url,
        "n_items": len(items),
        "group_a": {
            "recall_at_10": round(sum(1 for i in per_item_a if i["found_in_top10"]) / len(per_item_a), 4),
            "mrr": _mrr(per_item_a),
            "p50_latency_ms": _pct(latencies_a, 0.5),
            "p95_latency_ms": _pct(latencies_a, 0.95),
            "per_item": per_item_a,
        },
    }
    if not args.skip_graph:
        by_class: dict[str, int] = {}
        for it in per_item_b:
            by_class[it["classification"]] = by_class.get(it["classification"], 0) + 1
        result["group_b"] = {
            "recall_at_merged_set": round(sum(1 for i in per_item_b if i["found_in_merged"]) / len(per_item_b), 4),
            "mrr": _mrr(per_item_b),
            "p50_latency_ms": _pct(latencies_b, 0.5),
            "p95_latency_ms": _pct(latencies_b, 0.95),
            "classification_counts": by_class,
            "mean_tool_calls": round(sum(i["tool_calls"] for i in per_item_b) / len(per_item_b), 2),
            "per_item": per_item_b,
        }

    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"그룹 A: recall@10={result['group_a']['recall_at_10']:.1%} mrr={result['group_a']['mrr']} "
          f"p50={result['group_a']['p50_latency_ms']}ms p95={result['group_a']['p95_latency_ms']}ms")
    if not args.skip_graph:
        print(f"그룹 B: recall@merged={result['group_b']['recall_at_merged_set']:.1%} mrr={result['group_b']['mrr']} "
              f"p50={result['group_b']['p50_latency_ms']}ms p95={result['group_b']['p95_latency_ms']}ms "
              f"분류={result['group_b']['classification_counts']}")
    print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
