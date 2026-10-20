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


def test_search_failure_among_many_good_pages_blocks_cursor_end_to_end(tmp_path):
    """실제 크롤러 결과(루트 검색 401 + 정상 페이지 다수)를 커서 판정 헬퍼에 그대로
    넣는다 — 오류율이 1% 미만이어도 전진 불가여야 한다(루트 변경분 영구 누락 방지)."""
    from app.confluence.sync import errors_allow_cursor_advance

    client = _client()
    roots = {"bad_root": "Bad Root", "good_root": "Good Root"}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/search"):
            if "bad_root" in params["cql"]:
                return httpx.Response(401, json={"error": "unauthorized"})
            if int(params["start"]) >= 50 * 3:
                return httpx.Response(200, json={"results": []})
            start = int(params["start"])
            return httpx.Response(200, json={"results": [{"id": f"p{start + i}"} for i in range(50)]})
        page_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_page_meta_response(page_id))

    _patch_bulk_client(client, handler)
    result = asyncio.run(
        _crawl_map_source(
            client, source_id="test_source", roots=roots, space_key="DevOps001",
            space_name="SCP인프라운영팀", since=None, raw_dir=tmp_path,
            max_pages_per_root=None, rps=0, tz_name="Asia/Seoul",
        )
    )
    assert len(result.written) == 150
    assert len(result.errors) == 1 and result.errors[0]["page_id"] is None
    total = len(result.written) + len(result.errors)
    s = Settings(CONFLUENCE_MAX_ERROR_RATE=0.5, CONFLUENCE_MAX_ERROR_COUNT=10)  # 아무리 느슨해도
    assert errors_allow_cursor_advance(result.errors, total, s) is False


def _s(rate=0.01, count=0):
    return Settings(CONFLUENCE_MAX_ERROR_RATE=rate, CONFLUENCE_MAX_ERROR_COUNT=count)


def test_errors_allow_cursor_advance_policy_table():
    from app.confluence.sync import errors_allow_cursor_advance as ok

    page = lambda n: [{"page_id": f"p{i}", "root_id": "r", "error": "x"} for i in range(n)]  # noqa: E731
    root = [{"page_id": None, "root_id": "r", "error": "search"}]
    assert ok([], 10, _s()) is True
    assert ok(page(1), 1000, _s()) is True            # 0.1% <= 1%
    assert ok(page(1), 2, _s()) is False              # 50% > 1%, count=0
    assert ok(page(1), 2, _s(count=1)) is True        # opt-in count
    assert ok(page(2), 4, _s(count=1)) is False       # over count and rate
    assert ok(root, 1000, _s()) is False              # root-level: never
    assert ok(root, 1000, _s(rate=1.0, count=99)) is False
    assert ok(page(1) + root, 1000, _s()) is False    # mixed: root-level wins
    # explicit-seed errors carry page_id with root_id None → page-level
    assert ok([{"page_id": "9", "root_id": None, "error": "x"}], 1000, _s()) is True
