"""Find Confluence folders a partial-crawl map source may be missing.

confluence_map crawls only the `roots` (and `explicit_pages`) registered per
space, so a troubleshooting/issue folder created after those were chosen —
e.g. under a team reorganisation — is silently outside the index (this is
what hid the Neutron runbooks: 1,141 matching pages existed, none under a
registered root). This walks a space from its home page and lists folders
whose title looks incident-related and that no registered root covers.

Candidates are for a person to review. Nothing here adds a root: a wrong
guess would sweep personal workspaces into the index.

Pure logic only; the Confluence calls are injected (see
scripts/audit_map_root_coverage.py).
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Iterable

CANDIDATE_TITLE = re.compile(r"이슈|장애|트러블슈팅|SOP|KDB|케이스\s*스터디", re.IGNORECASE)

ListChildren = Callable[[str], Awaitable[list[dict[str, Any]]]]


async def find_root_candidates(
    home_id: str,
    list_children: ListChildren,
    covered_ids: Iterable[str],
    *,
    max_depth: int = 1,
    title_pattern: re.Pattern[str] = CANDIDATE_TITLE,
) -> list[dict[str, Any]]:
    """Breadth-first from the space home, `max_depth` levels down.

    A page in covered_ids (a registered root or explicit page) is neither
    reported nor descended into — its subtree is already crawled. Returns
    [{"page_id", "title", "depth", "path"}] for uncovered pages whose title
    matches, in discovery order.
    """
    covered = {str(c) for c in covered_ids}
    out: list[dict[str, Any]] = []
    level: list[tuple[str, str]] = [(str(home_id), "")]
    for depth in range(1, max_depth + 1):
        nxt: list[tuple[str, str]] = []
        for parent_id, parent_path in level:
            for child in await list_children(parent_id):
                cid = str(child.get("id") or "")
                if not cid or cid in covered:
                    continue
                title = str(child.get("title") or "")
                path = f"{parent_path} > {title}" if parent_path else title
                if title_pattern.search(title):
                    out.append({"page_id": cid, "title": title, "depth": depth, "path": path})
                nxt.append((cid, path))
        level = nxt
    return out
