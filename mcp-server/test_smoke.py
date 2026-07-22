#!/usr/bin/env python3
"""Logic smoke tests against a live citec-kb API (no full MCP protocol).

  CITEC_KB_BASE_URL=http://localhost:8573 python3 test_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import server  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS: {name}")
    else:
        FAIL += 1
        print(f"FAIL: {name} — {detail[:300]}")


async def main() -> int:
    base = os.environ.get("CITEC_KB_BASE_URL", "http://localhost:8573")
    server.CITEC_KB_BASE_URL = base
    print(f"base={base}")

    h = await server.kb_health()
    check("kb_health", not h.startswith("오류:"), h)

    st = await server.kb_stats()
    check("kb_stats", not st.startswith("오류:") and "total_documents" in st, st)

    sr = await server.kb_search("Redis", section="support_history", limit=3)
    check("kb_search", not sr.startswith("오류:"), sr)
    check("kb_search has results or empty msg", "검색 결과" in sr or "없습니다" in sr, sr)

    path_line = next((l for l in sr.splitlines() if l.strip().startswith("path:")), "")
    if path_line:
        path = path_line.split("path:", 1)[1].strip()
        doc = await server.kb_get_document(path)
        check("kb_get_document", not doc.startswith("오류:") and len(doc) > 10, doc[:100])

    alias = await server.wiki_search("Redis", limit=2)
    check("wiki_search alias", not alias.startswith("오류:"), alias)

    listing = await server.kb_list_insights(limit=3)
    check("kb_list_insights", not listing.startswith("오류:"), listing)

    t = await server.kb_ticket("CITECTS-2502")
    check("kb_ticket", not t.startswith("오류:") or "찾을 수 없" in t, t[:120])

    q = await server.kb_query("연도별 지원 건수", top_k=5)
    check("kb_query", not q.startswith("오류:") and "intent=" in q, q[:200])

    lt = await server.kb_list_tickets(relative="올해", limit=5)
    check("kb_list_tickets", not lt.startswith("오류:") and "tickets" in lt, lt[:200])

    an = await server.kb_analytics(group_by="year", top_k=5)
    check("kb_analytics", not an.startswith("오류:") and "analytics" in an, an[:200])

    si = await server.kb_similar_incident("Redis timeout", top_k=2)
    check("kb_similar_incident", not si.startswith("오류:"), si[:200])

    ci = await server.kb_list_checkitems(q="OOM", area="Linux", limit=5)
    check("kb_list_checkitems", not ci.startswith("오류:"), ci[:200])

    help_t = await server.kb_tools_help()
    check("kb_tools_help", "kb_list_tickets" in help_t, help_t[:100])

    # connection failure
    server.CITEC_KB_BASE_URL = "http://127.0.0.1:1"
    fail = await server.kb_search("x")
    check("down backend → 오류", fail.startswith("오류:"), fail)
    server.CITEC_KB_BASE_URL = base

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
