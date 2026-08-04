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
        item: dict[str, Any] = {
            "diagram_name": name,
            "attachment_id": att.get("id") if att else None,
            "version": ((att or {}).get("version") or {}).get("number"),
            "media_type": ((att or {}).get("metadata") or {}).get("mediaType"),
            "inline": True,
        }
        if not att:
            # Diagnostic aid: show every attachment title on the page (no
            # extension filtering — that assumption is exactly what's in
            # question when a match fails), so a naming-convention mismatch
            # is visible without extra tooling instead of a bare
            # attachment_id=None. An empty list is itself the answer: there's
            # nothing on the page to match against at all.
            item["candidate_attachment_titles"] = [
                a.get("title") for a in attachments if a.get("title")
            ]
        items.append(item)
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
                "media_type": (att.get("metadata") or {}).get("mediaType"),
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
    content_bytes = xml_content.encode("utf-8")
    if att:
        # Reuse the attachment's actual title — some draw.io Confluence app
        # versions store the source with no extension at all. Forcing
        # "<diagram_name>.drawio" here would rename it out from under the
        # macro that references the original filename.
        filename = att.get("title") or diagram_name
        result = await client.update_attachment_data(page_id, att["id"], filename, content_bytes)
    else:
        # No extension — matches this org's real draw.io Confluence app
        # convention (confirmed against production data: working source
        # attachments have no suffix; only the auto-generated .png preview
        # and ~.tmp lock file do). A '.drawio'-suffixed filename was the
        # confirmed root cause of 'cannot display diagram' on newly created
        # attachments — Confluence re-derives the stored media type from the
        # filename extension rather than trusting the uploaded Content-Type.
        filename = diagram_name
        result = await client.create_attachment(page_id, filename, content_bytes)
    return {
        "attachment_id": result.get("id"),
        "version": (result.get("version") or {}).get("number"),
    }
