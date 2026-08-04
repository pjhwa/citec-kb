import asyncio

import pytest

from app.confluence import service
from app.confluence.client import ConfluenceConfigError


class _FakeClient:
    """Stand-in for ConfluenceClient — avoids real HTTP in unit tests."""

    def __init__(
        self,
        body_xml: str,
        attachments: list[dict],
        download_bytes: bytes = b"",
        search_results: list[dict] | None = None,
    ):
        self.body_xml = body_xml
        self.attachments = attachments
        self.download_bytes = download_bytes
        self.created: list[tuple] = []
        self.updated: list[tuple] = []
        self.search_results = search_results or []
        self.searched: tuple | None = None

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

    async def search_pages_by_space(self, space_key: str, title_query: str = "", limit: int = 25) -> list[dict]:
        self.searched = (space_key, title_query, limit)
        return self.search_results


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


def test_list_diagrams_surfaces_candidate_titles_when_unmatched(monkeypatch):
    """When a macro's diagramName can't be matched to any attachment, surface
    ALL attachment titles present on the page (unfiltered by extension) so a
    naming-convention mismatch is diagnosable without extra tooling or
    assumptions about what extension this Confluence app actually uses (this
    is the exact gap that made the 'attachment_id=None' report opaque)."""
    body = """
    <ac:structured-macro ac:name="drawio">
      <ac:parameter ac:name="diagramName">Architecture Diagram</ac:parameter>
    </ac:structured-macro>
    """
    attachments = [
        {
            "id": "att-real",
            "title": "8f3c1e2a-91.drawio",
            "version": {"number": 2},
        },
        {"id": "att-png", "title": "8f3c1e2a-91.png", "version": {"number": 2}},
    ]
    fake = _FakeClient(body, attachments)
    monkeypatch.setattr(service, "_client", lambda: fake)

    items = asyncio.run(service.list_diagrams("123"))

    item = items[0]
    assert item["attachment_id"] is None
    assert item["candidate_attachment_titles"] == ["8f3c1e2a-91.drawio", "8f3c1e2a-91.png"]


def test_list_diagrams_candidate_titles_present_even_when_no_attachments(monkeypatch):
    """If the page truly has zero attachments, this must still show up as an
    explicit empty list, not silently vanish — an empty result is itself the
    diagnostic signal (nothing to match against at all)."""
    body = """
    <ac:structured-macro ac:name="drawio">
      <ac:parameter ac:name="diagramName">Orphan Diagram</ac:parameter>
    </ac:structured-macro>
    """
    fake = _FakeClient(body, [])
    monkeypatch.setattr(service, "_client", lambda: fake)

    items = asyncio.run(service.list_diagrams("123"))

    assert items[0]["candidate_attachment_titles"] == []


def test_list_diagrams_omits_candidate_titles_when_matched(monkeypatch):
    fake = _FakeClient(_BODY, _ATTACHMENTS)
    monkeypatch.setattr(service, "_client", lambda: fake)

    items = asyncio.run(service.list_diagrams("123"))

    assert "candidate_attachment_titles" not in items[0]


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


def test_get_diagram_xml_raises_format_error_on_non_utf8_bytes(monkeypatch):
    png_magic_bytes = b"\x89PNG\r\n\x1a\n"
    fake = _FakeClient(_BODY, _ATTACHMENTS, download_bytes=png_magic_bytes)
    monkeypatch.setattr(service, "_client", lambda: fake)

    with pytest.raises(service.DiagramFormatError) as exc_info:
        asyncio.run(service.get_diagram_xml("123", "architecture"))
    assert "att1" in str(exc_info.value)


def test_put_diagram_xml_updates_existing_attachment(monkeypatch):
    fake = _FakeClient(_BODY, _ATTACHMENTS)
    monkeypatch.setattr(service, "_client", lambda: fake)

    result = asyncio.run(service.put_diagram_xml("123", "architecture", "<mxGraphModel/>"))

    assert result == {"attachment_id": "att1", "version": 2}
    assert fake.updated[0][1] == "att1"


def test_put_diagram_xml_preserves_existing_attachment_filename(monkeypatch):
    """Some draw.io Confluence app versions store the source attachment with
    no extension at all (confirmed against real production data). Updating
    an existing attachment must reuse its actual title, not force a
    '<diagram_name>.drawio' filename — that would rename the attachment out
    from under the macro that references the original name."""
    attachments = [
        {
            "id": "att-bare",
            "title": "MAZ 가용성 테스트 구성도",
            "version": {"number": 3},
            "_links": {"download": "/download/attachments/1/MAZ"},
        },
    ]
    fake = _FakeClient(_BODY, attachments)
    monkeypatch.setattr(service, "_client", lambda: fake)

    asyncio.run(service.put_diagram_xml("123", "MAZ 가용성 테스트 구성도", "<mxGraphModel/>"))

    assert fake.updated[0][2] == "MAZ 가용성 테스트 구성도"


def test_put_diagram_xml_creates_new_attachment_when_absent(monkeypatch):
    fake = _FakeClient(_BODY, [])
    monkeypatch.setattr(service, "_client", lambda: fake)

    result = asyncio.run(service.put_diagram_xml("123", "brand-new", "<mxGraphModel/>"))

    assert result == {"attachment_id": "new-att-1", "version": 1}
    assert fake.created[0][1] == "brand-new.drawio"


def test_find_pages_returns_page_id_and_title(monkeypatch):
    fake = _FakeClient(
        _BODY,
        [],
        search_results=[
            {"id": "456", "title": "Network Diagram", "_links": {"webui": "/pages/456/Network+Diagram"}},
        ],
    )
    monkeypatch.setattr(service, "_client", lambda: fake)

    items = asyncio.run(service.find_pages("LOOKIN", title_query="Network", limit=10))

    assert items == [{"page_id": "456", "title": "Network Diagram", "web_url": "/pages/456/Network+Diagram"}]
    assert fake.searched == ("LOOKIN", "Network", 10)


def test_find_pages_empty_when_no_matches(monkeypatch):
    fake = _FakeClient(_BODY, [], search_results=[])
    monkeypatch.setattr(service, "_client", lambda: fake)

    items = asyncio.run(service.find_pages("LOOKIN"))

    assert items == []
