import asyncio

import pytest

from app.confluence import service
from app.confluence.client import ConfluenceConfigError


class _FakeClient:
    """Stand-in for ConfluenceClient — avoids real HTTP in unit tests."""

    def __init__(self, body_xml: str, attachments: list[dict], download_bytes: bytes = b""):
        self.body_xml = body_xml
        self.attachments = attachments
        self.download_bytes = download_bytes
        self.created: list[tuple] = []
        self.updated: list[tuple] = []

    async def get_page_body(self, page_id: str) -> str:
        return self.body_xml

    async def list_attachments(self, page_id: str) -> list[dict]:
        return self.attachments

    async def download_attachment(self, download_path: str) -> bytes:
        return self.download_bytes

    async def create_attachment(self, page_id: str, filename: str, content: bytes) -> dict:
        self.created.append((page_id, filename, content))
        return {"id": "new-att-1", "version": {"number": 1}}

    async def update_attachment_data(self, page_id: str, attachment_id: str, filename: str, content: bytes) -> dict:
        self.updated.append((page_id, attachment_id, filename, content))
        return {"id": attachment_id, "version": {"number": 2}}


_BODY = """
<ac:structured-macro ac:name="drawio">
  <ac:parameter ac:name="diagramName">architecture</ac:parameter>
</ac:structured-macro>
"""

_ATTACHMENTS = [
    {
        "id": "att1",
        "title": "architecture.drawio",
        "version": {"number": 3},
        "_links": {"download": "/download/attachments/1/architecture.drawio"},
    },
    {
        "id": "att2",
        "title": "standalone.drawio",
        "version": {"number": 1},
        "_links": {"download": "/download/attachments/1/standalone.drawio"},
    },
]


def test_list_diagrams_includes_inline_and_standalone(monkeypatch):
    fake = _FakeClient(_BODY, _ATTACHMENTS)
    monkeypatch.setattr(service, "_client", lambda: fake)

    items = asyncio.run(service.list_diagrams("123"))

    by_name = {i["diagram_name"]: i for i in items}
    assert by_name["architecture"]["attachment_id"] == "att1"
    assert by_name["architecture"]["inline"] is True
    assert by_name["standalone"]["attachment_id"] == "att2"
    assert by_name["standalone"]["inline"] is False


def test_get_diagram_xml_returns_decoded_content(monkeypatch):
    fake = _FakeClient(_BODY, _ATTACHMENTS, download_bytes=b"<mxGraphModel/>")
    monkeypatch.setattr(service, "_client", lambda: fake)

    xml = asyncio.run(service.get_diagram_xml("123", "architecture"))

    assert xml == "<mxGraphModel/>"


def test_get_diagram_xml_raises_not_found(monkeypatch):
    fake = _FakeClient(_BODY, _ATTACHMENTS)
    monkeypatch.setattr(service, "_client", lambda: fake)

    with pytest.raises(service.DiagramNotFoundError):
        asyncio.run(service.get_diagram_xml("123", "does-not-exist"))


def test_put_diagram_xml_updates_existing_attachment(monkeypatch):
    fake = _FakeClient(_BODY, _ATTACHMENTS)
    monkeypatch.setattr(service, "_client", lambda: fake)

    result = asyncio.run(service.put_diagram_xml("123", "architecture", "<mxGraphModel/>"))

    assert result == {"attachment_id": "att1", "version": 2}
    assert fake.updated[0][1] == "att1"


def test_put_diagram_xml_creates_new_attachment_when_absent(monkeypatch):
    fake = _FakeClient(_BODY, [])
    monkeypatch.setattr(service, "_client", lambda: fake)

    result = asyncio.run(service.put_diagram_xml("123", "brand-new", "<mxGraphModel/>"))

    assert result == {"attachment_id": "new-att-1", "version": 1}
    assert fake.created[0][1] == "brand-new.drawio"
