#!/usr/bin/env python3
"""Lightweight concurrent load smoke against search + health.

Usage:
  python scripts/load_smoke.py --n 30 --concurrency 6
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def _post(url: str, body: dict, timeout: float = 60.0) -> tuple[int, float, str]:
    t0 = time.time()
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (time.time() - t0) * 1000, raw.decode()[:80]
    except urllib.error.HTTPError as exc:
        return exc.code, (time.time() - t0) * 1000, str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0, (time.time() - t0) * 1000, str(exc)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8573")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument(
        "--multi-query",
        action="store_true",
        help="Enable multi-query hybrid (heavier; default off for load smoke)",
    )
    p.add_argument(
        "--p95-max-ms",
        type=float,
        default=15000,
        help="Fail if p95 latency exceeds this (default 15000ms)",
    )
    args = p.parse_args()
    queries = [
        "CITECTS-2502",
        "모니모 Redis",
        "GRO offload",
        "지난 주 지원건",
        "Linux hang",
    ]
    url = args.base.rstrip("/") + "/v1/search"
    jobs = []
    for i in range(args.n):
        q = queries[i % len(queries)]
        jobs.append({"q": q, "top_k": 3, "multi_query": bool(args.multi_query)})

    latencies = []
    ok = 0
    fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(_post, url, body) for body in jobs]
        for f in as_completed(futs):
            status, ms, _ = f.result()
            latencies.append(ms)
            if 200 <= status < 300:
                ok += 1
            else:
                fail += 1
    wall = (time.time() - t0) * 1000
    latencies.sort()
    p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0
    report = {
        "n": args.n,
        "concurrency": args.concurrency,
        "ok": ok,
        "fail": fail,
        "wall_ms": round(wall, 1),
        "rps": round(args.n / (wall / 1000), 2) if wall else 0,
        "latency_ms": {
            "p50": round(statistics.median(latencies), 1) if latencies else 0,
            "p95": round(p95, 1),
            "mean": round(statistics.mean(latencies), 1) if latencies else 0,
            "max": round(max(latencies), 1) if latencies else 0,
        },
        "multi_query": bool(args.multi_query),
        "p95_max_ms": args.p95_max_ms,
        "pass": fail == 0 and p95 <= args.p95_max_ms,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
