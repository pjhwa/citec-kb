# Confluence draw.io Diagram Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Claude session, through the citec-kb MCP server, list/read/write draw.io diagrams (`.drawio` XML) attached to Confluence pages, including diagrams referenced by inline `drawio` macros in the page body.

**Architecture:** New `apps/api/app/confluence/` package (macro parser + Confluence REST client + orchestration service) backing a new `apps/api/app/routers/confluence.py` FastAPI router (`/v1/confluence/*`), proxied by three new tools in `mcp-server/server.py`. Mirrors the existing `failure_buckets` package/router split.

**Tech Stack:** Python, FastAPI, httpx (async), pytest, pydantic-settings. Confluence REST API v1 (`/rest/api/content/...`) with HTTP Basic Auth (`username`/`password`).

Spec: `docs/superpowers/specs/2026-08-03-confluence-drawio-integration-design.md`

---

## Task 1: Settings — Confluence base URL + Basic Auth credentials

**Files:**
- Modify: `apps/api/app/settings.py`
- Test: `apps/api/tests/test_confluence_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_confluence_settings.py
from app.settings import Settings, get_settings


def test_confluence_settings_default_to_none(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_BASE_URL", raising=False)
    monkeypatch.delenv("CONFLUENCE_USERNAME", raising=False)
    monkeypatch.delenv("CONFLUENCE_PASSWORD", raising=False)
    s = Settings()
    assert s.confluence_base_url is None
    assert s.confluence_username is None
    assert s.confluence_password is None


def test_confluence_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://confluence.internal.example.com")
    monkeypatch.setenv("CONFLUENCE_USERNAME", "svc-citec-kb")
    monkeypatch.setenv("CONFLUENCE_PASSWORD", "secret-pw")
    get_settings.cache_clear()
    s = Settings()
    assert s.confluence_base_url == "https://confluence.internal.example.com"
    assert s.confluence_username == "svc-citec-kb"
    assert s.confluence_password == "secret-pw"
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m pytest tests/test_confluence_settings.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'confluence_base_url'`

- [ ] **Step 3: Add the settings fields**

In `apps/api/app/settings.py`, add after the `raw_dir` field (around line 69):

```python
    confluence_base_url: str | None = Field(default=None, alias="CONFLUENCE_BASE_URL")
    confluence_username: str | None = Field(default=None, alias="CONFLUENCE_USERNAME")
    confluence_password: str | None = Field(default=None, alias="CONFLUENCE_PASSWORD")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && python -m pytest tests/test_confluence_settings.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/settings.py apps/api/tests/test_confluence_settings.py
git commit -m "feat(api): add CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD settings"
```

---

## Task 2: drawio macro parser

**Files:**
- Create: `apps/api/app/confluence/__init__.py`
- Create: `apps/api/app/confluence/macro.py`
- Test: `apps/api/tests/test_confluence_macro.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_confluence_macro.py
from app.confluence.macro import extract_drawio_diagram_names, match_attachment_for_diagram

_SINGLE_MACRO_BODY = """
<p>intro</p>
<ac:structured-macro ac:name="drawio" ac:schema-version="1">
  <ac:parameter ac:name="diagramName">architecture</ac:parameter>
  <ac:parameter ac:name="revision">3</ac:parameter>
</ac:structured-macro>
<p>outro</p>
"""

_MULTI_MACRO_BODY = """
<ac:structured-macro ac:name="drawio">
  <ac:parameter ac:name="diagramName">network-topology</ac:parameter>
</ac:structured-macro>
<p>text between</p>
<ac:structured-macro ac:name="drawio">
  <ac:parameter ac:name="diagramName">deployment flow</ac:parameter>
</ac:structured-macro>
"""

_NO_MACRO_BODY = "<p>just text, no diagrams here</p>"

_MALFORMED_BODY = "<ac:structured-macro ac:name=\"drawio\"><ac:parameter ac:name=\"diagramName\">broken"


def test_extract_single_macro():
    assert extract_drawio_diagram_names(_SINGLE_MACRO_BODY) == ["architecture"]


def test_extract_multiple_macros():
    assert extract_drawio_diagram_names(_MULTI_MACRO_BODY) == [
        "network-topology",
        "deployment flow",
    ]


def test_extract_no_macros():
    assert extract_drawio_diagram_names(_NO_MACRO_BODY) == []


def test_extract_empty_body():
    assert extract_drawio_diagram_names("") == []


def test_extract_malformed_body_fails_soft():
    assert extract_drawio_diagram_names(_MALFORMED_BODY) == []


def test_match_attachment_exact_filename():
    attachments = [
        {"id": "att1", "title": "architecture.drawio"},
        {"id": "att2", "title": "unrelated.png"},
    ]
    match = match_attachment_for_diagram("architecture", attachments)
    assert match == {"id": "att1", "title": "architecture.drawio"}


def test_match_attachment_case_insensitive_stem_fallback():
    attachments = [{"id": "att3", "title": "Network-Topology.drawio"}]
    match = match_attachment_for_diagram("network-topology", attachments)
    assert match == {"id": "att3", "title": "Network-Topology.drawio"}


def test_match_attachment_none_found():
    attachments = [{"id": "att1", "title": "other.drawio"}]
    assert match_attachment_for_diagram("missing", attachments) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && python -m pytest tests/test_confluence_macro.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.confluence'`

