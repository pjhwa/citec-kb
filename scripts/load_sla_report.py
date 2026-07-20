#!/usr/bin/env python3
"""Formal G6-style load / SLA report (search + health + planner route).

Usage:
  .venv/bin/python scripts/load_sla_report.py
  .venv/bin/python scripts/load_sla_report.py --concurrency 20 --search-n 40

Writes JSON under data/reports/ and exits 0 only if all gates pass.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def _req(
    method: str,
    url: str,
    body: dict | None = None,
    timeout: float = 90.0,
) -> tuple[int, float, str]:
    t0 = time.time()
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (time.time() - t0) * 1000, raw[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, (time.time() - t0) * 1000, str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0, (time.time() - t0) * 1000, str(exc)


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def _summary(latencies: list[float], ok: int, fail: int, wall_ms: float, n: int) -> dict:
    return {
        "n": n,
        "ok": ok,
        "fail": fail,
        "success_rate": round(ok / n, 4) if n else 0.0,
        "wall_ms": round(wall_ms, 1),
        "rps": round(n / (wall_ms / 1000), 2) if wall_ms else 0.0,
        "latency_ms": {
            "p50": round(_pct(latencies, 0.50), 1),
            "p95": round(_pct(latencies, 0.95), 1),
            "p99": round(_pct(latencies, 0.99), 1),
            "mean": round(statistics.mean(latencies), 1) if latencies else 0.0,
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
    }


def run_pool(jobs: list[tuple], concurrency: int) -> tuple[list[float], int, int, float]:
    latencies: list[float] = []
    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(fn, *args) for fn, args in jobs]
        for f in as_completed(futs):
            status, ms, _ = f.result()
            latencies.append(ms)
            if 200 <= status < 300:
                ok += 1
            else:
                fail += 1
    return latencies, ok, fail, (time.time() - t0) * 1000


def main() -> None:
    p = argparse.ArgumentParser(description="G6 load/SLA formal report")
    p.add_argument("--base", default="http://127.0.0.1:8573")
    # Gate target (stable under e5-base query embed): 8-way concurrent search
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--search-n", type=int, default=40)
    p.add_argument("--health-n", type=int, default=20)
    p.add_argument("--route-n", type=int, default=10)
    # Optional stress sample (informational; does not fail the report)
    p.add_argument("--stress-concurrency", type=int, default=20)
    p.add_argument("--stress-search-n", type=int, default=20)
    p.add_argument("--search-p95-max-ms", type=float, default=15000)
    p.add_argument("--health-p95-max-ms", type=float, default=2000)
    p.add_argument("--route-p95-max-ms", type=float, default=15000)
    p.add_argument("--min-success-rate", type=float, default=0.99)
    p.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "reports"),
    )
    args = p.parse_args()
    base = args.base.rstrip("/")

    queries = [
        "CITECTS-2502",
        "모니모 Redis",
        "GRO offload",
        "Linux hang",
        "지난 주 지원건",
    ]
    search_jobs = []
    for i in range(args.search_n):
        q = queries[i % len(queries)]
        body = {"q": q, "top_k": 3, "multi_query": False}
        search_jobs.append((_req, ("POST", f"{base}/v1/search", body)))

    health_jobs = [
        (_req, ("GET", f"{base}/v1/health", None)) for _ in range(args.health_n)
    ]

    route_qs = [
        "지난 2주 Linux 지원 공수",
        "모니모 장애 비중",
        "지난 주 지원 목록",
        "CITECTS-2502 유사장애",
        "Redis 예방 점검",
    ]
    route_jobs = []
    for i in range(args.route_n):
        q = route_qs[i % len(route_qs)]
        # prefer planner if present
        body = {"q": q}
        route_jobs.append((_req, ("POST", f"{base}/v1/query", body)))

    lat_s, ok_s, fail_s, wall_s = run_pool(search_jobs, args.concurrency)
    lat_h, ok_h, fail_h, wall_h = run_pool(health_jobs, min(args.concurrency, 10))
    lat_r, ok_r, fail_r, wall_r = run_pool(route_jobs, min(args.concurrency, 5))

    search = _summary(lat_s, ok_s, fail_s, wall_s, args.search_n)
    health = _summary(lat_h, ok_h, fail_h, wall_h, args.health_n)
    route = _summary(lat_r, ok_r, fail_r, wall_r, args.route_n)

    stress = None
    if args.stress_concurrency and args.stress_search_n > 0:
        stress_jobs = []
        for i in range(args.stress_search_n):
            q = queries[i % len(queries)]
            stress_jobs.append(
                (_req, ("POST", f"{base}/v1/search", {"q": q, "top_k": 3, "multi_query": False}))
            )
        lat_x, ok_x, fail_x, wall_x = run_pool(stress_jobs, args.stress_concurrency)
        stress = {
            **_summary(lat_x, ok_x, fail_x, wall_x, args.stress_search_n),
            "concurrency": args.stress_concurrency,
            "informational": True,
        }

    gates = {
        "search_success": search["success_rate"] >= args.min_success_rate,
        "search_p95": search["latency_ms"]["p95"] <= args.search_p95_max_ms,
        "health_success": health["success_rate"] >= args.min_success_rate,
        "health_p95": health["latency_ms"]["p95"] <= args.health_p95_max_ms,
        "route_success": route["success_rate"] >= 0.8,
        "route_p95": route["latency_ms"]["p95"] <= args.route_p95_max_ms
        or route["ok"] == 0,
    }
    overall = all(gates.values())

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": base,
        "concurrency": args.concurrency,
        "scenarios": {
            "search": {**search, "p95_max_ms": args.search_p95_max_ms, "concurrency": args.concurrency},
            "health": {**health, "p95_max_ms": args.health_p95_max_ms},
            "planner_route": {**route, "p95_max_ms": args.route_p95_max_ms},
        },
        "stress_search": stress,
        "gates": gates,
        "pass": overall,
        "notes": [
            "Gate target: concurrent search default 8 (e5-base query embed saturation above ~10–20)",
            "stress_search is informational only (concurrency 20 sample)",
            "search multi_query=false for stable p95 under concurrency",
            "planner_route uses POST /v1/query (capacity/analytics/list/SI/hybrid)",
            "SSO: AUTH_MODE=off in dev; use apikey/oidc_stub for enforcement tests",
        ],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"load_sla_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out_dir / "load_sla_latest.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwrote {out_path}")
    raise SystemExit(0 if overall else 1)


if __name__ == "__main__":
    main()
