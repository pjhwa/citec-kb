"""Tests for app.confluence.map_sync._crawl_map_source — the production
pagination loop (not just search_pages_incremental in isolation). All
httpx-mocked, no live Confluence, no DB (uses tmp_path for raw_dir only).

Mirrors test_confluence_crawl.py's style for the sibling confluence_docs/
tech_repo crawler.
"""

from __future__ import annotations

import asyncio

import httpx

from app.confluence.client import ConfluenceClient
from app.confluence.map_sync import _crawl_map_source
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


def test_map_crawl_writes_files_across_two_roots(tmp_path):
    client = _client()
    roots = {"root1": "Root One", "root2": "Root Two"}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/search"):
            root_id = params["cql"].split("ancestor=", 1)[1].split(" ", 1)[0]
            if int(params["start"]) > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": f"{root_id}-p1"}]})
        page_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_page_meta_response(page_id))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_map_source(
            client,
            source_id="test_source",
            roots=roots,
            space_key="DevOps001",
            space_name="SCP인프라운영팀",
            since=None,
            raw_dir=tmp_path,
            max_pages_per_root=None,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert len(result.written) == 2
    assert not result.errors
    for w in result.written:
        assert w.path.exists()


def test_map_crawl_search_failure_on_one_root_does_not_abort_other_roots(tmp_path):
    """Prod evidence (2026-09-16 dry-run): an unhandled 401 on the
    search/listing call for confluence_map_lookin propagated out of
    _crawl_map_source entirely, and since sync_map()'s per-source_id loop
    calls asyncio.run(_crawl_map_source(...)) once per source_id, it killed
    every remaining source_id in the run too (techrepo/serviceexcellence
    team/icloudut/devops001/... never ran). Only the per-page get_page_meta
    call was ever guarded by try/except."""
    client = _client()
    roots = {"bad_root": "Bad Root", "good_root": "Good Root"}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/search"):
            if "bad_root" in params["cql"]:
                return httpx.Response(401, json={"error": "unauthorized"})
            return httpx.Response(200, json={"results": [{"id": "good-p1"}]})
        page_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_page_meta_response(page_id))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_map_source(
            client,
            source_id="test_source",
            roots=roots,
            space_key="DevOps001",
            space_name="SCP인프라운영팀",
            since=None,
            raw_dir=tmp_path,
            max_pages_per_root=None,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert [w.page_id for w in result.written] == ["good-p1"]
    assert len(result.errors) == 1
    assert result.errors[0]["root_id"] == "bad_root"
    assert result.errors[0]["page_id"] is None


def test_map_crawl_one_bad_page_does_not_abort_the_others(tmp_path):
    client = _client()
    roots = {"root1": "Root One"}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/search"):
            if int(params["start"]) > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": "bad"}, {"id": "good"}]})
        page_id = request.url.path.rsplit("/", 1)[-1]
        if page_id == "bad":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=_page_meta_response(page_id))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_map_source(
            client,
            source_id="test_source",
            roots=roots,
            space_key="DevOps001",
            space_name="SCP인프라운영팀",
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