- [ ] **Step 3: Create the package and implement the parser**

```python
# apps/api/app/confluence/__init__.py
"""Confluence integration: draw.io diagram read/write for citec-kb."""
```

```python
# apps/api/app/confluence/macro.py
"""Parses draw.io Diagrams for Confluence macros out of storage-format page bodies.

Uses regex instead of a strict XML parser deliberately: page bodies can contain
other macros/HTML that aren't valid standalone XML fragments, and a malformed
or unrelated fragment must not blow up diagram listing — it should just fail
soft (return no matches) so the rest of the page's diagrams still resolve.
"""

from __future__ import annotations

import re

_DRAWIO_MACRO_RE = re.compile(
    r'<ac:structured-macro[^>]*ac:name="drawio"[^>]*>(.*?)</ac:structured-macro>',
    re.DOTALL,
)
_DIAGRAM_NAME_PARAM_RE = re.compile(
    r'<ac:parameter[^>]*ac:name="diagramName"[^>]*>(.*?)</ac:parameter>',
    re.DOTALL,
)


def extract_drawio_diagram_names(body_storage_xml: str) -> list[str]:
    """Return diagramName values (in document order) of drawio macros in a
    Confluence storage-format page body. Returns [] for empty/no-match/malformed input."""
    if not body_storage_xml:
        return []
    names: list[str] = []
    for macro_match in _DRAWIO_MACRO_RE.finditer(body_storage_xml):
        param_match = _DIAGRAM_NAME_PARAM_RE.search(macro_match.group(1))
        if param_match:
            names.append(param_match.group(1).strip())
    return names


def match_attachment_for_diagram(diagram_name: str, attachments: list[dict]) -> dict | None:
    """Resolve a diagram name to its backing Confluence attachment.

    Tries an exact "<diagram_name>.drawio" filename match first, then falls
    back to a case-insensitive filename-stem comparison (Confluence can
    normalize whitespace/case in stored attachment titles).
    """
    exact = f"{diagram_name}.drawio"
    for att in attachments:
        if att.get("title") == exact:
            return att
    stem = diagram_name.strip().lower()
    for att in attachments:
        title = att.get("title") or ""
        if "." in title and title.rsplit(".", 1)[0].lower() == stem:
            return att
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && python -m pytest tests/test_confluence_macro.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/confluence/__init__.py apps/api/app/confluence/macro.py apps/api/tests/test_confluence_macro.py
git commit -m "feat(api): parse drawio macros from Confluence page bodies"
```

---

## Task 3: Confluence REST client

**Files:**
- Create: `apps/api/app/confluence/client.py`
- Test: `apps/api/tests/test_confluence_client.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_confluence_client.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m pytest tests/test_confluence_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.confluence.client'`

- [ ] **Step 3: Implement the client**

