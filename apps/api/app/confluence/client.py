"""Thin async httpx wrapper over the Confluence REST API (v1, /rest/api/*)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.settings import Settings

logger = logging.getLogger("citec.confluence.client")


class ConfluenceConfigError(RuntimeError):
    """CONFLUENCE_BASE_URL / CONFLUENCE_USERNAME / CONFLUENCE_PASSWORD not configured."""


class RateLimiter:
    """Simple async token-spacing limiter (min interval between requests).

    Not a burst-capacity limiter — just paces requests evenly, which is
    what a synchronous batch crawl against a shared internal Confluence
    needs. `rps <= 0` disables pacing (immediate)."""

    def __init__(self, rps: float) -> None:
        self._min_interval = 1.0 / rps if rps and rps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


def build_incremental_cql(ancestor_id: str, since: str | None = None) -> str:
    """CQL for 'pages under ancestor_id, optionally modified after since'.

    `since` must already be formatted in Confluence CQL's expected
    "yyyy/MM/dd HH:mm" form (see app.confluence.sync.format_cursor) — this
    function does no date parsing, only string assembly, so it stays
    testable without a live Confluence to check syntax against.
    `order by id asc` makes start/limit pagination deterministic (ties in
    lastmodified could otherwise reorder pages between pages of results).
    """
    cql = f"ancestor={ancestor_id} and type=page"
    if since:
        escaped = since.replace('"', '\\"')
        cql += f' and lastmodified > "{escaped}"'
    cql += " order by id asc"
    return cql


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
            # Retries connection-level failures only (DNS, TCP connect,
            # TLS handshake) — never an HTTP response like 401/403. A DNS
            # blip and a transient 401 were both observed in production,
            # each self-resolving on the very next call seconds later; this
            # absorbs the connection-level case automatically without ever
            # retrying an auth failure (see the "no retry on auth failure"
            # rule in the Confluence guide).
            transport=httpx.AsyncHTTPTransport(retries=2),
        )

    def bulk_client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        """A single reusable AsyncClient for a long batch (e.g. sync_cli's
        Confluence crawl) — keeps the TCP/TLS connection alive across
        hundreds of requests instead of a fresh handshake per call, which
        is what every other method on this class does today. Left those
        alone (live MCP diagram traffic depends on their current behavior);
        this is only used by the new incremental-sync path."""
        return self._http(timeout=timeout)

    async def _get_with_retry(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        *,
        limiter: RateLimiter,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        """GET with rate pacing + bounded backoff on 429/503.

        AsyncHTTPTransport(retries=2) on `_http()` only retries
        connection-level failures (DNS/TCP/TLS) — never an HTTP response.
        429/503 from a shared internal Confluence under batch load needs
        its own explicit, logged backoff honoring Retry-After.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            await limiter.wait()
            try:
                resp = await client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt >= max_retries:
                    raise
                delay = min(2.0**attempt, 30.0)
                logger.warning(
                    "confluence request error path=%s attempt=%s/%s err=%s retry_in=%.1fs",
                    path, attempt, max_retries, exc, delay,
                )
                await asyncio.sleep(delay)
                continue
            if resp.status_code in (429, 503):
                retry_after_hdr = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after_hdr) if retry_after_hdr else min(2.0**attempt, 30.0)
                except ValueError:
                    delay = min(2.0**attempt, 30.0)
                logger.warning(
                    "confluence rate-limited path=%s status=%s retry_after=%s attempt=%s/%s retry_in=%.1fs",
                    path, resp.status_code, retry_after_hdr, attempt, max_retries, delay,
                )
                if attempt >= max_retries:
                    resp.raise_for_status()
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
        if last_exc:
            raise last_exc
        raise RuntimeError(f"confluence request exhausted retries: {path}")  # pragma: no cover

    async def get_page_full(
        self,
        page_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
    ) -> dict[str, Any]:
        """Page body + version + ancestors — needed for last-modified date
        (version.when) and tech_repo's ancestor breadcrumb; get_page_body()
        only expands body.storage."""
        params = {"expand": "body.storage,version,ancestors"}
        if client is not None:
            return await self._get_with_retry(
                client, f"/rest/api/content/{page_id}", params,
                limiter=limiter or RateLimiter(0),
            )
        async with self._http() as c:
            resp = await c.get(f"/rest/api/content/{page_id}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def search_pages_incremental(
        self,
        ancestor_id: str,
        *,
        since: str | None = None,
        start: int = 0,
        limit: int = 50,
        client: httpx.AsyncClient | None = None,
        limiter: RateLimiter | None = None,
    ) -> dict[str, Any]:
        """Pages under ancestor_id, optionally lastmodified > since (CQL
        cursor string, see build_incremental_cql). since=None → full
        bootstrap crawl of everything under the ancestor."""
        cql = build_incremental_cql(ancestor_id, since=since)
        params = {"cql": cql, "start": start, "limit": limit}
        if client is not None:
            return await self._get_with_retry(
                client, "/rest/api/content/search", params,
                limiter=limiter or RateLimiter(0),
            )
        async with self._http() as c:
            resp = await c.get("/rest/api/content/search", params=params)
            resp.raise_for_status()
            return resp.json()

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
                # Confluence omits version and metadata.mediaType unless
                # explicitly expanded — without them, version.number is
                # missing (list_diagrams could only report version=None) and
                # there's no way to compare a citec-kb-uploaded attachment's
                # Content-Type against a known-working, UI-created one.
                params={"limit": 200, "expand": "version,metadata.mediaType"},
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
                files={"file": (filename, content, "application/vnd.jgraph.mxfile")},
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
                files={"file": (filename, content, "application/vnd.jgraph.mxfile")},
            )
            resp.raise_for_status()
            return resp.json()
