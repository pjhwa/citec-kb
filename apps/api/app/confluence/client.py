"""Thin async httpx wrapper over the Confluence REST API (v1, /rest/api/*)."""

from __future__ import annotations

from typing import Any

import httpx

from app.settings import Settings


class ConfluenceConfigError(RuntimeError):
    """CONFLUENCE_BASE_URL / CONFLUENCE_USERNAME / CONFLUENCE_PASSWORD not configured."""


class ConfluenceClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.confluence_base_url or not settings.confluence_username or not settings.confluence_password:
            raise ConfluenceConfigError(
                "CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD "
                "is not configured on the API server"
            )
        self._base_url = settings.confluence_base_url.rstrip("/")
        self._auth = (settings.confluence_username, settings.confluence_password)

    def _http(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            auth=self._auth,
            headers={"Accept": "application/json"},
        )

    async def get_page_body(self, page_id: str) -> str:
        async with self._http() as client:
            resp = await client.get(
                f"/rest/api/content/{page_id}",
                params={"expand": "body.storage"},
            )
            resp.raise_for_status()
            data = resp.json()
        return ((data.get("body") or {}).get("storage") or {}).get("value") or ""

    async def search_pages_by_space(
        self, space_key: str, title_query: str = "", limit: int = 25
    ) -> list[dict[str, Any]]:
        cql = f'space="{space_key}" and type=page'
        if title_query:
            escaped = title_query.replace('"', '\\"')
            cql += f' and title~"{escaped}"'
        async with self._http() as client:
            resp = await client.get(
                "/rest/api/content/search",
                params={"cql": cql, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("results") or []

    async def list_attachments(self, page_id: str) -> list[dict[str, Any]]:
        async with self._http() as client:
            resp = await client.get(
                f"/rest/api/content/{page_id}/child/attachment",
                # Confluence omits the version sub-object unless explicitly
                # expanded — without this, every attachment's version.number
                # is missing and list_diagrams can only report version=None.
                params={"limit": 200, "expand": "version"},
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("results") or []

    async def download_attachment(self, download_path: str) -> bytes:
        async with self._http(timeout=60.0) as client:
            resp = await client.get(download_path)
            resp.raise_for_status()
            return resp.content

    async def create_attachment(self, page_id: str, filename: str, content: bytes) -> dict[str, Any]:
        async with self._http(timeout=60.0) as client:
            resp = await client.post(
                f"/rest/api/content/{page_id}/child/attachment",
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, content, "application/xml")},
            )
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") or [data]
        return results[0]

    async def update_attachment_data(
        self, page_id: str, attachment_id: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        async with self._http(timeout=60.0) as client:
            resp = await client.post(
                f"/rest/api/content/{page_id}/child/attachment/{attachment_id}/data",
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, content, "application/xml")},
            )
            resp.raise_for_status()
            return resp.json()
