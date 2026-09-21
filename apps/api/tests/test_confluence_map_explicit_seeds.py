"""Tests for app.confluence.map_sync._crawl_explicit_pages — fetching a
fixed list of individually-registered page IDs directly by ID (no CQL
ancestor listing). Mirrors test_confluence_map_crawl.py's style: httpx-mocked,
no live Confluence, no DB (tmp_path for raw_dir only).
"""

from __future__ import annotations

import asyncio

import httpx

from app.confluence.client import ConfluenceClient
from app.confluence.map_sync import _crawl_explicit_pages
from app.settings import Settings


def _client() -> ConfluenceClient:
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME="u",
        CONFLUENCE_PASSWORD="p",
    )
    return ConfluenceClient(settings)


def _page_meta_response(page_id: str, title: str = "T") -> dict:
    return {
        "id": page_id,
        "title": title,
        "version": {"number": 1, "when": "2026-09-07T15:59:11.000+09:00"},
        "ancestors": [],
    }


def _patch_bulk_client(client: ConfluenceClient, handler) -> None:
    def bulk_client(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=client._base_url, transport=httpx.MockTransport(handler))

    client.bulk_client = bulk_client  # type: ignore[method-assign]


def test_crawl_explicit_pages_writes_one_file_per_seed(tmp_path):
    client = _client()
    pages = {"111": "GitHub Q&A", "222": "SDS GitHub info."}

    def handler(request: httpx.Request) -> httpx.Response:
        page_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_page_meta_response(page_id, title=f"Title {page_id}"))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_explicit_pages(
            client,
            pages=pages,
            space_key="SPC",
            space_name="Coding References",
            raw_dir=tmp_path,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert len(result.written) == 2
    assert not result.errors
    written_ids = {w.page_id for w in result.written}
    assert written_ids == {"111", "222"}
    for w in result.written:
        assert w.path.exists()
        assert "SPC" in w.path.read_text(encoding="utf-8")


def test_crawl_explicit_pages_one_failure_does_not_drop_the_rest(tmp_path):
    client = _client()
    pages = {"111": "OK page", "222": "Broken page"}

    def handler(request: httpx.Request) -> httpx.Response:
        page_id = request.url.path.rsplit("/", 1)[-1]
        if page_id == "222":
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=_page_meta_response(page_id))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_explicit_pages(
            client,
            pages=pages,
            space_key="SPC",
            space_name="Coding References",
            raw_dir=tmp_path,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert len(result.written) == 1
    assert result.written[0].page_id == "111"
    assert len(result.errors) == 1
    assert result.errors[0]["page_id"] == "222"
