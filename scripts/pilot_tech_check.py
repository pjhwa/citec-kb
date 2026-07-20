#!/usr/bin/env python3
"""Automated engineering checks for Phase 2 pilot scenarios (A/C partial).

Does not replace domain sign-off. Exit 0 if all automated checks pass.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8573"


def get(path: str) -> Any:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def post(path: str, body: dict) -> Any:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def main() -> None:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(("OK  " if ok else "FAIL") + f" {name}" + (f" — {detail}" if detail else ""))

    ops = get("/v1/ops/status")
    check("ops.status", ops.get("status") == "ok", str(ops.get("status")))
    check("ops.pilot_engineering_ready", bool(ops.get("pilot_engineering_ready")))
    check("worker.heartbeat", bool((ops.get("checks") or {}).get("worker", {}).get("ok")))

    # A1 search
    s = post("/v1/search", {"q": "CITECTS-2502", "top_k": 3, "multi_query": True})
    top = (s.get("results") or [{}])[0].get("external_id")
    check("A1 search CITECTS-2502 top1", top == "CITECTS-2502", str(top))

    # A3 monimo
    s2 = post("/v1/search", {"q": "모니모 Redis", "top_k": 5})
    ids = [r.get("external_id") for r in (s2.get("results") or [])]
    check("A3 monimo redis hits", len(ids) > 0, ",".join(ids[:3]))

    # route intents
    r = post("/v1/query/route", {"q": "지난 주 지원건"})
    check("route list", r.get("intent") == "time_scoped_list", r.get("intent"))
    r = post("/v1/query/route", {"q": "2주 Linux 대수"})
    check("route capacity", r.get("intent") == "capacity", r.get("intent"))
    r = post("/v1/query/route", {"q": "연도별 지원 건수"})
    check("route analytics", r.get("intent") == "analytics", r.get("intent"))

    # SI C1 shape
    si = post("/v1/similar-incident", {"symptom": "모니모 Redis 타임아웃", "top_k": 2})
    cases = si.get("cases") or []
    check("C1 SI cases", len(cases) >= 1, str(len(cases)))
    if cases:
        check(
            "C1 SI has applicability",
            bool((cases[0].get("applicability") or {}).get("label")),
            str((cases[0].get("applicability") or {}).get("label")),
        )

    # jobs ping
    job = post("/v1/jobs", {"type": "ping"})
    import time

    jid = job["id"]
    st = "queued"
    for _ in range(20):
        st = get(f"/v1/jobs/{jid}").get("status")
        if st in {"done", "failed"}:
            break
        time.sleep(0.5)
    check("job ping done", st == "done", st)

    # seeds
    check("entities>=1", get("/v1/entities/")["total"] >= 1)
    check("lexicon>=1", get("/v1/lexicon/terms")["total"] >= 1)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nsummary pass={len(results)-len(failed)}/{len(results)}")
    raise SystemExit(0 if not failed else 1)


if __name__ == "__main__":
    main()
