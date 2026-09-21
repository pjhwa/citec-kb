# Confluence Map Handoff Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the real, verified gaps between `~/tmp/x/confluence-map-handoff.zip`'s design doc and the confluence_map feature actually implemented in this repo (`apps/api/app/confluence/map_sync.py` + friends), without duplicating the working single-index search/crawl infrastructure the handoff's authors hadn't seen when they wrote it.

**Architecture:** Extend the existing structure-only crawler (`map_sync.py`) rather than building the handoff's separate `map_pages`/`map_page_search` tables or new MCP tools. Confluence pages already flow into the shared `documents`/`chunks` search index as `evidence_grade: "C"` pointer docs via `iter_confluence_map()` — every task below builds on that path. The one handoff requirement genuinely not closeable this way (per-user Confluence ACL delegation) is explicitly out of scope by user decision: confluence_map content is scoped to spaces/pages considered safe for all KB users, documented as a deliberate decision, not a silent gap.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (Postgres/JSONB), pytest, httpx (mocked Confluence in tests).

---

## Background (do not re-derive — read once, use throughout)

Confirmed by direct source inspection (not the handoff doc, which never saw this repo):

- `apps/api/app/confluence/map_sync.py`: `MAP_SOURCE_DEFS` (dict of `source_id -> {space_key, space_name, roots}`) drives a crawler that lists all descendants of each `roots` page ID via CQL (`ConfluenceClient.search_pages_incremental`), fetches each page's version+ancestors via `ConfluenceClient.get_page_meta`, and writes a frontmatter `.md` file per page under `data/raw/confluence_map/` via `_write_map_page`/`build_frontmatter_confluence_map`.
- `apps/api/app/ingest/adapters.py:iter_confluence_map()` reads those `.md` files into `DocumentDraft(source_type="confluence_map", evidence_grade="C", ...)`. `apps/api/app/ingest/pipeline.py:run_ingest()` upserts every draft as a `Document` row with a **fixed `source_id="fs_raw"`** — i.e. all 9 (soon 12) confluence_map sub-sources share one `Document.source_id`, so anything that needs to scope by "which MAP_SOURCE_DEFS entry" must filter on `Document.metadata_["space_key"].astext`, not `Document.source_id`.
- `Document.metadata_` (JSONB, GIN-indexed) holds the raw frontmatter key/value pairs as strings: `공간명`, `space_key`, `루트`, `Page ID`, `제목`, `URL`, `경로` (breadcrumb, `"A > B > C"` title chain), `최종수정일`, `유형` (`문서`|`폴더`).
- `apps/api/app/retrieval/search.py:_apply_doc_filters()` filters `Document.status == (filters.status or "active")` by default — setting `Document.status = "archived"` is sufficient to hide a document from `kb_query`/`kb_search`/`/v1/search` with zero other changes.
- `ConfluenceClient.get_page_meta(page_id)` (no `client=`/`limiter=` needed for single ad-hoc calls, but pass them when already inside a `bulk_client()` block) fetches `version`+`ancestors` for exactly one page ID — no CQL listing involved. This is the primitive for registering individual "seed" pages outside any curated root.
- Existing tests for this module live in `apps/api/tests/test_confluence_map_sync.py` (pure-function tests) and `apps/api/tests/test_confluence_map_crawl.py` (httpx-mocked crawl tests, no DB). DB-touching tests follow the opt-in pattern in `apps/api/tests/test_citec_dashboard_service.py`: skipped unless `CONFLUENCE_SYNC_TEST_DATABASE_URL` is set, and they set `os.environ["DATABASE_URL"]` from it plus clear `get_engine`/`get_session_factory`/`get_settings` caches in an autouse fixture.
- The 6 GitHub-question pages from the handoff's `evaluation-seed.json` and their real ancestor chains (verified 2026-09-16 by live Confluence read, recorded in the handoff package):
  - `155680474` "SDS GitHub info.(connection, policy, etc.)" — space `SPC`, chain `97897639 코딩 레퍼런스(CoCook) > 155680470 GitHub > (page)`
  - `383755011` "GitHub Q&A" — space `SPC`, same chain root
  - `1488175558` "Self-hosted Runner 구축하기" — space `GUID`, chain `7307288 DevOps Support Home > 59054364 Guide > 59055676 GitHub Guide > 1443164167 Using GitHub Actions > 1484788228 GitHub Actions Runner > (page)`
  - `1475349722` "GitHub 아이피 변경 적용" — space `DevOps001` (already in `MAP_SOURCE_DEFS`), but under `338021717 SCP인프라운영팀 Home > 1138707710 B. SCP시스템운영그룹 > 1232062646 개인별 업무공간 > 1232062969 이정수 > ... > (page)` — **outside all 3 curated roots already configured for that source**
  - `1268082455` "신규 SCP 프로젝트 구성시 추가 작업" — space `genaibusiness`, chain `1061726173 Gen.AI 사업팀 Home > 1443030175 Gen.AI서비스운영 > 1448397572 [SO-03] 인프라운영셀 > 1267643230 [SO-03-01] 아키텍처 > 1267643257 [SO-03-01] FabriX on SCP > (page)`
  - `2254661271` "Squid Proxy 전환" — space `Openstack101` (already in `MAP_SOURCE_DEFS`), but under `361297136 OPENSTACK PLATFORM : NURI Program > 416417708 Personal Space > 601850469 Personal Space - 정지원 > 2165314450 2026~Techops > (page)` — **outside all 4 curated roots already configured**
