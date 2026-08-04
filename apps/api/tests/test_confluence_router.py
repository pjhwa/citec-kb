import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.confluence.client import ConfluenceConfigError
from app.confluence.service import DiagramNotFoundError
from app.routers import confluence as confluence_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(confluence_router.router)
    return TestClient(app)


async def _raise(*_args, **_kwargs):
    raise ConfluenceConfigError("not configured")


async def _raise_not_found(*_args, **_kwargs):
    raise DiagramNotFoundError("architecture")


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://c.example.com/x")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_list_diagrams_503_when_not_configured(monkeypatch):
    monkeypatch.setattr(confluence_router, "list_diagrams", _raise)
    r = _client().get("/v1/confluence/pages/123/diagrams")
    assert r.status_code == 503


def test_list_diagrams_happy_path(monkeypatch):
    async def _fake(page_id):
        assert page_id == "123"
        return [{"diagram_name": "architecture", "attachment_id": "att1", "version": 3, "inline": True}]

    monkeypatch.setattr(confluence_router, "list_diagrams", _fake)
    r = _client().get("/v1/confluence/pages/123/diagrams")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["diagram_name"] == "architecture"


def test_get_diagram_404_when_not_found(monkeypatch):
    monkeypatch.setattr(confluence_router, "get_diagram_xml", _raise_not_found)
    r = _client().get("/v1/confluence/pages/123/diagrams/missing")
    assert r.status_code == 404


def test_get_diagram_happy_path(monkeypatch):
    async def _fake(page_id, diagram_name):
        return "<mxGraphModel/>"

    monkeypatch.setattr(confluence_router, "get_diagram_xml", _fake)
    r = _client().get("/v1/confluence/pages/123/diagrams/architecture")
    assert r.status_code == 200
    assert r.text == "<mxGraphModel/>"
    assert r.headers["content-type"].startswith("text/xml")


def test_get_diagram_502_on_confluence_auth_error(monkeypatch):
    async def _fake(page_id, diagram_name):
        raise _http_error(403)

    monkeypatch.setattr(confluence_router, "get_diagram_xml", _fake)
    r = _client().get("/v1/confluence/pages/123/diagrams/architecture")
    assert r.status_code == 502


def test_put_diagram_happy_path(monkeypatch):
    async def _fake(page_id, diagram_name, xml_content):
        assert diagram_name == "architecture"
        assert xml_content == "<mxGraphModel/>"
        return {"attachment_id": "att1", "version": 4}

    monkeypatch.setattr(confluence_router, "put_diagram_xml", _fake)
    r = _client().put(
        "/v1/confluence/pages/123/diagrams/architecture",
        content=b"<mxGraphModel/>",
    )
    assert r.status_code == 200
    assert r.json() == {"attachment_id": "att1", "version": 4}


def test_find_pages_503_when_not_configured(monkeypatch):
    monkeypatch.setattr(confluence_router, "find_pages", _raise)
    r = _client().get("/v1/confluence/spaces/LOOKIN/pages")
    assert r.status_code == 503


def test_find_pages_happy_path(monkeypatch):
    async def _fake(space_key, title_query="", limit=25):
        assert space_key == "LOOKIN"
        assert title_query == "Network"
        assert limit == 10
        return [{"page_id": "456", "title": "Network Diagram", "web_url": "/pages/456"}]

    monkeypatch.setattr(confluence_router, "find_pages", _fake)
    r = _client().get("/v1/confluence/spaces/LOOKIN/pages", params={"title": "Network", "limit": 10})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["page_id"] == "456"


def test_list_diagrams_502_on_confluence_connection_error(monkeypatch):
    async def _fake(page_id):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(confluence_router, "list_diagrams", _fake)
    r = _client().get("/v1/confluence/pages/123/diagrams")
    assert r.status_code == 502
    assert "Connection refused" in r.json()["detail"]


def test_list_diagrams_502_on_unsupported_protocol(monkeypatch):
    async def _fake(page_id):
        raise httpx.UnsupportedProtocol("Request URL has an unsupported protocol 'htps://'.")

    monkeypatch.setattr(confluence_router, "list_diagrams", _fake)
    r = _client().get("/v1/confluence/pages/123/diagrams")
    assert r.status_code == 502
    assert "unsupported protocol" in r.json()["detail"].lower()


def test_find_pages_502_on_confluence_connection_error(monkeypatch):
    async def _fake(space_key, title_query="", limit=25):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(confluence_router, "find_pages", _fake)
    r = _client().get("/v1/confluence/spaces/LOOKIN/pages")
    assert r.status_code == 502
    assert "Connection refused" in r.json()["detail"]
