import asyncio

import httpx
import pytest

from app.confluence.client import ConfluenceClient, ConfluenceConfigError
from app.settings import Settings


def test_client_raises_when_base_url_missing():
    settings = Settings(
        CONFLUENCE_BASE_URL=None, CONFLUENCE_USERNAME="u", CONFLUENCE_PASSWORD="p"
    )
    with pytest.raises(ConfluenceConfigError):
        ConfluenceClient(settings)


def test_client_raises_when_username_missing():
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME=None,
        CONFLUENCE_PASSWORD="p",
    )
    with pytest.raises(ConfluenceConfigError):
        ConfluenceClient(settings)


def test_client_raises_when_password_missing():
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME="u",
        CONFLUENCE_PASSWORD=None,
    )
    with pytest.raises(ConfluenceConfigError):
        ConfluenceClient(settings)


def test_client_constructs_with_all_set():
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com/",
        CONFLUENCE_USERNAME="svc-citec-kb",
        CONFLUENCE_PASSWORD="secret-pw",
    )
    client = ConfluenceClient(settings)
    assert client._base_url == "https://c.example.com"
    assert client._auth == ("svc-citec-kb", "secret-pw")


def test_list_attachments_requests_version_expand():
    """Confluence's REST API omits the `version` sub-object from attachment
    results unless expand=version is explicitly requested — without it,
    every attachment's version.number is missing, so list_diagrams can only
    ever report version=None. Confirmed against real production output
    (three diagrams, all version=None) even after attachment matching was
    fixed."""
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME="u",
        CONFLUENCE_PASSWORD="p",
    )
    client = ConfluenceClient(settings)

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": []})

    def _http(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=client._base_url,
            transport=httpx.MockTransport(handler),
        )

    client._http = _http

    async def _run():
        return await client.list_attachments("123")

    asyncio.run(_run())
    assert captured["params"].get("expand") == "version"
