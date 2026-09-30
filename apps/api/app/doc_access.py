"""Stable access handles for full document body (API + web + MCP clients).

Every document hit in API responses should include these so external systems
do not depend on UI-only 「원문 보기」 links.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from app.settings import get_settings


def document_path(
    *,
    external_id: Optional[str] = None,
    source_type: Optional[str] = None,
    path: Optional[str] = None,
) -> str:
    if path:
        return str(path).lstrip("/")
    st = (source_type or "support_history").strip() or "support_history"
    eid = (external_id or "").strip()
    if not eid:
        return st
    if eid.endswith(".md"):
        return f"{st}/{eid}"
    return f"{st}/{eid}.md"


def document_access(
    *,
    external_id: Optional[str] = None,
    source_type: Optional[str] = None,
    document_id: Optional[str] = None,
    path: Optional[str] = None,
    title: Optional[str] = None,
    absolute: bool = True,
) -> dict[str, Any]:
    """Return relative + absolute URLs to fetch full original body."""
    settings = get_settings()
    web = (settings.public_web_base or "").rstrip("/")
    api = (settings.public_api_base or "").rstrip("/")
    st = (source_type or "support_history").strip() or "support_history"
    eid = (external_id or "").strip()
    p = document_path(external_id=eid, source_type=st, path=path)

    # Relative API paths (preferred for same-origin / compose internal clients)
    body_api_rel = ""
    if st == "checkitem" and eid:
        body_api_rel = f"/v1/checkitems/{quote(eid, safe='')}"
    elif eid:
        body_api_rel = f"/v1/tickets/{quote(eid, safe='')}?source_type={quote(st, safe='')}"
    body_api_file_rel = f"/api/wiki/file?path={quote(p, safe='/')}"
    web_rel = "/doc.html?"
    qparts = []
    if eid:
        qparts.append(f"eid={quote(eid, safe='')}")
    if st:
        qparts.append(f"st={quote(st, safe='')}")
    if p:
        qparts.append(f"path={quote(p, safe='')}")
    if title:
        qparts.append(f"title={quote(str(title)[:200], safe='')}")
    if st == "checkitem":
        qparts.append("kind=checkitem")
    web_rel += "&".join(qparts)

    # P0-A (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §4
    # item 3): confluence_map only ever stores a ~400-char excerpt of the
    # source page, never its full body (see app/retrieval/search.py's
    # evidence_eligible=False for this source_type). kb_get_document on a map
    # row therefore returns that same snapshot again — never the current
    # Confluence body — so callers must not be told "full body" for it.
    # body_kind names which of those two `mcp_tool` actually returns; a
    # non-map body_kind stays "fulltext" (kb_get_document does return the
    # ingested full body_md for every other source_type).
    is_map = st == "confluence_map"
    body_kind = "snapshot" if is_map else "fulltext"
    verify_via: Optional[dict[str, Any]] = None
    if is_map and eid and eid.isdigit():
        # confluence_map's external_id is normally the Confluence pageId
        # (see app/confluence/map_sync.py), but iter_confluence_map falls
        # back to the filename stem when a row has no Page ID frontmatter —
        # that stem is not a real pageId, so only emit this guidance when
        # eid actually looks like one. Point at the real source lookup
        # instead of re-querying the KB snapshot — §4 item 3's required
        # default: confluence-mcp.getPageByID(pageId, body.storage, version).
        verify_via = {
            "tool": "confluence-mcp.getPageByID",
            "args": {"pageId": eid, "expand": "body.storage,version"},
            "note": "이 결과는 KB에 저장된 발췌(snapshot)입니다. 현재 원문/버전은 이 도구로 직접 확인하세요.",
        }
    out: dict[str, Any] = {
        "path": p,
        "external_id": eid or None,
        "source_type": st,
        "document_id": document_id,
        # How to load full body
        "body_api": body_api_rel or body_api_file_rel,
        "body_api_file": body_api_file_rel,
        "web_path": web_rel,
        "body_kind": body_kind,
        "verify_via": verify_via,
        # MCP / agents: which tool to call. For confluence_map this still
        # returns the stored snapshot, not the current page — see body_kind.
        "mcp_tool": "kb_get_checkitem" if st == "checkitem" else "kb_get_document",
        "mcp_args": (
            {"code": eid} if st == "checkitem" and eid else {"path": p}
        ),
    }
    if absolute:
        if body_api_rel and api:
            out["body_api_url"] = api + body_api_rel
        elif api:
            out["body_api_url"] = api + body_api_file_rel
        if api:
            out["body_api_file_url"] = api + body_api_file_rel
        if web:
            out["web_url"] = web + web_rel
    return out


def attach_document_access(item: dict[str, Any], *, absolute: bool = True) -> dict[str, Any]:
    """Mutate/return item with access fields flattened + nested ``access`` object."""
    if not isinstance(item, dict):
        return item
    st = item.get("source_type") or item.get("section")
    eid = item.get("external_id") or item.get("code") or item.get("id")
    # checkitem rows often only have code
    if not st and item.get("code") and (
        item.get("check_method") is not None or item.get("subject") is not None
    ):
        st = "checkitem"
    acc = document_access(
        external_id=eid,
        source_type=st,
        document_id=item.get("document_id"),
        path=item.get("path"),
        title=item.get("title") or item.get("subject"),
        absolute=absolute,
    )
    # Flatten common keys without overwriting richer existing values
    if not item.get("path"):
        item["path"] = acc["path"]
    item["body_api"] = acc.get("body_api")
    item["body_api_url"] = acc.get("body_api_url")
    item["web_url"] = acc.get("web_url")
    item["web_path"] = acc.get("web_path")
    item["access"] = acc
    return item
