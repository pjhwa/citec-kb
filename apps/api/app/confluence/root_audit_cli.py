"""CLI: python -m app.confluence.root_audit_cli [--source-ids ...] [--depth N]

Audit confluence_map root coverage (one-off, read-only). Needs live
Confluence access (CONFLUENCE_BASE_URL/USERNAME/PASSWORD) and the sources
table, i.e. the api container — run it via scripts/audit_map_root_coverage.py.

For each partial-crawl source it lists folders under the space home whose
title matches 이슈|장애|트러블슈팅|SOP|KDB|케이스 스터디 and that are not a
registered root/explicit page. Review each by hand; add a real one with
POST /v1/confluence-map/sources/{source_id}/roots. Never add candidates
automatically.

--depth defaults to 1 (the space home's direct children). The gap that
prompted this audit sat five levels down, so a depth-1 pass would not have
found it: raise --depth (each level costs one request per folder).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# Spaces crawled whole from their home page — nothing to audit.
FULL_CRAWL_SPACES = {"LOOKIN", "TechRepo", "ServiceExcellenceTeam", "ICLOUDUT"}


async def _audit(source_ids: list[str] | None, depth: int) -> int:
    from app.confluence.client import ConfluenceClient, RateLimiter
    from app.confluence.map_sync import get_source_defs
    from app.confluence.root_audit import find_root_candidates
    from app.settings import get_settings

    defs = get_source_defs(active_only=True)
    if source_ids is None:
        source_ids = [i for i, d in defs.items() if d["space_key"] not in FULL_CRAWL_SPACES]
    client = ConfluenceClient(get_settings())
    limiter = RateLimiter(get_settings().confluence_rate_limit_rps)
    total = 0
    async with client.bulk_client() as http:

        async def children(page_id: str) -> list[dict]:
            results: list[dict] = []
            start = 0
            while True:
                data = await client._get_with_retry(
                    http,
                    f"/rest/api/content/{page_id}/child/page",
                    {"start": start, "limit": 100},
                    limiter=limiter,
                )
                batch = data.get("results") or []
                results.extend(batch)
                if len(batch) < 100:
                    return results
                start += len(batch)

        for sid in source_ids:
            sd = defs.get(sid)
            if not sd:
                print(f"[skip] unknown or disabled source: {sid}", file=sys.stderr)
                continue
            space = await client._get_with_retry(
                http, f"/rest/api/space/{sd['space_key']}", {"expand": "homepage"}, limiter=limiter
            )
            home = (space.get("homepage") or {}).get("id")
            if not home:
                print(f"[skip] {sid}: space has no homepage", file=sys.stderr)
                continue
            covered = set(sd["roots"]) | set(sd.get("explicit_pages") or {})
            found = await find_root_candidates(home, children, covered, max_depth=depth)
            print(f"\n== {sid} ({sd['space_key']}) home={home} roots={len(covered)}")
            if not found:
                print("   no uncovered incident-style folders")
            for c in found:
                print(f"   candidate {c['page_id']}  d{c['depth']}  {c['path']}")
            total += len(found)
    print(f"\n{total} candidate(s). Review by hand before adding any root.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--source-ids", help="comma list; default: every active partial-crawl source")
    ap.add_argument("--depth", type=int, default=1, help="levels below the space home (default 1)")
    a = ap.parse_args(argv)
    ids = [s.strip() for s in a.source_ids.split(",") if s.strip()] if a.source_ids else None
    return asyncio.run(_audit(ids, max(1, a.depth)))


if __name__ == "__main__":
    raise SystemExit(main())
