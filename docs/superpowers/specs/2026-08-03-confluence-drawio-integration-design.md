# Confluence draw.io Diagram Integration — Design

## Problem

citec-kb's Confluence integration currently only ingests page text (`confluence_docs` → stored as `tech_repo`) via batch file import. There is no way for a Claude session (via the citec-kb MCP server) to read or write the draw.io diagrams that live on Confluence pages — neither the diagram attachments themselves nor the inline `drawio` macros that embed them in the page body. Confluence's own MCP integration doesn't support attachment download/upload, so this gap has to be closed in citec-kb.

## Goal

Let a Claude session, through the citec-kb MCP server, on a given Confluence page:
1. List the draw.io diagrams present on the page (from inline `drawio` macros in the body, resolved to their backing attachments).
2. Read a diagram's raw `.drawio` XML.
3. Write (create or update) a diagram's `.drawio` XML from Claude-generated content, so it shows up correctly wherever the page's `drawio` macro already references it.

Rendering to PNG/SVG, general page body read/write, and non-draw.io macro types are explicitly out of scope — page text is already handled by existing ingestion, and Claude only needs the editable XML source, not a rendered preview.

## Architecture

Follows the existing citec-kb pattern: the MCP server is a thin protocol adapter; all HTTP/parsing logic lives in `apps/api`.

```
Claude ↔ mcp-server (proxy, new tools) ↔ apps/api /v1/confluence/* (new router) ↔ Confluence REST API (Bearer PAT)
```

### 1. Settings (`apps/api/app/settings.py`)

Add two fields, following the existing `Field(default=..., alias="ENV_VAR")` convention:

- `confluence_base_url: str | None` (alias `CONFLUENCE_BASE_URL`) — e.g. `https://confluence.internal.example.com`
- `confluence_username: str | None` (alias `CONFLUENCE_USERNAME`)
- `confluence_password: str | None` (alias `CONFLUENCE_PASSWORD`)

Auth is HTTP Basic (`username:password`), not a token — this org's Confluence is set up for Basic Auth, not PATs. If the base URL or either credential is unset, the confluence router's endpoints return a clear 503 rather than failing with a confusing connection error.

### 2. Confluence client (`apps/api/app/integrations/confluence_client.py`, new module)

A small async httpx wrapper (mirrors the style of `mcp-server/server.py`'s `_client()` helper, but server-side):

- `get_page_body(page_id) -> str` — `GET /rest/api/content/{id}?expand=body.storage` → the storage-format body XML.
- `list_attachments(page_id) -> list[dict]` — `GET /rest/api/content/{id}/child/attachment` → each with `id`, `title`, `_links.download`.
- `download_attachment(download_link) -> bytes` — fetches the raw attachment bytes.
- `upload_attachment(page_id, filename, content, existing_attachment_id=None)` — `POST /rest/api/content/{id}/child/attachment` (create) or `POST /rest/api/content/{id}/child/attachment/{attachmentId}/data` (new version), matching Confluence's attachment API semantics — creates the version Confluence expects when a macro already references that filename.

All requests carry HTTP Basic Auth built from `settings.confluence_username` / `settings.confluence_password` (httpx's `auth=(username, password)`).

### 3. Macro parser (same module or `apps/api/app/integrations/drawio_macro.py`)

Parses the page's storage-format body for `draw.io Diagrams for Confluence` macros:

```xml
<ac:structured-macro ac:name="drawio">
  <ac:parameter ac:name="diagramName">architecture</ac:parameter>
  <ac:parameter ac:name="revision">3</ac:parameter>
</ac:structured-macro>
```

`extract_drawio_diagram_names(body_xml) -> list[str]` returns the `diagramName` values found. These are then matched against the page's attachment list — primarily by filename (`{diagramName}.drawio`), since Confluence may adjust the stored filename (spaces, extensions); the match falls back to a case-insensitive stem comparison if an exact match isn't found. Diagrams that only exist as standalone `.drawio` attachments (no macro in the body) are also included in the list, flagged as `inline=False`.

### 4. Router (`apps/api/app/routers/confluence.py`, new)

Registered alongside the other routers in `apps/api/app/routers/__init__.py` / main app wiring (follow whatever pattern `failure_buckets.py` or `checkitems.py` uses for registration).

- `GET /v1/confluence/pages/{page_id}/diagrams`
  → `{"items": [{"diagram_name": "architecture", "attachment_id": "...", "version": 3, "inline": true}, ...]}`
- `GET /v1/confluence/pages/{page_id}/diagrams/{diagram_name}`
  → raw `.drawio` XML as `text/xml` (404 if no matching attachment)
- `PUT /v1/confluence/pages/{page_id}/diagrams/{diagram_name}` (body: raw XML)
  → uploads as a new attachment version if one exists for that name, else creates a new attachment named `{diagram_name}.drawio`. Returns the new version number/attachment id. Note: this does not insert a `drawio` macro into the page body if one doesn't already exist — it only updates/creates the attachment. If no macro exists yet, the diagram is uploaded but won't be visible on the page until an editor adds the macro manually (acceptable per scope: body-macro editing is out of scope, only *detecting* existing macros is in scope).

### 5. MCP tools (`mcp-server/server.py`)

Three new tools mirroring the router 1:1, following the existing `kb_*` tool conventions (Korean docstrings, `_client()` proxy pattern, `_err()` on `httpx.HTTPError`):

- `kb_confluence_list_diagrams(page_id: str) -> str`
- `kb_confluence_get_diagram(page_id: str, diagram_name: str) -> str`
- `kb_confluence_put_diagram(page_id: str, diagram_name: str, xml_content: str) -> str`

## Error handling

- Missing `CONFLUENCE_BASE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_PASSWORD` → 503 with a message telling the operator to set them.
- Confluence 404 (page/attachment not found) → passed through as 404 with a clear message.
- Confluence 401/403 (bad credentials) → passed through as 502 with a message pointing at the credential config (avoid leaking the password itself in logs/errors).
- Malformed body storage XML → macro parser fails soft (returns empty list, page's attachments are still checked for standalone `.drawio` files) rather than raising.

## Testing

- Unit tests for `extract_drawio_diagram_names` against sample storage-format XML fixtures (single macro, multiple macros, no macros, malformed XML).
- Router tests with a mocked `confluence_client` (respx or monkeypatched httpx, matching the pattern in `apps/api/tests/test_external_compat.py`) covering: list/get/put happy paths, 404s, and missing-config 503.
- MCP smoke test addition in `mcp-server/test_smoke.py` for the three new tools against a mocked or real dev API.

## Config / docs

- Document `CONFLUENCE_BASE_URL` / `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` in `.env.example` (or wherever other integration env vars are documented) and in `docs/MCP.md`'s tool table.
