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
    assert "version" in captured["params"].get("expand", "").split(",")


def test_list_attachments_requests_media_type_expand():
    """To compare a citec-kb-uploaded attachment's Content-Type against a
    known-working, UI-created one (diagnosing 'cannot display diagram' after
    the revision-mismatch hypothesis was ruled out — version and macro
    revision matched), the media type must be expanded too."""
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
    expand = captured["params"].get("expand", "")
    assert "version" in expand.split(",")
    assert "metadata.mediaType" in expand.split(",")


def _make_client() -> ConfluenceClient:
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME="u",
        CONFLUENCE_PASSWORD="p",
    )
    return ConfluenceClient(settings)


def test_create_attachment_uses_mxfile_media_type():
    """A known-working (UI-created) diagram's media_type is
    application/vnd.jgraph.mxfile — confirmed against real production data.
    citec-kb was hardcoding application/xml, which is why uploaded diagrams
    failed to render ('cannot display diagram') even though the attachment
    content and macro revision were otherwise correct."""
    client = _make_client()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"id": "att-new", "version": {"number": 1}})

    def _http(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=client._base_url, transport=httpx.MockTransport(handler))

    client._http = _http

    async def _run():
        return await client.create_attachment("123", "diagram", b"<mxfile></mxfile>")

    asyncio.run(_run())
    assert b"application/vnd.jgraph.mxfile" in captured["body"]
    assert b"Content-Type: application/xml" not in captured["body"]


def test_update_attachment_data_uses_mxfile_media_type():
    client = _make_client()
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"id": "att1", "version": {"number": 4}})

    def _http(timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=client._base_url, transport=httpx.MockTransport(handler))

    client._http = _http

    async def _run():
        return await client.update_attachment_data("123", "att1", "diagram", b"<mxfile></mxfile>")

    asyncio.run(_run())
    assert b"application/vnd.jgraph.mxfile" in captured["body"]
    assert b"Content-Type: application/xml" not in captured["body"]
