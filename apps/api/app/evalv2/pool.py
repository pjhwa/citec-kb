"""Candidate pooling: union of top-N results from several search settings."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("citec.evalv2.pool")

# (arm name, multi_query). Only settings the REST /v1/search exposes.
ARMS: tuple[tuple[str, bool], ...] = (("mq_on", True), ("mq_off", False))

SearchFn = Callable[[str, dict, bool, int], list[dict[str, Any]]]


def bare(eid: str | None) -> str:
    return (eid or "").split("#")[0]


def pool_candidates(
    search: SearchFn,
    q: str,
    filters: dict | None = None,
    *,
    arms: tuple[tuple[str, bool], ...] = ARMS,
    top_n: int = 10,
    max_pool: int = 15,
    extra: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    cands: dict[str, dict[str, Any]] = {}
    for name, mq in arms:
        try:
            results = search(q, filters or {}, mq, top_n)
        except Exception as exc:  # noqa: BLE001 — one failed arm must not drop the query
            logger.warning("pool arm %s failed for %r: %s", name, q[:40], exc)
            continue
        seen: set[str] = set()
        for rank, r in enumerate(results[:top_n], 1):
            eid = bare(r.get("external_id"))
            if not eid or eid in seen:
                continue
            seen.add(eid)
            c = cands.setdefault(eid, {
                "external_id": eid, "title": r.get("title") or "",
                "source_type": r.get("source_type"),
                "evidence_eligible": r.get("evidence_eligible"), "arms": {},
            })
            c["arms"][name] = rank
    seeds: dict[str, dict[str, Any]] = {}
    for e in extra or []:
        eid = bare(e.get("external_id"))
        if not eid:
            continue
        c = cands.setdefault(eid, {
            "external_id": eid, "title": e.get("title") or "", "source_type": e.get("source_type"),
            "evidence_eligible": e.get("evidence_eligible"), "arms": {},
        })
        c["arms"]["seed"] = 0
        seeds[eid] = c

    def order_key(c: dict[str, Any]) -> tuple[int, int]:
        ranks = [v for k, v in c["arms"].items() if k != "seed"]
        return (-len(ranks), min(ranks) if ranks else 99)

    ordered = sorted(cands.values(), key=order_key)
    rest = [c for c in ordered if c["external_id"] not in seeds]
    keep_ids = {c["external_id"] for c in rest[: max(0, max_pool - len(seeds))]} | set(seeds)
    return [c for c in ordered if c["external_id"] in keep_ids]


def rest_search(base: str, *, timeout: float = 120.0, attempts: int = 2) -> SearchFn:
    """Read-only POST /v1/search adapter (same request shape the gate harness uses)."""

    def _search(q: str, filters: dict, multi_query: bool, top_k: int) -> list[dict[str, Any]]:
        body = {"q": q, "top_k": top_k, "filters": {"status": "active", **(filters or {})}, "multi_query": multi_query}
        last: Exception | None = None
        for _ in range(attempts):
            try:
                req = urllib.request.Request(
                    base.rstrip("/") + "/v1/search", data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read()).get("results", [])
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise RuntimeError(f"search failed: {last}")

    return _search