- User decision (this session, 2026-09-21): include both personal-workspace pages (`1475349722`, `2254661271`) as explicit seeds — they're already within the company's normal Confluence search visibility, this just adds them to the KB pointer index — but the code must say plainly that they came from a personal workspace, not a curated team root, matching this module's existing style of dated/attributed scope-decision comments (e.g. the "Approved 2026-09-16 (박재화)" block already in `map_sync.py`).
- User decision: the missing per-user ACL (nothing in `apps/api/app/auth/` links a KB caller's identity to Confluence, and `/v1/search` has no auth dependency at all) is resolved by **explicit scope limitation**, not by building delegated-permission checking. This plan documents that decision in code; it does not implement Confluence identity delegation.

---

## File Structure

- Modify: `apps/api/app/confluence/map_sync.py` — add `explicit_pages` support to `MAP_SOURCE_DEFS` entries, add `_crawl_explicit_pages()`, wire it into `_sync_map_body()`, add `run_map_inventory()`, add the ACL-scope-decision docstring block, register the 5 new/changed source entries.
- Modify: `apps/api/tests/test_confluence_map_sync.py` — update the "nine approved spaces" test to twelve, update the "each source has nonempty roots" test to allow `roots` **or** `explicit_pages`.
- Create: `apps/api/tests/test_confluence_map_explicit_seeds.py` — httpx-mocked tests for `_crawl_explicit_pages`, no DB.
- Create: `apps/api/tests/test_confluence_map_inventory_db.py` — opt-in DB tests for `run_map_inventory`.
- Modify: `apps/api/app/routers/confluence_map.py` — add `POST /v1/confluence-map/_run-inventory` (admin-only, mirrors `_run-sync`), add `GET /v1/confluence-map/children` (no admin gate, matches `/v1/search`'s current no-auth posture per the ACL scope decision).
- Create: `apps/api/tests/test_confluence_map_children_endpoint_db.py` — opt-in DB tests for the children endpoint.
- Modify: `apps/api/app/retrieval/search.py` — add `evidence_eligible` and `map_synced_at` fields to `SearchHit`, populate them in `hybrid_search()`.
- Create: `apps/api/tests/test_search_confluence_map_freshness_db.py` — opt-in DB tests for the new `SearchHit` fields.

---

## Task 1: Explicit single-page seed crawling

**Files:**
- Modify: `apps/api/app/confluence/map_sync.py`
- Test: `apps/api/tests/test_confluence_map_explicit_seeds.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_confluence_map_explicit_seeds.py`:

```python
"""Tests for app.confluence.map_sync._crawl_explicit_pages — fetching a
fixed list of individually-registered page IDs directly by ID (no CQL
ancestor listing). Mirrors test_confluence_map_crawl.py's style: httpx-mocked,
no live Confluence, no DB (tmp_path for raw_dir only).
"""

from __future__ import annotations

import asyncio

import httpx

from app.confluence.client import ConfluenceClient
from app.confluence.map_sync import _crawl_explicit_pages
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


def test_crawl_explicit_pages_writes_one_file_per_seed(tmp_path):
    client = _client()
    pages = {"111": "GitHub Q&A", "222": "SDS GitHub info."}

    def handler(request: httpx.Request) -> httpx.Response:
        page_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_page_meta_response(page_id, title=f"Title {page_id}"))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_explicit_pages(
            client,
            pages=pages,
            space_key="SPC",
            space_name="Coding References",
            raw_dir=tmp_path,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert len(result.written) == 2
    assert not result.errors
    written_ids = {w.page_id for w in result.written}
    assert written_ids == {"111", "222"}
    for w in result.written:
        assert w.path.exists()
        assert "SPC" in w.path.read_text(encoding="utf-8")


def test_crawl_explicit_pages_one_failure_does_not_drop_the_rest(tmp_path):
    client = _client()
    pages = {"111": "OK page", "222": "Broken page"}

    def handler(request: httpx.Request) -> httpx.Response:
        page_id = request.url.path.rsplit("/", 1)[-1]
        if page_id == "222":
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=_page_meta_response(page_id))

    _patch_bulk_client(client, handler)

    result = asyncio.run(
        _crawl_explicit_pages(
            client,
            pages=pages,
            space_key="SPC",
            space_name="Coding References",
            raw_dir=tmp_path,
            rps=0,
            tz_name="Asia/Seoul",
        )
    )
    assert len(result.written) == 1
    assert result.written[0].page_id == "111"
    assert len(result.errors) == 1
    assert result.errors[0]["page_id"] == "222"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_explicit_seeds.py -v`
Expected: FAIL with `ImportError: cannot import name '_crawl_explicit_pages'`

- [ ] **Step 3: Implement `_crawl_explicit_pages`**

In `apps/api/app/confluence/map_sync.py`, add this function immediately after `_crawl_map_source` (after its closing `return CrawlResult(written=written, errors=errors, cql_log=cql_log)` line):

```python
async def _crawl_explicit_pages(
    client: ConfluenceClient,
    *,
    pages: dict[str, str],
    space_key: str,
    space_name: str,
    raw_dir: Path,
    rps: float,
    tz_name: str,
) -> CrawlResult:
    """Fetch a fixed list of individually-registered page IDs directly by ID
    (ConfluenceClient.get_page_meta, no CQL ancestor listing) and write them
    with the same frontmatter shape _crawl_map_source uses.

    For "seed" registrations from MAP_SOURCE_DEFS[...]["explicit_pages"]: a
    specific page worth indexing whose containing subtree is not (and
    should not be) curated as a whole — e.g. a single page that happens to
    live under someone's personal workspace folder, where treating the
    whole personal space as a curated root would sweep in unrelated
    personal content. `pages` maps page_id -> a human-readable label used
    as this page's root_label in the frontmatter (there is no shared root
    page for these, so each carries its own descriptive label instead).
    """
    base_url = client._base_url
    limiter = RateLimiter(rps)
    written: list[WrittenPage] = []
    errors: list[dict[str, Any]] = []

    async with client.bulk_client() as http_client:
        for page_id, label in pages.items():
            try:
                meta = await client.get_page_meta(
                    page_id, client=http_client, limiter=limiter
                )
                wp = _write_map_page(
                    meta=meta,
                    root_label=label,
                    space_key=space_key,
                    space_name=space_name,
                    base_url=base_url,
                    tz_name=tz_name,
                    raw_dir=raw_dir,
                )
                written.append(wp)
            except Exception as exc:  # noqa: BLE001 — one bad seed must not drop the rest
                logger.exception(
                    "confluence map explicit seed fetch failed page_id=%s space=%s",
                    page_id, space_key,
                )
                errors.append({"page_id": page_id, "root_id": None, "error": str(exc)})

    return CrawlResult(written=written, errors=errors, cql_log=[])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_explicit_seeds.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire `explicit_pages` into the daily sync driver**

In `apps/api/app/confluence/map_sync.py`, in `_sync_map_body()`, find this block (right after the `result = asyncio.run(_crawl_map_source(...))` call closes):

```python
        total_attempted = len(result.written) + len(result.errors)
```

Replace it with:

```python
        explicit_pages = sd.get("explicit_pages") or {}
        if explicit_pages and root_id is None:
            # explicit seeds have no root_id to filter by — only run them
            # on untruncated/full-source syncs, same as --root-id smoke
            # tests skip checkpointing above.
            explicit_result = asyncio.run(
                _crawl_explicit_pages(
                    client,
                    pages=explicit_pages,
                    space_key=sd["space_key"],
                    space_name=sd["space_name"],
                    raw_dir=raw_root,
                    rps=settings.confluence_rate_limit_rps,
                    tz_name=settings.confluence_timezone,
                )
            )
            result.written.extend(explicit_result.written)
            result.errors.extend(explicit_result.errors)

        total_attempted = len(result.written) + len(result.errors)
```

- [ ] **Step 6: Run the full map_sync test suite to check nothing broke**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_sync.py tests/test_confluence_map_crawl.py tests/test_confluence_map_explicit_seeds.py -v`
Expected: All PASS (the two pre-existing `MAP_SOURCE_DEFS` tests still pass since no source's shape changed yet in this task)

- [ ] **Step 7: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/confluence/map_sync.py apps/api/tests/test_confluence_map_explicit_seeds.py
git commit -m "$(cat <<'EOF'
feat(confluence-map): support explicit single-page seeds

map_sync's crawler only knows how to list every descendant of a curated
root page — there was no way to register one specific page (e.g. a
GitHub-support doc buried under someone's personal workspace) without
either curating that entire personal space as a root or leaving the page
out of the map index entirely. _crawl_explicit_pages() fetches a fixed
list of page IDs directly via get_page_meta, reusing the existing
frontmatter writer, and _sync_map_body() runs it alongside each source's
normal root crawl when MAP_SOURCE_DEFS[...]["explicit_pages"] is set.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RCGcac9NYvFYa4vd57nHhk
EOF
)"
```

---

## Task 2: Register the 6 missing GitHub-question pages as seeds

**Files:**
- Modify: `apps/api/app/confluence/map_sync.py`
- Modify: `apps/api/tests/test_confluence_map_sync.py`

- [ ] **Step 1: Update the failing/soon-to-be-wrong assertions first**

In `apps/api/tests/test_confluence_map_sync.py`, replace the two existing tests:

```python
def test_map_source_defs_cover_all_nine_approved_spaces():
    # 4 whole-space (LOOKIN/TechRepo/ServiceExcellenceTeam/ICLOUDUT, promoted
    # 2026-09-16 from one-time migration to continuous daily crawl) + 5
    # curated-root new spaces. sysops intentionally excluded (no issue/KDB
    # content yet).
    assert set(MAP_SOURCE_DEFS.keys()) == {
        "confluence_map_lookin",
        "confluence_map_techrepo",
        "confluence_map_serviceexcellenceteam",
        "confluence_map_icloudut",
        "confluence_map_devops001",
        "confluence_map_openstack101",
        "confluence_map_cldeng",
        "confluence_map_dftrts",
        "confluence_map_emcloud",
    }


def test_map_source_defs_each_have_own_space_key_and_nonempty_roots():
    space_keys = set()
    for source_id, sd in MAP_SOURCE_DEFS.items():
        assert sd["roots"], f"{source_id} has no roots"
        space_keys.add(sd["space_key"])
    # each mapped space gets its own independent cursor (source_id) —
    # this is *why* map_sync isn't folded into sync.py's _SOURCE_DEFS
    assert len(space_keys) == len(MAP_SOURCE_DEFS)
```

with:

```python
def test_map_source_defs_cover_all_twelve_approved_spaces():
    # Original 9 (see git history) + 3 added 2026-09-21 to close the gap a
    # GitHub-connectivity KB question exposed: 6 real, live-verified answer
    # pages lived entirely outside the 4 original snapshot spaces, and 3 of
    # those 6 were in spaces the map didn't cover at all yet (SPC, GUID,
    # genaibusiness) — see docs/superpowers/plans/
    # 2026-09-21-confluence-map-handoff-gap-closure.md.
    assert set(MAP_SOURCE_DEFS.keys()) == {
        "confluence_map_lookin",
        "confluence_map_techrepo",
        "confluence_map_serviceexcellenceteam",
        "confluence_map_icloudut",
        "confluence_map_devops001",
        "confluence_map_openstack101",
        "confluence_map_cldeng",
        "confluence_map_dftrts",
        "confluence_map_emcloud",
        "confluence_map_spc",
        "confluence_map_guid",
        "confluence_map_genaibusiness",
    }


def test_map_source_defs_each_have_own_space_key_and_roots_or_explicit_pages():
    space_keys = set()
    for source_id, sd in MAP_SOURCE_DEFS.items():
        assert sd["roots"] or sd.get("explicit_pages"), (
            f"{source_id} has neither roots nor explicit_pages"
        )
        space_keys.add(sd["space_key"])
    # each mapped space gets its own independent cursor (source_id) —
    # this is *why* map_sync isn't folded into sync.py's _SOURCE_DEFS
    assert len(space_keys) == len(MAP_SOURCE_DEFS)


def test_map_source_defs_explicit_pages_scoped_to_the_six_github_question_pages():
    # The concrete gap this task closes — page IDs live-verified 2026-09-16
    # (see evaluation-seed.json in the handoff package). Two of the six
    # (DevOps001, Openstack101) sit under personal workspaces outside every
    # curated root already configured for those spaces, so they're
    # registered as explicit_pages on the *existing* source entries rather
    # than expanding those roots to cover the personal-workspace subtree.
    assert MAP_SOURCE_DEFS["confluence_map_spc"]["explicit_pages"].keys() == {
        "155680474", "383755011",
    }
    assert MAP_SOURCE_DEFS["confluence_map_guid"]["explicit_pages"].keys() == {
        "1488175558",
    }
    assert MAP_SOURCE_DEFS["confluence_map_genaibusiness"]["explicit_pages"].keys() == {
        "1268082455",
    }
    assert "1475349722" in MAP_SOURCE_DEFS["confluence_map_devops001"]["explicit_pages"]
    assert "2254661271" in MAP_SOURCE_DEFS["confluence_map_openstack101"]["explicit_pages"]
```

- [ ] **Step 2: Run to verify these fail**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_sync.py -v`
Expected: FAIL — `confluence_map_spc`/`confluence_map_guid`/`confluence_map_genaibusiness` don't exist yet, and `devops001`/`openstack101` have no `explicit_pages` key yet.

- [ ] **Step 3: Add the ACL scope-decision comment block and the 3 new source entries**

In `apps/api/app/confluence/map_sync.py`, immediately before the `MAP_SOURCE_DEFS: dict[str, dict[str, Any]] = {` line, add:

```python
# --- ACL scope decision (2026-09-21) -----------------------------------
# citec-kb has no mechanism to delegate a KB caller's identity to
# Confluence and check their live read permission before returning a
# search result — apps/api/app/auth/ is role-based only (viewer/author/
# senior/admin), and GET /v1/search has no auth dependency at all. A
# separate handoff design proposed building that delegation before any
# confluence_map rollout; this codebase makes the opposite call instead:
# every space/page registered below (roots or explicit_pages) is a
# deliberate scope decision that its title/breadcrumb/URL are safe to show
# to any KB user, not a temporary gap pending an ACL system. Do not add a
# space or page here on the assumption that per-user filtering will catch
# anything this list gets wrong — there is no such filtering, on this path
# or on kb_query/kb_search/kb_ask generally.
# -------------------------------------------------------------------------
```

Then, inside `MAP_SOURCE_DEFS`, after the closing `},` of the `confluence_map_emcloud` entry (the last one), add the 3 new explicit-only entries:

```python
    # Added 2026-09-21 to close a gap a GitHub-connectivity KB question
    # exposed: these 3 spaces had zero MAP_SOURCE_DEFS coverage, and the
    # relevant pages are individually-known-important rather than whole
    # curated subtrees worth crawling — see explicit_pages below and
    # docs/superpowers/plans/2026-09-21-confluence-map-handoff-gap-closure.md.
    "confluence_map_spc": {
        "space_key": "SPC",
        "space_name": "Coding References",
        "roots": {},
        "explicit_pages": {
            "155680474": "SDS GitHub info.(connection, policy, etc.)",
            "383755011": "GitHub Q&A",
        },
    },
    "confluence_map_guid": {
        "space_key": "GUID",
        "space_name": "DevOps Support",
        "roots": {},
        "explicit_pages": {
            "1488175558": "Self-hosted Runner 구축하기",
        },
    },
    "confluence_map_genaibusiness": {
        "space_key": "genaibusiness",
        "space_name": "Gen.AI 사업팀",
        "roots": {},
        "explicit_pages": {
            "1268082455": "신규 SCP 프로젝트 구성시 추가 작업",
        },
    },
```

- [ ] **Step 4: Add explicit_pages to the two existing sources with personal-workspace pages**

Still in `MAP_SOURCE_DEFS`, find the `confluence_map_devops001` entry:

```python
    "confluence_map_devops001": {
        "space_key": "DevOps001",
        "space_name": "SCP인프라운영팀",
        "roots": {
            "601879661": "005. 이슈/문제/KDB/SOP",
            "468085983": "006. SCP CASE study",
            "370644108": "★★ SCP (SCP SRE + SCP NW Share) ★★",
        },
    },
```

Replace with:

```python
    "confluence_map_devops001": {
        "space_key": "DevOps001",
        "space_name": "SCP인프라운영팀",
        "roots": {
            "601879661": "005. 이슈/문제/KDB/SOP",
            "468085983": "006. SCP CASE study",
            "370644108": "★★ SCP (SCP SRE + SCP NW Share) ★★",
        },
        # Added 2026-09-21: lives under 개인별 업무공간 > 이정수, outside all
        # 3 curated roots above. Registered as a single-page seed rather
        # than adding "개인별 업무공간" as a 4th root — that folder is one
        # person's personal workspace, not team-curated content, and
        # crawling it whole would sweep in unrelated personal pages.
        "explicit_pages": {
            "1475349722": "GitHub 아이피 변경 적용 (개인 업무공간 seed: 이정수 > R+ 거점서버 인수인계)",
        },
    },
```

Then find the `confluence_map_openstack101` entry:

```python
    "confluence_map_openstack101": {
        "space_key": "Openstack101",
        "space_name": "OPENSTACK PLATFORM",
        "roots": {
            "1148203906": "knowledge base",
            "2318695720": "9. 팀 ISSUE 관리",
            "1176274225": "Nuri 운영구성",
            "1204002143": "Nuri 운영 관련",
        },
    },
```

Replace with:

```python
    "confluence_map_openstack101": {
        "space_key": "Openstack101",
        "space_name": "OPENSTACK PLATFORM",
        "roots": {
            "1148203906": "knowledge base",
            "2318695720": "9. 팀 ISSUE 관리",
            "1176274225": "Nuri 운영구성",
            "1204002143": "Nuri 운영 관련",
        },
        # Added 2026-09-21: lives under Personal Space > 정지원, outside all
        # 4 curated roots above — same rationale as confluence_map_devops001's
        # explicit_pages.
        "explicit_pages": {
            "2254661271": "Squid Proxy 전환 (개인 업무공간 seed: 정지원 > 2026~Techops)",
        },
    },
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_sync.py tests/test_confluence_map_crawl.py tests/test_confluence_map_explicit_seeds.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/confluence/map_sync.py apps/api/tests/test_confluence_map_sync.py
git commit -m "$(cat <<'EOF'
feat(confluence-map): register the 6 GitHub-question seed pages

Closes the concrete gap a GitHub-connectivity KB question exposed: all 6
live-verified answer pages (see evaluation-seed.json in the handoff
package) sat outside the map's coverage. 3 spaces (SPC/GUID/genaibusiness)
had no MAP_SOURCE_DEFS entry at all; 2 more pages sat under personal
workspaces inside DevOps001/Openstack101, outside every curated root
already configured there. All 6 are registered as explicit_pages rather
than as new/expanded curated roots, since none of them warrant crawling
their whole containing subtree.

Also documents (in-code) the 2026-09-21 decision to close the missing
per-user-ACL gap by explicit scope limitation rather than building
Confluence identity delegation — every confluence_map source above is a
deliberate "safe for all KB users" call, not a placeholder pending ACL.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RCGcac9NYvFYa4vd57nHhk
EOF
)"
```

---

## Task 3: Weekly full-metadata inventory / reconciliation

**Files:**
- Modify: `apps/api/app/confluence/map_sync.py`
- Modify: `apps/api/app/routers/confluence_map.py`
- Create: `apps/api/tests/test_confluence_map_inventory_db.py`

- [ ] **Step 1: Write the failing DB test**

Create `apps/api/tests/test_confluence_map_inventory_db.py`:

```python
"""DB-touching tests for app.confluence.map_sync.run_map_inventory.

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
scratch Postgres DB with the alembic schema applied (same opt-in
convention as test_citec_dashboard_service.py) — never the live
citec_knowledge DB.
"""

from __future__ import annotations

import hashlib
import json
import os

import httpx
import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _seed_document(session, *, external_id: str, space_key: str, root_label: str, title: str):
    from app.db.models import Document

    meta = {
        "space_key": space_key,
        "루트": root_label,
        "Page ID": external_id,
        "제목": title,
        "경로": title,
    }
    payload = f"{title}\n\n{json.dumps(meta, sort_keys=True)}"
    doc = Document(
        id=f"test_inv:{external_id}",
        source_type="confluence_map",
        external_id=external_id,
        title=title,
        body_md=title,
        metadata_=meta,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        evidence_grade="C",
        status="active",
    )
    session.add(doc)


def test_run_map_inventory_archives_a_page_no_longer_returned(tmp_path, monkeypatch):
    from app.confluence.map_sync import MAP_SOURCE_DEFS, run_map_inventory
    from app.db.models import Document
    from app.db.session import session_scope
    from sqlalchemy import select

    source_id = "confluence_map_devops001"
    space_key = MAP_SOURCE_DEFS[source_id]["space_key"]

    with session_scope() as session:
        # "111" is still returned by the mocked full listing below; "999" is
        # a page that has since moved/been deleted and must be archived.
        _seed_document(session, external_id="111", space_key=space_key, root_label="R", title="Still here")
        _seed_document(session, external_id="999", space_key=space_key, root_label="R", title="Gone now")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            params = dict(request.url.params)
            if int(params["start"]) > 0:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"results": [{"id": "111"}]})
        return httpx.Response(
            200,
            json={
                "id": "111",
                "title": "Still here",
                "version": {"number": 2, "when": "2026-09-21T00:00:00.000+09:00"},
                "ancestors": [],
            },
        )

    def fake_bulk_client(self, timeout: float = 30.0):
        return httpx.AsyncClient(base_url=self._base_url, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.confluence.client.ConfluenceClient.bulk_client", fake_bulk_client)

    stats = run_map_inventory(source_id, tmp_path)

    assert stats["archived"] == ["999"]
    with session_scope() as session:
        gone = session.scalar(select(Document).where(Document.id == "test_inv:999"))
        still_here = session.scalar(select(Document).where(Document.id == "test_inv:111"))
        assert gone.status == "archived"
        assert still_here.status == "active"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<your-scratch-db-url> python -m pytest tests/test_confluence_map_inventory_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_map_inventory'`

If no scratch DB is available in this environment, skip running Step 2/4 live and rely on code review — but still write the test file exactly as above so it runs correctly wherever `CONFLUENCE_SYNC_TEST_DATABASE_URL` is set.

- [ ] **Step 3: Implement `run_map_inventory`**

In `apps/api/app/confluence/map_sync.py`, add near the top of the file's imports (after the existing `from app.db.models import Source` line):

```python
from app.db.models import Document, Source
```

Then add this function after `seed_cursor` (right before `def sync_map(`):

```python
def run_map_inventory(
    source_id: str,
    raw_dir: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Weekly full-metadata reconciliation for one confluence_map source.

    sync_map()'s daily crawl is incremental (CQL lastmodified > cursor) — a
    page that moves out of a root's subtree, gets relabeled, or is deleted
    never shows up in that incremental result, so its old confluence_map
    entry lingers in the search index forever (§5 "전체 메타데이터 대조" in
    the 2026-09-16 handoff design was right that this gap exists; it just
    hadn't seen this repo's actual incremental-only implementation).

    This re-lists every configured root in full (since=None, the same call
    the initial bootstrap crawl uses) and re-fetches every explicit_pages
    seed, then archives (Document.status="archived" — already filtered out
    of search by _apply_doc_filters' default status="active", so no new
    search-side logic is needed) any page previously indexed under this
    source's space_key that the fresh listing no longer returns.

    Deliberately does not touch last_sync_at/checkpoint — this runs on its
    own (weekly, ops-triggered) cadence independent of the daily
    incremental sync and must not perturb it.

    Scoping note: all confluence_map documents share Document.source_id=
    "fs_raw" (see app.ingest.pipeline.run_ingest — the filesystem adapter
    path doesn't know which MAP_SOURCE_DEFS entry a given file came from),
    so "previously indexed under this source" is determined by
    Document.metadata_["space_key"], not Document.source_id. This is safe
    because each space_key maps to exactly one MAP_SOURCE_DEFS entry (see
    test_map_source_defs_each_have_own_space_key_and_roots_or_explicit_pages).
    """
    raw_root = Path(raw_dir)
    settings = get_settings()
    client = ConfluenceClient(settings)
    if source_id not in MAP_SOURCE_DEFS:
        raise ValueError(f"unknown confluence_map source_id={source_id!r}")
    sd = MAP_SOURCE_DEFS[source_id]

    with session_scope() as session:
        previous_ids = {
            row[0]
            for row in session.execute(
                select(Document.external_id).where(
                    Document.source_type == "confluence_map",
                    Document.metadata_["space_key"].astext == sd["space_key"],
                    Document.status == "active",
                )
            ).all()
        }

    result = asyncio.run(
        _crawl_map_source(
            client,
            source_id=source_id,
            roots=sd["roots"],
            space_key=sd["space_key"],
            space_name=sd["space_name"],
            since=None,
            raw_dir=raw_root,
            max_pages_per_root=None,
            rps=settings.confluence_rate_limit_rps,
            tz_name=settings.confluence_timezone,
            use_checkpoint=False,
        )
    )
    explicit_pages = sd.get("explicit_pages") or {}
    if explicit_pages:
        explicit_result = asyncio.run(
            _crawl_explicit_pages(
                client,
                pages=explicit_pages,
                space_key=sd["space_key"],
                space_name=sd["space_name"],
                raw_dir=raw_root,
                rps=settings.confluence_rate_limit_rps,
                tz_name=settings.confluence_timezone,
            )
        )
        result.written.extend(explicit_result.written)
        result.errors.extend(explicit_result.errors)

    current_ids = {w.page_id for w in result.written}
    gone_ids = previous_ids - current_ids
    archived: list[str] = []
    if gone_ids and not dry_run:
        with session_scope() as session:
            rows = session.execute(
                select(Document).where(
                    Document.source_type == "confluence_map",
                    Document.metadata_["space_key"].astext == sd["space_key"],
                    Document.external_id.in_(gone_ids),
                    Document.status == "active",
                )
            ).scalars().all()
            for doc in rows:
                doc.status = "archived"
                archived.append(doc.external_id)

    if not dry_run:
        from app.ingest.pipeline import run_ingest

        run_ingest(raw_root, sources=["confluence_map"])

    return {
        "source_id": source_id,
        "dry_run": dry_run,
        "previous_count": len(previous_ids),
        "current_count": len(current_ids),
        "written": len(result.written),
        "errors": result.errors,
        "archived": sorted(archived),
    }
```

Add the missing `select` import: find the existing `from sqlalchemy import text` line near the top of the file and replace it with:

```python
from sqlalchemy import select, text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<your-scratch-db-url> python -m pytest tests/test_confluence_map_inventory_db.py -v`
Expected: PASS. If no scratch DB is available, at minimum run `python -c "import app.confluence.map_sync"` to confirm the module still imports cleanly with no syntax errors.

- [ ] **Step 5: Expose it via an admin-triggered endpoint**

In `apps/api/app/routers/confluence_map.py`, add after the existing `RunBody`/`run_sync` block:

```python
class InventoryBody(BaseModel):
    source_id: str
    dry_run: bool = False


@router.post("/_run-inventory")
def run_inventory(
    body: InventoryBody,
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    _ = principal
    from app.confluence.map_sync import run_map_inventory

    settings = get_settings()
    try:
        return run_map_inventory(body.source_id, settings.raw_dir, dry_run=body.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 6: Run the full confluence_map test suite**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_sync.py tests/test_confluence_map_crawl.py tests/test_confluence_map_explicit_seeds.py -v`
Expected: All PASS (inventory DB test runs separately, opt-in)

- [ ] **Step 7: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/confluence/map_sync.py apps/api/app/routers/confluence_map.py apps/api/tests/test_confluence_map_inventory_db.py
git commit -m "$(cat <<'EOF'
feat(confluence-map): weekly full-metadata inventory reconciliation

map_sync's daily crawl is incremental-only (CQL lastmodified > cursor), so
a page that moves out of its root's subtree, gets relabeled, or is
deleted never shows up in that result and its stale confluence_map entry
lingers in the search index indefinitely. run_map_inventory() re-lists
every root in full and re-verifies every explicit_pages seed, then
archives (Document.status="archived") any previously-indexed page under
that space the fresh listing no longer returns — already sufficient to
drop it from search via _apply_doc_filters' default active-only filter.
Exposed as POST /v1/confluence-map/_run-inventory (admin-only, mirrors the
existing _run-sync trigger) for the ops runbook to call weekly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RCGcac9NYvFYa4vd57nHhk
EOF
)"
```

---

## Task 4: Surface freshness + evidence-eligibility on search hits

**Files:**
- Modify: `apps/api/app/retrieval/search.py`
- Create: `apps/api/tests/test_search_confluence_map_freshness_db.py`

- [ ] **Step 1: Write the failing DB test**

Create `apps/api/tests/test_search_confluence_map_freshness_db.py`:

```python
"""DB-touching tests for the SearchHit.evidence_eligible / map_synced_at
fields added to app.retrieval.search. Same opt-in-DB convention as
test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _seed(session, *, source_type: str, external_id: str, title: str, evidence_grade: str):
    from app.db.models import Chunk, Document

    meta = {"space_key": "SPC"} if source_type == "confluence_map" else {}
    payload = f"{title}\n\n{json.dumps(meta, sort_keys=True)}"
    doc_id = f"test_fresh:{source_type}:{external_id}"
    doc = Document(
        id=doc_id,
        source_type=source_type,
        external_id=external_id,
        title=title,
        body_md=f"{title} unique_marker_freshness_test",
        metadata_=meta,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        evidence_grade=evidence_grade,
        status="active",
    )
    session.add(doc)
    session.flush()
    session.add(
        Chunk(
            id=f"{doc_id}:chunk",
            document_id=doc_id,
            ordinal=0,
            text=f"{title} unique_marker_freshness_test",
            header_context="",
        )
    )
    return doc_id


def test_confluence_map_hit_has_evidence_eligible_false_and_map_synced_at(tmp_path):
    from app.db.session import session_scope
    from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

    with session_scope() as session:
        _seed(
            session,
            source_type="confluence_map",
            external_id="unique_marker_freshness_test_page",
            title="unique_marker_freshness_test page",
            evidence_grade="C",
        )

    with session_scope() as session:
        resp = hybrid_search(
            session,
            SearchRequest(q="unique_marker_freshness_test", top_k=5, filters=SearchFilters()),
        )

    hits = [h for h in resp.results if h.source_type == "confluence_map"]
    assert hits, "expected the seeded confluence_map document to be found"
    assert hits[0].evidence_eligible is False
    assert hits[0].map_synced_at is not None


def test_non_confluence_map_hit_is_evidence_eligible_with_no_synced_at():
    from app.db.session import session_scope
    from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

    with session_scope() as session:
        _seed(
            session,
            source_type="tech_repo",
            external_id="unique_marker_freshness_test_doc",
            title="unique_marker_freshness_test doc",
            evidence_grade="A",
        )

    with session_scope() as session:
        resp = hybrid_search(
            session,
            SearchRequest(q="unique_marker_freshness_test", top_k=5, filters=SearchFilters()),
        )

    hits = [h for h in resp.results if h.source_type == "tech_repo"]
    assert hits, "expected the seeded tech_repo document to be found"
    assert hits[0].evidence_eligible is True
    assert hits[0].map_synced_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<your-scratch-db-url> python -m pytest tests/test_search_confluence_map_freshness_db.py -v`
Expected: FAIL with `TypeError: SearchHit.__init__() got an unexpected keyword argument` or `AttributeError: 'SearchHit' object has no attribute 'evidence_eligible'`

- [ ] **Step 3: Add the fields to `SearchHit` and populate them**

In `apps/api/app/retrieval/search.py`, find the `SearchHit` dataclass:

```python
@dataclass
class SearchHit:
    rank: int
    score: float
    document_id: str
    chunk_id: str
    title: str
    snippet: str
    source_type: str
    external_id: str
    evidence_grade: str
    domain: Optional[str]
    environment: Optional[str]
    work_type: Optional[str]
    path_l2: Optional[str]
    source_uri: Optional[str]
    fts_rank: Optional[int]
    vec_rank: Optional[int]
```

Replace with:

```python
@dataclass
class SearchHit:
    rank: int
    score: float
    document_id: str
    chunk_id: str
    title: str
    snippet: str
    source_type: str
    external_id: str
    evidence_grade: str
    domain: Optional[str]
    environment: Optional[str]
    work_type: Optional[str]
    path_l2: Optional[str]
    source_uri: Optional[str]
    fts_rank: Optional[int]
    vec_rank: Optional[int]
    # evidence_eligible=False marks a hit as a pointer, not verified
    # evidence — currently true only for evidence_grade="C" confluence_map
    # rows. Callers (incl. the AI answer path) must read the current
    # Confluence page before treating a False-flagged hit as an answer
    # basis, per the handoff design's evidence_eligible/requires_source_read
    # contract (§7 of the 2026-09-16 design doc).
    evidence_eligible: bool = True
    # Document.updated_at for confluence_map hits — an approximation of
    # "last confirmed synced", not "last confirmed still live": it only
    # advances when map_sync's crawl actually rewrites this page's
    # frontmatter (title/breadcrumb/version changed), not on every crawl
    # that merely re-touches an unchanged page. None for non-confluence_map
    # hits and for confluence_map hits never re-touched since ingest.
    map_synced_at: Optional[str] = None
```

Now find the metadata-loading query (the `select(Chunk.id, Chunk.document_id, ...)` block joined to `Document`) and add `Document.updated_at`:

```python
        rows = session.execute(
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.text,
                Chunk.header_context,
                Document.title,
                Document.source_type,
                Document.external_id,
                Document.evidence_grade,
                Document.domain,
                Document.environment,
                Document.work_type,
                Document.path_l2,
                Document.source_uri,
                Document.updated_at,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id.in_(all_ids))
        ).all()
        for r in rows:
            meta_by_id[r.id] = {
                "document_id": r.document_id,
                "title": r.title,
                "text": r.text,
                "header_context": r.header_context,
                "source_type": r.source_type,
                "external_id": r.external_id,
                "evidence_grade": r.evidence_grade,
                "domain": r.domain,
                "environment": r.environment,
                "work_type": r.work_type,
                "path_l2": r.path_l2,
                "source_uri": r.source_uri,
                "updated_at": r.updated_at,
            }
```

Finally, in the `SearchHit(...)` construction inside the `for h in gated_list:` loop, add the two new fields:

```python
        results.append(
            SearchHit(
                rank=len(results) + 1,
                score=round(h.score, 6),
                document_id=doc_id,
                chunk_id=h.chunk_id,
                title=str(m.get("title") or ""),
                snippet=_snippet(str(m.get("text") or ""), req.q),
                source_type=str(m.get("source_type") or ""),
                external_id=str(m.get("external_id") or ""),
                evidence_grade=str(m.get("evidence_grade") or ""),
                domain=m.get("domain"),
                environment=m.get("environment"),
                work_type=m.get("work_type"),
                path_l2=m.get("path_l2"),
                source_uri=m.get("source_uri"),
                fts_rank=h.fts_rank,
                vec_rank=h.vec_rank,
                evidence_eligible=str(m.get("source_type") or "") != "confluence_map",
                map_synced_at=(
                    m["updated_at"].isoformat()
                    if m.get("source_type") == "confluence_map" and m.get("updated_at")
                    else None
                ),
            )
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<your-scratch-db-url> python -m pytest tests/test_search_confluence_map_freshness_db.py -v`
Expected: PASS

- [ ] **Step 5: Run the full search test suite to check nothing broke**

Run: `cd apps/api && python -m pytest tests/test_retrieval_search.py -v` (adjust filename if the existing suite uses a different name — check `apps/api/tests/` for the actual `search.py`-covering test file first with `ls apps/api/tests/ | grep -i search`)
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/retrieval/search.py apps/api/tests/test_search_confluence_map_freshness_db.py
git commit -m "$(cat <<'EOF'
feat(search): surface evidence_eligible + map_synced_at on search hits

The handoff design's API contract (§7) calls for every confluence_map
response to carry evidence_eligible=false/requires_source_read=true plus
freshness info — but GET /v1/confluence-map/status only exposes freshness
to admins, and no caller of kb_query/kb_search/kb_ask had any way to tell
a "C"-grade pointer hit apart from verified evidence, or know how stale
its title/breadcrumb might be. Adds evidence_eligible (false only for
confluence_map hits) and map_synced_at (Document.updated_at, i.e. last
time map_sync's crawl actually rewrote this page) to SearchHit — no new
tables or endpoints, just two derived fields on the existing hit shape.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RCGcac9NYvFYa4vd57nHhk
EOF
)"
```

---

## Task 5: Browse-children endpoint

**Files:**
- Modify: `apps/api/app/routers/confluence_map.py`
- Create: `apps/api/tests/test_confluence_map_children_endpoint_db.py`

- [ ] **Step 1: Write the failing DB test**

Create `apps/api/tests/test_confluence_map_children_endpoint_db.py`:

```python
"""DB-touching tests for GET /v1/confluence-map/children. Same opt-in-DB
convention as test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _seed(session, *, external_id, space_key, path, is_folder):
    from app.db.models import Document

    meta = {"space_key": space_key, "경로": path, "유형": "폴더" if is_folder else "문서"}
    payload = f"{path}\n\n{json.dumps(meta, sort_keys=True)}"
    session.add(
        Document(
            id=f"test_children:{external_id}",
            source_type="confluence_map",
            external_id=external_id,
            title=path.rsplit(" > ", 1)[-1],
            body_md=path,
            metadata_=meta,
            content_hash=hashlib.sha256(payload.encode()).hexdigest(),
            evidence_grade="C",
            status="active",
        )
    )


def test_children_returns_only_direct_children_of_space_root():
    from app.db.session import session_scope
    from app.routers.confluence_map import get_children

    with session_scope() as session:
        _seed(session, external_id="1", space_key="TESTSPC", path="GitHub", is_folder=True)
        _seed(session, external_id="2", space_key="TESTSPC", path="GitHub > Q&A", is_folder=False)
        _seed(session, external_id="3", space_key="TESTSPC", path="GitHub > Q&A > deep", is_folder=False)
        _seed(session, external_id="4", space_key="TESTSPC", path="Other Root", is_folder=True)

    result = get_children(space_key="TESTSPC", parent_path="")
    ids = {item["page_id"] for item in result["items"]}
    assert ids == {"1", "4"}


def test_children_returns_only_direct_children_of_given_path():
    from app.db.session import session_scope
    from app.routers.confluence_map import get_children

    with session_scope() as session:
        _seed(session, external_id="11", space_key="TESTSPC2", path="GitHub", is_folder=True)
        _seed(session, external_id="12", space_key="TESTSPC2", path="GitHub > Q&A", is_folder=False)
        _seed(session, external_id="13", space_key="TESTSPC2", path="GitHub > Q&A > deep", is_folder=False)

    result = get_children(space_key="TESTSPC2", parent_path="GitHub")
    ids = {item["page_id"] for item in result["items"]}
    assert ids == {"12"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<your-scratch-db-url> python -m pytest tests/test_confluence_map_children_endpoint_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_children'`

- [ ] **Step 3: Implement the endpoint**

In `apps/api/app/routers/confluence_map.py`, add these imports at the top alongside the existing ones:

```python
from sqlalchemy import select

from app.db.models import Document
```

Then add the endpoint after the `run_inventory` function added in Task 3:

```python
@router.get("/children")
def get_children(space_key: str, parent_path: str = "") -> dict[str, Any]:
    """Direct children of a space (parent_path="") or of a given breadcrumb
    path within it, read from confluence_map documents already ingested
    into the KB search index. Only pages already indexed by confluence_map
    show up here — this is not a general live Confluence browser, and
    (per the 2026-09-21 ACL scope decision — see map_sync.py) carries no
    per-user permission check, matching every other confluence_map/search
    read path today.
    """
    with session_scope() as session:
        rows = session.execute(
            select(Document.external_id, Document.title, Document.source_uri, Document.metadata_)
            .where(
                Document.source_type == "confluence_map",
                Document.status == "active",
                Document.metadata_["space_key"].astext == space_key,
            )
        ).all()

    items: list[dict[str, Any]] = []
    for external_id, title, source_uri, meta in rows:
        path = (meta or {}).get("경로") or ""
        if parent_path:
            prefix = parent_path + " > "
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix):]
        else:
            remainder = path
        if not remainder or " > " in remainder:
            continue  # not present at this level, or a deeper descendant
        items.append(
            {
                "page_id": external_id,
                "title": title,
                "url": source_uri,
                "path": path,
                "is_folder": (meta or {}).get("유형") == "폴더",
            }
        )
    items.sort(key=lambda it: it["title"])
    return {"space_key": space_key, "parent_path": parent_path, "items": items}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<your-scratch-db-url> python -m pytest tests/test_confluence_map_children_endpoint_db.py -v`
Expected: PASS

- [ ] **Step 5: Run the full confluence_map router/test suite**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_sync.py tests/test_confluence_map_crawl.py tests/test_confluence_map_explicit_seeds.py -v`
Expected: All PASS. Also run `python -c "import app.routers.confluence_map"` to confirm the router module still imports without a running DB.

- [ ] **Step 6: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/routers/confluence_map.py apps/api/tests/test_confluence_map_children_endpoint_db.py
git commit -m "$(cat <<'EOF'
feat(confluence-map): add GET /v1/confluence-map/children

Full-text search (kb_query/kb_search) already covers "find a page," but
there was no way to browse a space's or a page's direct children without
already having hit something via search first — the one genuinely-missing
discovery primitive from the handoff design's UI section (§8 lazy tree).
Implemented as a breadcrumb-prefix query over already-ingested
confluence_map documents (Document.metadata_["경로"]) rather than a new
parent_id column — no schema change, and it's exactly the data the
crawler already writes. No admin gate, matching /v1/search's current
no-auth posture per the 2026-09-21 ACL scope decision.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RCGcac9NYvFYa4vd57nHhk
EOF
)"
```

---

## Self-Review Notes (already applied above, kept for the executor's context)

1. **Spec coverage against the gap-analysis report:**
   - Missing SPC/GUID/genaibusiness + 2 personal-workspace pages → Task 2. ✅
   - No mechanism for individually-registered seed pages outside curated roots → Task 1 (this was discovered during planning, not in the original gap report — the original report assumed root-based coverage could just be "expanded," but the personal-workspace pages made that wrong; explicit seeding is the correct primitive). ✅
   - No full-metadata inventory / moves / renames / reappearing docs → Task 3. ✅
   - Freshness signals not surfaced outside admin status → Task 4. ✅
   - No browse/children discovery primitive → Task 5. ✅
   - Per-user ACL gap → explicitly NOT implemented; documented as a decision in Task 2, Step 3's comment block and this plan's header. ✅
   - Handoff's separate `map_pages`/`map_page_search` tables, 4 new MCP tools, generation-based publish/rollback → explicitly NOT built (duplicates working infra / no evidence of need yet per gap analysis items 6-7). Not a task in this plan by design.
2. **Placeholder scan:** no TBD/TODO/"add error handling" phrases in any step; every step has literal code or an exact command.
3. **Type consistency:** `_crawl_explicit_pages` returns `CrawlResult` (same type as `_crawl_map_source`) so Task 1's `result.written.extend(...)` in Task 3 and the `_sync_map_body` wiring in Task 1 Step 5 line up. `SearchHit.evidence_eligible`/`map_synced_at` names match between Task 4's dataclass edit and its `hybrid_search()` construction edit and its test assertions.

**Note for whoever executes Task 3/4/5's DB tests:** this session had no reachable scratch Postgres (`CONFLUENCE_SYNC_TEST_DATABASE_URL` unset, and the live wiki-mcp Confluence connection was also unconfigured here) — those tests are written but unverified by an actual run. Run them for real before considering Tasks 3-5 done, not just Tasks 1-2 (which were verified against the pure-function/httpx-mocked suite that ran cleanly in this environment... verify this at execution time too, since this plan was written without running pytest in this repo yet).
