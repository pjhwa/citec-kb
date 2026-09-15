import asyncio

import httpx
import pytest

from app.confluence.client import ConfluenceClient, RateLimiter, build_incremental_cql
from app.settings import Settings


def _make_client() -> ConfluenceClient:
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME="u",
        CONFLUENCE_PASSWORD="p",
    )
    return ConfluenceClient(settings)


# --- build_incremental_cql: pure string assembly, no I/O ---


def test_cql_bootstrap_has_no_lastmodified_filter():
    cql = build_incremental_cql("222532692", since=None)
    assert cql == "ancestor=222532692 and type=page order by id asc"


def test_cql_incremental_includes_lastmodified_filter():
    cql = build_incremental_cql("222532692", since="2026/09/07 06:59")
    assert cql == (
        'ancestor=222532692 and type=page and lastmodified > "2026/09/07 06:59" '
        "order by id asc"
    )


def test_cql_escapes_embedded_quotes_in_cursor():
    cql = build_incremental_cql("1", since='2026/09/07 06:59" or 1=1')
    assert '\\"' in cql
    assert cql.count('"') == 2 + 1  # opening+closing + one escaped


# --- get_page_full / search_pages_incremental request shape ---


def test_get_page_full_requests_body_version_ancestors_expand():
    client = _make_client()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"id": "123"})

    def _http(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=client._base_url, transport=httpx.MockTransport(handler))

    client._http = _http

    async def _run():
        return await client.get_page_full("123")

    data = asyncio.run(_run())
    assert data == {"id": "123"}
    expand = captured["params"]["expand"].split(",")
    assert set(expand) == {"body.storage", "version", "ancestors"}


def test_search_pages_incremental_sends_cql_and_pagination_params():
    client = _make_client()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [], "size": 0})

    def _http(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=client._base_url, transport=httpx.MockTransport(handler))

    client._http = _http

    async def _run():
        return await client.search_pages_incremental("222532692", since="2026/01/01 00:00", start=50, limit=25)

    asyncio.run(_run())
    assert captured["params"]["start"] == "50"
    assert captured["params"]["limit"] == "25"
    assert "lastmodified" in captured["params"]["cql"]


def test_search_pages_incremental_paginates_across_three_pages():
    """250 results in pages of 100 → 3 requests at start=0,100,200."""
    client = _make_client()
    total = 250
    page_size = 100
    all_ids = [str(1000 + i) for i in range(total)]
    starts_seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(dict(request.url.params)["start"])
        starts_seen.append(start)
        chunk = all_ids[start : start + page_size]
        return httpx.Response(200, json={"results": [{"id": i} for i in chunk], "size": len(chunk)})

    def _http(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=client._base_url, transport=httpx.MockTransport(handler))

    client._http = _http

    async def _run():
        collected: list[str] = []
        start = 0
        while True:
            data = await client.search_pages_incremental("1", start=start, limit=page_size)
            results = data.get("results") or []
            collected.extend(r["id"] for r in results)
            if len(results) < page_size:
                break
            start += page_size
        return collected

    collected = asyncio.run(_run())
    assert collected == all_ids
    assert starts_seen == [0, 100, 200]


def test_get_with_retry_backs_off_on_429_then_succeeds():
    client = _make_client()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"id": "123"})

    async def _run():
        async with httpx.AsyncClient(
            base_url=client._base_url, transport=httpx.MockTransport(handler)
        ) as http_client:
            return await client.get_page_full(
                "123", client=http_client, limiter=RateLimiter(0)
            )

    data = asyncio.run(_run())
    assert data == {"id": "123"}
    assert calls["n"] == 2


def test_get_with_retry_raises_after_exhausting_retries():
    client = _make_client()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "0"}, json={})

    async def _run():
        async with httpx.AsyncClient(
            base_url=client._base_url, transport=httpx.MockTransport(handler)
        ) as http_client:
            return await client.get_page_full(
                "123", client=http_client, limiter=RateLimiter(0)
            )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_run())


def test_rate_limiter_spaces_calls_by_min_interval():
    async def _run():
        limiter = RateLimiter(rps=1000)  # 1ms spacing, fast test
        import time

        t0 = time.monotonic()
        await limiter.wait()
        await limiter.wait()
        await limiter.wait()
        return time.monotonic() - t0

    elapsed = asyncio.run(_run())
    assert elapsed >= 0.002  # at least 2 intervals of ~1ms


def test_rate_limiter_disabled_when_rps_zero():
    async def _run():
        limiter = RateLimiter(rps=0)
        import time

        t0 = time.monotonic()
        for _ in range(50):
            await limiter.wait()
        return time.monotonic() - t0

    elapsed = asyncio.run(_run())
    assert elapsed < 0.05
