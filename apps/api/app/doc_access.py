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
    if eid:
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
    web_rel += "&".join(qparts)

    out: dict[str, Any] = {
        "path": p,
        "external_id": eid or None,
        "source_type": st,
        "document_id": document_id,
        # How to load full body
        "body_api": body_api_rel or body_api_file_rel,
        "body_api_file": body_api_file_rel,
        "web_path": web_rel,
        # MCP / agents: which tool to call for full text
        "mcp_tool": "kb_get_document",
        "mcp_args": {"path": p},
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
    acc = document_access(
        external_id=item.get("external_id") or item.get("code") or item.get("id"),
        source_type=item.get("source_type") or item.get("section"),
        document_id=item.get("document_id"),
        path=item.get("path"),
        title=item.get("title"),
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
