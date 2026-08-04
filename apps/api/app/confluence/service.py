"""Orchestrates ConfluenceClient + macro parsing into diagram list/get/put operations."""

from __future__ import annotations

from typing import Any

from app.confluence.client import ConfluenceClient
from app.confluence.macro import extract_drawio_diagram_names, match_attachment_for_diagram
from app.settings import get_settings


class DiagramNotFoundError(RuntimeError):
    """No attachment could be resolved for the requested diagram name."""


class DiagramFormatError(RuntimeError):
    """The matched attachment's bytes aren't UTF-8 text (e.g. a mismatched
    binary preview, or a genuinely non-text-XML .drawio storage format)."""


def _client() -> ConfluenceClient:
    return ConfluenceClient(get_settings())


async def list_diagrams(page_id: str) -> list[dict[str, Any]]:
    client = _client()
    body_xml = await client.get_page_body(page_id)
    attachments = await client.list_attachments(page_id)
    inline_names = extract_drawio_diagram_names(body_xml)

    items: list[dict[str, Any]] = []
    seen_attachment_ids: set[str] = set()
    for name in inline_names:
        att = match_attachment_for_diagram(name, attachments)
        items.append(
            {
                "diagram_name": name,
                "attachment_id": att.get("id") if att else None,
                "version": ((att or {}).get("version") or {}).get("number"),
                "inline": True,
            }
        )
        if att and att.get("id"):
            seen_attachment_ids.add(att["id"])

    for att in attachments:
        title = att.get("title") or ""
        if not title.endswith(".drawio"):
            continue
        if att.get("id") in seen_attachment_ids:
            continue
        items.append(
            {
                "diagram_name": title[: -len(".drawio")],
                "attachment_id": att.get("id"),
                "version": (att.get("version") or {}).get("number"),
                "inline": False,
            }
        )
    return items


async def find_pages(space_key: str, title_query: str = "", limit: int = 25) -> list[dict[str, Any]]:
    client = _client()
    results = await client.search_pages_by_space(space_key, title_query=title_query, limit=limit)
    return [
        {
            "page_id": r.get("id"),
            "title": r.get("title"),
            "web_url": (r.get("_links") or {}).get("webui") or "",
        }
        for r in results
    ]


async def get_diagram_xml(page_id: str, diagram_name: str) -> str:
    client = _client()
    attachments = await client.list_attachments(page_id)
    att = match_attachment_for_diagram(diagram_name, attachments)
    if not att:
        raise DiagramNotFoundError(diagram_name)
    download_link = (att.get("_links") or {}).get("download") or ""
    if not download_link:
        raise DiagramNotFoundError(diagram_name)
    content = await client.download_attachment(download_link)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DiagramFormatError(
            f"attachment_id={att.get('id')} title={att.get('title')!r} "
            f"is not valid UTF-8 text ({e})"
        ) from e


async def put_diagram_xml(page_id: str, diagram_name: str, xml_content: str) -> dict[str, Any]:
    client = _client()
    attachments = await client.list_attachments(page_id)
    att = match_attachment_for_diagram(diagram_name, attachments)
    filename = f"{diagram_name}.drawio"
    content_bytes = xml_content.encode("utf-8")
    if att:
        result = await client.update_attachment_data(page_id, att["id"], filename, content_bytes)
    else:
        result = await client.create_attachment(page_id, filename, content_bytes)
    return {
        "attachment_id": result.get("id"),
        "version": (result.get("version") or {}).get("number"),
    }