```python
# apps/api/app/confluence/client.py
"""Thin async httpx wrapper over the Confluence REST API (v1, /rest/api/*)."""

from __future__ import annotations

from typing import Any

import httpx

from app.settings import Settings


class ConfluenceConfigError(RuntimeError):
    """CONFLUENCE_BASE_URL / CONFLUENCE_USERNAME / CONFLUENCE_PASSWORD not configured."""


class ConfluenceClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.confluence_base_url or not settings.confluence_username or not settings.confluence_password:
            raise ConfluenceConfigError(
                "CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD "
                "is not configured on the API server"
            )
        self._base_url = settings.confluence_base_url.rstrip("/")
        self._auth = (settings.confluence_username, settings.confluence_password)

    def _http(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            auth=self._auth,
            headers={"Accept": "application/json"},
        )

    async def get_page_body(self, page_id: str) -> str:
        async with self._http() as client:
            resp = await client.get(
                f"/rest/api/content/{page_id}",
                params={"expand": "body.storage"},
            )
            resp.raise_for_status()
            data = resp.json()
        return ((data.get("body") or {}).get("storage") or {}).get("value") or ""

    async def list_attachments(self, page_id: str) -> list[dict[str, Any]]:
        async with self._http() as client:
            resp = await client.get(
                f"/rest/api/content/{page_id}/child/attachment",
                params={"limit": 200},
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("results") or []

    async def download_attachment(self, download_path: str) -> bytes:
        async with self._http(timeout=60.0) as client:
            resp = await client.get(download_path)
            resp.raise_for_status()
            return resp.content

    async def create_attachment(self, page_id: str, filename: str, content: bytes) -> dict[str, Any]:
        async with self._http(timeout=60.0) as client:
            resp = await client.post(
                f"/rest/api/content/{page_id}/child/attachment",
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, content, "application/xml")},
            )
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") or [data]
        return results[0]

    async def update_attachment_data(
        self, page_id: str, attachment_id: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        async with self._http(timeout=60.0) as client:
            resp = await client.post(
                f"/rest/api/content/{page_id}/child/attachment/{attachment_id}/data",
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, content, "application/xml")},
            )
            resp.raise_for_status()
            return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && python -m pytest tests/test_confluence_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/confluence/client.py apps/api/tests/test_confluence_client.py
git commit -m "feat(api): add Confluence REST client for diagram attachments"
```

---

## Task 4: Orchestration service (list/get/put diagrams)

**Files:**
- Create: `apps/api/app/confluence/service.py`
- Test: `apps/api/tests/test_confluence_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_confluence_service.py
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
```

Tests use `asyncio.run(...)` inside plain `def test_...` functions rather than `async def` + `pytest-asyncio`, since this repo has neither `pytest-asyncio` installed nor any existing async tests (verified: `grep -i pytest-asyncio apps/api/requirements*.txt` and `grep -rl "async def test_" apps/api/tests` are both empty) — no new test dependency needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && python -m pytest tests/test_confluence_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.confluence.service'`

- [ ] **Step 3: Implement the service**

