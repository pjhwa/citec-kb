"""Tests for app.confluence.sync._crawl_source — the production pagination
loop (not just search_pages_incremental in isolation). All httpx-mocked, no
live Confluence, no DB (uses tmp_path for raw_dir only)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.confluence.client import ConfluenceClient, RateLimiter
from app.confluence.sync import _crawl_source
from app.settings import Settings


def _client() -> ConfluenceClient:
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME="u",
        CONFLUENCE_PASSWORD="p",
    )
    return ConfluenceClient(settings)


def _page_response(page_id: str, title: str = "T") -> dict:
    return {
        "id": page_id,
        "title": title,
        "version": {"number": 1, "when": "2026-09-07T15:59:11.000+09:00"},
        "ancestors": [],
        "body": {"storage": {"value": f"<p>body {page_id}</p>"}},
    }


def _patch_bulk_client(client: ConfluenceClient, handler) -> None:
    def bulk_client(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=client._base_url, transport=httpx.MockTransport(handler))

    client.bulk_client = bulk_client  # type: ignore[method-assign]


def test_crawl_writes_files_across_two_roots(tmp_path):
    client = _client()
    roots = {"root1": "Root One", "root2": "Root Two"}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/search"):
            root_id = params["cql"].split("ancestor=", 1)[1].split(" ", 1)[0]
            start = int(params["start"])
            if start > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": f"{root_id}-p1"}]})
        page_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_page_response(page_id))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_source(
            client,
            source_type="confluence_docs",
            roots=roots,
            space_key="LOOKIN",
            since=None,
            raw_dir=tmp_path,
            max_pages_per_root=None,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert len(result.written) == 2
    assert not result.errors
    written_ids = {w.page_id for w in result.written}
    assert written_ids == {"root1-p1", "root2-p1"}
    for w in result.written:
        assert w.path.exists()
    # spec requirement: the literal CQL string must be present in the log
    # trail so an operator can tell "wrong format" from "no changes"
    assert any("ancestor=root1" in note and "cql=" in note for note in result.cql_log)


def test_crawl_one_bad_page_does_not_abort_the_others(tmp_path):
    client = _client()
    roots = {"root1": "Root One"}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/search"):
            start = int(params["start"])
            if start > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": "bad"}, {"id": "good"}]})
        page_id = request.url.path.rsplit("/", 1)[-1]
        if page_id == "bad":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=_page_response(page_id))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_source(
            client,
            source_type="confluence_docs",
            roots=roots,
            space_key="LOOKIN",
            since=None,
            raw_dir=tmp_path,
            max_pages_per_root=None,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert [w.page_id for w in result.written] == ["good"]
    assert len(result.errors) == 1
    assert result.errors[0]["page_id"] == "bad"


def test_crawl_aborts_root_when_pagination_makes_no_progress(tmp_path):
    """If the Confluence instance ignores start/limit and keeps returning
    the same full page, the loop must break instead of hanging forever and
    rewriting the same files indefinitely — this is unverified-live item #3
    (pagination behavior at scale) turned into a defensive guard."""
    client = _client()
    roots = {"root1": "Root One"}
    page_size = 2

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            # always returns the SAME full page regardless of start=
            return httpx.Response(
                200, json={"results": [{"id": "a"}, {"id": "b"}]}
            )
        page_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_page_response(page_id))

    _patch_bulk_client(client, handler)

    async def _run():
        import app.confluence.sync as sync_mod

        original = sync_mod._PAGE_SIZE
        sync_mod._PAGE_SIZE = page_size
        try:
            return await _crawl_source(
                client,
                source_type="confluence_docs",
                roots=roots,
                space_key="LOOKIN",
                since=None,
                raw_dir=tmp_path,
                max_pages_per_root=None,
                rps=0,
                tz_name="Asia/Seoul",
            )
        finally:
            sync_mod._PAGE_SIZE = original

    result = asyncio.run(asyncio.wait_for(_run(), timeout=5))
    assert {w.page_id for w in result.written} == {"a", "b"}  # written once
    assert any("pagination stalled" in e["error"] for e in result.errors)