```python
# apps/api/app/confluence/service.py
"""Orchestrates ConfluenceClient + macro parsing into diagram list/get/put operations."""

from __future__ import annotations

from typing import Any

from app.confluence.client import ConfluenceClient
from app.confluence.macro import extract_drawio_diagram_names, match_attachment_for_diagram
from app.settings import get_settings


class DiagramNotFoundError(RuntimeError):
    """No attachment could be resolved for the requested diagram name."""


def _client() -> ConfluenceClient:
    return ConfluenceClient(get_settings())


async def list_diagrams(page_id: str) -> list[dict[str, Any]]:
    client = _client()
    body_xml = await client.get_page_body(page_id)
    attachments = await client.list_attachments(page_id)
    inline_names = extract_drawio_diagram_names(body_xml)

    items: list[dict[str, Any]] = []
    seen_attachment_ids: set[str] = set()
    for name in inline_names:
        att = match_attachment_for_diagram(name, attachments)
        items.append(
            {
                "diagram_name": name,
                "attachment_id": att.get("id") if att else None,
                "version": ((att or {}).get("version") or {}).get("number"),
                "inline": True,
            }
        )
        if att and att.get("id"):
            seen_attachment_ids.add(att["id"])

    for att in attachments:
        title = att.get("title") or ""
        if not title.endswith(".drawio"):
            continue
        if att.get("id") in seen_attachment_ids:
            continue
        items.append(
            {
                "diagram_name": title[: -len(".drawio")],
                "attachment_id": att.get("id"),
                "version": (att.get("version") or {}).get("number"),
                "inline": False,
            }
        )
    return items


async def get_diagram_xml(page_id: str, diagram_name: str) -> str:
    client = _client()
    attachments = await client.list_attachments(page_id)
    att = match_attachment_for_diagram(diagram_name, attachments)
    if not att:
        raise DiagramNotFoundError(diagram_name)
    download_link = (att.get("_links") or {}).get("download") or ""
    if not download_link:
        raise DiagramNotFoundError(diagram_name)
    content = await client.download_attachment(download_link)
    return content.decode("utf-8")


async def put_diagram_xml(page_id: str, diagram_name: str, xml_content: str) -> dict[str, Any]:
    client = _client()
    attachments = await client.list_attachments(page_id)
    att = match_attachment_for_diagram(diagram_name, attachments)
    filename = f"{diagram_name}.drawio"
    content_bytes = xml_content.encode("utf-8")
    if att:
        result = await client.update_attachment_data(page_id, att["id"], filename, content_bytes)
    else:
        result = await client.create_attachment(page_id, filename, content_bytes)
    return {
        "attachment_id": result.get("id"),
        "version": (result.get("version") or {}).get("number"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && python -m pytest tests/test_confluence_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/confluence/service.py apps/api/tests/test_confluence_service.py
git commit -m "feat(api): orchestrate Confluence diagram list/get/put"
```

---

## Task 5: FastAPI router + registration

**Files:**
- Create: `apps/api/app/routers/confluence.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_confluence_router.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_confluence_router.py
import httpx
import pytest
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


async def _raise_http_error(status_code):
    async def _inner(*_args, **_kwargs):
        raise _http_error(status_code)

    return _inner


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && python -m pytest tests/test_confluence_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.confluence'`

- [ ] **Step 3: Implement the router**

```python
# apps/api/app/routers/confluence.py
"""Confluence draw.io diagram API — list/get/put diagrams on a page."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.confluence.client import ConfluenceConfigError
from app.confluence.service import (
    DiagramNotFoundError,
    get_diagram_xml,
    list_diagrams,
    put_diagram_xml,
)

router = APIRouter(prefix="/v1/confluence", tags=["confluence"])


def _map_http_error(e: httpx.HTTPStatusError) -> HTTPException:
    status = e.response.status_code
    if status == 404:
        return HTTPException(status_code=404, detail="confluence resource not found")
    if status in (401, 403):
        return HTTPException(status_code=502, detail="confluence auth failed — check CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD")
    return HTTPException(status_code=502, detail=f"confluence error: {status}")


@router.get("/pages/{page_id}/diagrams")
async def get_page_diagrams(page_id: str) -> dict[str, Any]:
    try:
        items = await list_diagrams(page_id)
    except ConfluenceConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except httpx.HTTPStatusError as e:
        raise _map_http_error(e) from None
    return {"items": items, "total": len(items)}


@router.get("/pages/{page_id}/diagrams/{diagram_name}")
async def get_page_diagram(page_id: str, diagram_name: str) -> Response:
    try:
        xml_content = await get_diagram_xml(page_id, diagram_name)
    except ConfluenceConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except DiagramNotFoundError:
        raise HTTPException(status_code=404, detail=f"diagram not found: {diagram_name}") from None
    except httpx.HTTPStatusError as e:
        raise _map_http_error(e) from None
    return Response(content=xml_content, media_type="text/xml")


@router.put("/pages/{page_id}/diagrams/{diagram_name}")
async def put_page_diagram(page_id: str, diagram_name: str, request: Request) -> dict[str, Any]:
    body = await request.body()
    try:
        result = await put_diagram_xml(page_id, diagram_name, body.decode("utf-8"))
    except ConfluenceConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except httpx.HTTPStatusError as e:
        raise _map_http_error(e) from None
    return result
```

- [ ] **Step 4: Register the router in the app**

In `apps/api/app/main.py`, add the import near the other router imports (after line 29's `failure_buckets` import):

```python
from app.routers import confluence as confluence_router  # noqa: E402
```

And add the include near the other `app.include_router(...)` calls (after line 82's `failure_buckets_router`):

```python
app.include_router(confluence_router.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && python -m pytest tests/test_confluence_router.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the full API test suite to check for regressions**

Run: `cd apps/api && python -m pytest -v`
Expected: All tests PASS (no regressions from the new router/import)

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/routers/confluence.py apps/api/app/main.py apps/api/tests/test_confluence_router.py
git commit -m "feat(api): add /v1/confluence diagram list/get/put endpoints"
```

---

## Task 6: MCP tools

**Files:**
- Modify: `mcp-server/server.py`

- [ ] **Step 1: Add the three tools**

In `mcp-server/server.py`, add after the `kb_get_checkitem` tool (after line 896, before the `kb_register_failure_bucket` section):

```python
@mcp.tool()
async def kb_confluence_list_diagrams(page_id: str) -> str:
    """Confluence 페이지의 draw.io 다이어그램 목록을 조회한다 (본문 매크로 + 첨부파일 기준).
    page_id는 Confluence 페이지 ID(숫자 문자열)."""
    try:
        async with _client() as client:
            resp = await client.get(f"/v1/confluence/pages/{page_id}/diagrams")
            if resp.status_code == 503:
                return "오류: Confluence 연동이 설정되지 않았습니다 (CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD)."
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    items = data.get("items") or []
    if not items:
        return f"페이지 {page_id}에서 draw.io 다이어그램을 찾을 수 없습니다."
    lines = [f"다이어그램 {len(items)}건:"]
    for it in items:
        lines.append(
            f"- {it.get('diagram_name')} (attachment_id={it.get('attachment_id')}, "
            f"version={it.get('version')}, inline={it.get('inline')})"
        )
    return "\n".join(lines)


@mcp.tool()
async def kb_confluence_get_diagram(page_id: str, diagram_name: str) -> str:
    """Confluence 페이지의 draw.io 다이어그램 원본 XML을 조회한다.
    diagram_name은 kb_confluence_list_diagrams 결과의 diagram_name 값."""
    try:
        async with _client() as client:
            resp = await client.get(f"/v1/confluence/pages/{page_id}/diagrams/{diagram_name}")
            if resp.status_code == 503:
                return "오류: Confluence 연동이 설정되지 않았습니다 (CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD)."
            if resp.status_code == 404:
                return f"오류: 다이어그램을 찾을 수 없습니다: {diagram_name}"
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as e:
        return _err(e)


@mcp.tool()
async def kb_confluence_put_diagram(page_id: str, diagram_name: str, xml_content: str) -> str:
    """Claude가 생성/수정한 draw.io XML을 Confluence 페이지에 첨부파일로 업로드(갱신 또는 신규 생성)한다.
    기존 매크로가 이 diagram_name을 참조 중이면 페이지에 바로 반영된다."""
    try:
        async with _client() as client:
            resp = await client.put(
                f"/v1/confluence/pages/{page_id}/diagrams/{diagram_name}",
                content=xml_content.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
            if resp.status_code == 503:
                return "오류: Confluence 연동이 설정되지 않았습니다 (CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD)."
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return f"업로드됨: {diagram_name} (attachment_id={data.get('attachment_id')}, version={data.get('version')})"
```

- [ ] **Step 2: Add the tools to `kb_tools_help`**

In `mcp-server/server.py`, in the `kb_tools_help` docstring-returning function (around line 819-859), add a new section before the `[티켓 · Insight · 상태]` block:

```python
[Confluence draw.io 다이어그램]
  kb_confluence_list_diagrams(page_id=)          페이지의 다이어그램 목록
  kb_confluence_get_diagram(page_id=, diagram_name=)   원본 XML 조회
  kb_confluence_put_diagram(page_id=, diagram_name=, xml_content=)   업로드/갱신
```

- [ ] **Step 3: Manually verify the module imports cleanly**

Run: `cd mcp-server && python3 -c "import server"`
Expected: no output, exit code 0 (import succeeds — confirms no syntax errors in the new tool functions)

- [ ] **Step 4: Commit**

```bash
git add mcp-server/server.py
git commit -m "feat(mcp): add Confluence draw.io diagram tools"
```

---

## Task 7: Smoke test additions

**Files:**
- Modify: `mcp-server/test_smoke.py`

- [ ] **Step 1: Add smoke checks**

In `mcp-server/test_smoke.py`, add after the `kb_tools_help` check (after line 108, before the "connection failure" section):

```python
    cd = await server.kb_confluence_list_diagrams("0")
    check(
        "kb_confluence_list_diagrams",
        cd.startswith("오류: Confluence 연동이 설정되지 않았습니다")
        or "다이어그램" in cd
        or not cd.startswith("오류:"),
        cd[:200],
    )
```

This tolerates both states a dev/test API might be in: `CONFLUENCE_BASE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_PASSWORD` unset (503 → the specific Korean message) or configured against a real/reachable Confluence (any non-generic-error response).

- [ ] **Step 2: Run the smoke test against a local dev API**

Run: `CITEC_KB_BASE_URL=http://localhost:8573 python3 mcp-server/test_smoke.py`
Expected: `kb_confluence_list_diagrams` line shows `PASS` (other checks may fail/pass independently depending on local data — only confirm this new line passes and no new Python exceptions appear)

- [ ] **Step 3: Commit**

```bash
git add mcp-server/test_smoke.py
git commit -m "test(mcp): smoke-check Confluence diagram tool"
```

---

## Task 8: Env/docs

**Files:**
- Modify: `.env.example`
- Modify: `docs/MCP.md`

- [ ] **Step 1: Document the env vars**

In `.env.example`, add a new section (after the Auth/SSO section, before whatever comes next):

```bash
# --- Confluence (draw.io diagram read/write via MCP) ---
# Required for kb_confluence_* MCP tools / /v1/confluence/* endpoints.
# CONFLUENCE_BASE_URL=https://confluence.internal.example.com
# CONFLUENCE_USERNAME=
# CONFLUENCE_PASSWORD=
```

- [ ] **Step 2: Document the tools in docs/MCP.md**

In `docs/MCP.md`, add a new table row section after the "Insight · 상태" section (after line 71, before the `wiki_*` compatibility note on line 73):

```markdown
### Confluence draw.io 다이어그램

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_confluence_list_diagrams` | 페이지의 draw.io 다이어그램 목록(매크로+첨부) | `GET /v1/confluence/pages/{id}/diagrams` |
| `kb_confluence_get_diagram` | 다이어그램 원본 XML 조회 | `GET /v1/confluence/pages/{id}/diagrams/{name}` |
| `kb_confluence_put_diagram` | 다이어그램 XML 업로드/갱신 | `PUT /v1/confluence/pages/{id}/diagrams/{name}` |
```

Also add the env vars to the existing variable table (after line 113's `CITEC_KB_TOKEN` row):

```markdown
| `CONFLUENCE_BASE_URL` | (빈값, API 서버 설정) | Confluence 베이스 URL — 미설정 시 `kb_confluence_*` 도구는 503 오류 반환 |
| `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` | (빈값, API 서버 설정) | Confluence Basic Auth 자격 증명 (PAT 아님) |
```

(Note: `CONFLUENCE_BASE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_PASSWORD` are set on the **API server**, not the MCP container — the MCP-side table documents this for clarity since the MCP tools are the ones surfacing the 503.)

- [ ] **Step 3: Commit**

```bash
git add .env.example docs/MCP.md
git commit -m "docs: document Confluence draw.io env vars and MCP tools"
```

---

## Final check

- [ ] Run the full API test suite once more: `cd apps/api && python -m pytest -v` — expect all green.
- [ ] Run `cd mcp-server && python3 -c "import server"` once more — expect clean import.
- [ ] Review `git log --oneline -10` — expect one commit per task above, in order.
