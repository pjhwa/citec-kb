# Confluence 맵 동기화 공간 관리자 추가/제거 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin add or disable confluence_map sync spaces from admin.html, instead of editing the hardcoded `MAP_SOURCE_DEFS` dict in `apps/api/app/confluence/map_sync.py` and redeploying.

**Architecture:** The `sources` table (`Source` model, `type="confluence_map"`) already stores per-source `config` JSONB (`space_key`/`roots`/`checkpoint`) and a `status` column, but rows are currently created lazily on first sync. This plan promotes that table into the actual registry: every confluence_map source gets a row up front (seeded once via an Alembic data migration for the 12 existing spaces, or created via a new admin API for new ones), a new `get_source_defs()` function reads it, and every runtime code path (`sync_map`, `run_map_inventory`, the CLI, the status router) is switched from the `MAP_SOURCE_DEFS` constant to that function. `status` (`active`/`disabled`) gates which sources the next sync run picks up; disabling never touches already-ingested documents.

**Tech Stack:** FastAPI, SQLAlchemy (Postgres/JSONB), Alembic, vanilla JS (admin.html), pytest.

Design doc: `docs/superpowers/specs/2026-09-22-confluence-map-source-admin-design.md`

---

## Before you start

Read these once, in this order:

1. `apps/api/app/confluence/map_sync.py` — full file. Note `MAP_SOURCE_DEFS` (lines ~111-233), `_ensure_source_row` (~629-641), `sync_map`/`_sync_map_locked`/`_sync_map_body` (~905-1105), `run_map_inventory`/`_run_map_inventory_locked` (~724-903).
2. `apps/api/app/routers/confluence_map.py` — full file (small).
3. `apps/api/app/confluence/map_sync_cli.py` — full file (small).
4. `apps/api/tests/test_confluence_map_sync.py` and `apps/api/tests/test_confluence_map_inventory_db.py` — full files.
5. `apps/api/tests/test_confluence_sync_db.py` docstring (lines 1-25) — the opt-in scratch-DB convention (`CONFLUENCE_SYNC_TEST_DATABASE_URL`) every DB-touching test in this area follows. **Never point it at the live `citec_knowledge` DB.**
6. `apps/api/alembic/versions/20260916_0006_issue_frames_citec_domains.py` — the most recent migration, for revision-id/file-naming convention.
7. `apps/web/public/admin.html` — the "Confluence 맵 동기화" card (search for `mapSyncBody`), roughly lines 126-145 (HTML) and 308-375 (JS).

Run the existing tests once before touching anything, to get a known-good baseline:

```bash
cd apps/api && python -m pytest tests/test_confluence_map_sync.py tests/test_confluence_map_explicit_seeds.py tests/test_confluence_map_crawl.py -q
```

Expected: all pass (these don't need a DB).

---

### Task 1: Seed-data module + Alembic migration for the 12 existing spaces

**Files:**
- Create: `apps/api/app/confluence/map_source_seed.py`
- Create: `apps/api/alembic/versions/20260922_0007_confluence_map_sources_seed.py`
- Test: `apps/api/tests/test_confluence_map_source_seed.py`

This task moves the literal 12-space definitions out of `map_sync.py` into a small standalone module used **only** by the migration and by this test — no runtime code will import it after Task 2.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_confluence_map_source_seed.py`:

```python
"""Pure-data tests for the confluence_map source seed used by the
20260922_0007 Alembic migration. No DB, no I/O — mirrors the old
MAP_SOURCE_DEFS shape tests that lived in test_confluence_map_sync.py
before that constant moved out of the runtime path (see
docs/superpowers/specs/2026-09-22-confluence-map-source-admin-design.md).
"""

from __future__ import annotations

from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS


def test_seed_covers_all_twelve_approved_spaces():
    assert set(SEED_MAP_SOURCE_DEFS.keys()) == {
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


def test_seed_each_have_own_space_key_and_roots_or_explicit_pages():
    space_keys = set()
    for source_id, sd in SEED_MAP_SOURCE_DEFS.items():
        assert sd["roots"] or sd.get("explicit_pages"), (
            f"{source_id} has neither roots nor explicit_pages"
        )
        space_keys.add(sd["space_key"])
    assert len(space_keys) == len(SEED_MAP_SOURCE_DEFS)


def test_seed_explicit_pages_scoped_to_the_six_github_question_pages():
    assert SEED_MAP_SOURCE_DEFS["confluence_map_spc"]["explicit_pages"].keys() == {
        "155680474", "383755011",
    }
    assert SEED_MAP_SOURCE_DEFS["confluence_map_guid"]["explicit_pages"].keys() == {
        "1488175558",
    }
    assert SEED_MAP_SOURCE_DEFS["confluence_map_genaibusiness"]["explicit_pages"].keys() == {
        "1268082455",
    }
    assert "1475349722" in SEED_MAP_SOURCE_DEFS["confluence_map_devops001"]["explicit_pages"]
    assert "2254661271" in SEED_MAP_SOURCE_DEFS["confluence_map_openstack101"]["explicit_pages"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_source_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.confluence.map_source_seed'`

- [ ] **Step 3: Create the seed module**

Create `apps/api/app/confluence/map_source_seed.py` by copying the **entire** `MAP_SOURCE_DEFS` dict literal (all 12 entries, verbatim, including every comment) from `apps/api/app/confluence/map_sync.py` lines 111-233, renamed to `SEED_MAP_SOURCE_DEFS`:

```python
"""One-time seed data for the confluence_map source registry.

Used only by alembic/versions/20260922_0007_confluence_map_sources_seed.py
(to upsert the 12 originally-hardcoded spaces into the `sources` table) and
by test_confluence_map_source_seed.py. Nothing at runtime imports this —
see app.confluence.map_sync.get_source_defs(), which reads the registry
from the DB. Do not add new spaces here; use POST /v1/confluence-map/sources
instead (see apps/api/app/routers/confluence_map.py).
"""

from __future__ import annotations

from typing import Any

# Curated roots per space — narrow subtrees only (incident/tech-support
# relevant), same "don't ingest whole space" policy as sync.py's
# CONFLUENCE_DOCS_ROOTS/TECHREPO_ROOTS. Whole-space crawl would also pull
# other teams' 손익 관리/팀 KPI/해외법인 업무/Personal Space/999. FreeSpace —
# not CI-TEC's business and pure noise for incident lookup.
#
# Approved 2026-09-16 (박재화) after sampling each space's actual content via
# confluence-mcp getChild (see conversation) — content shape varies a lot:
# CLDENG's KDB root alone has 100s of pages titled as literal symptom/error
# strings (e.g. "[HW] CPU Uncorrectable Machine Check Exception"), so a
# title-only index is already high-value there; others (Openstack101,
# EMCloud) are much thinner.
#
# sysops (MSP인프라운영팀) intentionally excluded for now: its home subtree
# only has 2 children (과제 진행 현황/손익 관리), no issue/KDB-shaped content
# as of this date. Add a roots entry here once that space grows one.
#
# 2026-09-16 (박재화, 2차 결정): LOOKIN/TechRepo/ServiceExcellenceTeam/
# ICLOUDUT는 "나머지 부분만 1회성 마이그레이션"이 아니라 **전체 공간을
# confluence_map으로 상시 포함**하고 매일 증분 갱신한다 — root은 해당 공간의
# 홈/최상위 페이지 하나(= 전체가 그 밑에 있으므로 사실상 "전체 공간" 크롤).
# LOOKIN/TechRepo는 이미 confluence_docs/tech_repo(4+5개 서브트리)로 본문
#전체가 들어간 페이지도 있는데, 그 페이지들도 이 맵에 C등급 포인터로 중복
# 등록된다 — 검색 랭킹엔 무해(A등급이 항상 우선)하고, 매번 실시간이라 예전
# "1회성 마이그레이션 스냅샷이 stale해지는" 문제 자체가 없어진다(예:
# 스냅샷과 실제 사이 326건 격차 같은 것). 홈페이지 자기 자신은 `ancestor=`
# CQL 특성상 결과에 안 잡힌다(자손만 반환) — confluence_docs/tech_repo도
# 동일한 특성이라 새로운 제약이 아니다.
SEED_MAP_SOURCE_DEFS: dict[str, dict[str, Any]] = {
    "confluence_map_lookin": {
        "space_key": "LOOKIN",
        "space_name": "CI-TEC",
        "roots": {
            "222532724": "전체 공간 (CI-TEC Home)",
        },
    },
    "confluence_map_techrepo": {
        "space_key": "TechRepo",
        "space_name": "[클라우드] 테크리포(Tech-Repository)",
        "roots": {
            "31951116": "전체 공간 (테크리포 Home)",
        },
    },
    "confluence_map_serviceexcellenceteam": {
        "space_key": "ServiceExcellenceTeam",
        "space_name": "서비스일류화팀",
        "roots": {
            "2001257717": "전체 공간 (서비스일류화팀 Home)",
        },
    },
    "confluence_map_icloudut": {
        "space_key": "ICLOUDUT",
        "space_name": "Cloud Umbrella Team",
        "roots": {
            "230553963": "전체 공간 (SCP Umbrella Team Home)",
        },
    },
    "confluence_map_devops001": {
        "space_key": "DevOps001",
        "space_name": "SCP인프라운영팀",
        "roots": {
            "601879661": "005. 이슈/문제/KDB/SOP",
            "468085983": "006. SCP CASE study",
            "370644108": "★★ SCP (SCP SRE + SCP NW Share) ★★",
        },
        "explicit_pages": {
            "1475349722": "GitHub 아이피 변경 적용 (개인 업무공간 seed: 이정수 > R+ 거점서버 인수인계)",
        },
    },
    "confluence_map_openstack101": {
        "space_key": "Openstack101",
        "space_name": "OPENSTACK PLATFORM",
        "roots": {
            "1148203906": "knowledge base",
            "2318695720": "9. 팀 ISSUE 관리",
            "1176274225": "Nuri 운영구성",
            "1204002143": "Nuri 운영 관련",
        },
        "explicit_pages": {
            "2254661271": "Squid Proxy 전환 (개인 업무공간 seed: 정지원 > 2026~Techops)",
        },
    },
    "confluence_map_cldeng": {
        "space_key": "CLDENG",
        "space_name": "MSP인프라기술그룹",
        "roots": {
            "289411381": "KB/SOP 검색",
            "271492877": "문제 해결 문서 (KDB)",
            "271779122": "004. 이슈,장애 관리",
            "271779763": "006. 기술&자동화",
            "271786535": "007. HW 운영 (서버HW/가상화/스토리지)",
            "331893132": "009. 통합백업",
            "561906785": "119. (★)DR 전환 및 비상가동 절차(★)",
        },
    },
    "confluence_map_dftrts": {
        "space_key": "DFTRTS",
        "space_name": "기술검증그룹",
        "roots": {
            "145822951": "기술자료",
            "966044388": "인프라설계검증",
            "958711159": "하드웨어분석",
        },
    },
    "confluence_map_emcloud": {
        "space_key": "EMCloud",
        "space_name": "통합Managed Infra서비스팀",
        "roots": {
            "271034968": "문제 해결 문서",
            "184265267": "7. Cloud Engineering(Shared Service)",
        },
    },
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
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_source_seed.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the Alembic migration**

Create `apps/api/alembic/versions/20260922_0007_confluence_map_sources_seed.py`:

```python
"""seed confluence_map source registry (12 originally-hardcoded spaces)

Revision ID: 20260922_0007
Revises: 20260916_0006

Promotes the `sources` table (type="confluence_map") into the live
registry app.confluence.map_sync.get_source_defs() reads from, instead of
rows being lazily created by the first sync_map() run for each source_id.
Idempotent: ON CONFLICT (id) DO NOTHING, so re-running this (or running it
against a DB where some of these sources already synced and have
last_sync_at/checkpoint set) never clobbers existing progress.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "20260922_0007"
down_revision: Union[str, None] = "20260916_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_sources = sa.table(
    "sources",
    sa.column("id", sa.String),
    sa.column("type", sa.String),
    sa.column("name", sa.String),
    sa.column("config", postgresql.JSONB),
    sa.column("status", sa.String),
)


def upgrade() -> None:
    from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS

    bind = op.get_bind()
    for source_id, sd in SEED_MAP_SOURCE_DEFS.items():
        stmt = pg_insert(_sources).values(
            id=source_id,
            type="confluence_map",
            name=f"Confluence Map {sd['space_key']}",
            config={
                "space_key": sd["space_key"],
                "space_name": sd["space_name"],
                "roots": sd.get("roots") or {},
                "explicit_pages": sd.get("explicit_pages") or {},
            },
            status="active",
        ).on_conflict_do_nothing(index_elements=["id"])
        bind.execute(stmt)


def downgrade() -> None:
    from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS

    bind = op.get_bind()
    # Only remove rows that still look exactly as seeded (never synced) —
    # a source that has since run for real (last_sync_at set) keeps its row
    # rather than losing sync history/checkpoint on downgrade.
    bind.execute(
        sa.text(
            "DELETE FROM sources WHERE type = 'confluence_map' "
            "AND id = ANY(:ids) AND last_sync_at IS NULL"
        ),
        {"ids": list(SEED_MAP_SOURCE_DEFS.keys())},
    )
```

- [ ] **Step 6: Verify the migration imports cleanly and chains correctly**

Run: `cd apps/api && python -c "import importlib.util; spec = importlib.util.spec_from_file_location('m', 'alembic/versions/20260922_0007_confluence_map_sources_seed.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print(mod.revision, mod.down_revision)"`
Expected: `20260922_0007 20260916_0006`

If you have a local Postgres reachable (the `CONFLUENCE_SYNC_TEST_DATABASE_URL` scratch DB from the "Before you start" section, or any throwaway DB with the schema already at `20260916_0006`), also run:

```bash
cd apps/api && DATABASE_URL=<that DSN> alembic upgrade head
```

Expected: no errors, ends at `20260922_0007`. Then confirm the migration is idempotent — run it again:

```bash
cd apps/api && DATABASE_URL=<that DSN> alembic downgrade 20260916_0006 && DATABASE_URL=<that DSN> alembic upgrade head
```

Expected: no errors on either command, and (query the DB directly) exactly 12 rows with `type='confluence_map'` afterward — the downgrade's `last_sync_at IS NULL` guard removes only the just-seeded rows (none of them have synced yet in a fresh scratch DB), and the re-upgrade's `ON CONFLICT DO NOTHING` re-inserts them cleanly.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/confluence/map_source_seed.py apps/api/alembic/versions/20260922_0007_confluence_map_sources_seed.py apps/api/tests/test_confluence_map_source_seed.py
git commit -m "feat(confluence-map): seed source registry from hardcoded 12-space defs"
```

---

### Task 2: `get_source_defs()` — switch `map_sync.py` to the DB registry

**Files:**
- Modify: `apps/api/app/confluence/map_sync.py`
- Test: `apps/api/tests/test_confluence_map_source_registry_db.py` (new)
- Modify: `apps/api/tests/test_confluence_map_inventory_db.py`
- Modify: `apps/api/tests/test_confluence_map_sync.py`

This task removes `MAP_SOURCE_DEFS` and `_ensure_source_row` from `map_sync.py` and adds `get_source_defs()`, reading from the `sources` table. All DB-touching tests in this task follow the same opt-in `CONFLUENCE_SYNC_TEST_DATABASE_URL` convention as `test_confluence_map_inventory_db.py` (see "Before you start" step 5) — **never** point it at the live `citec_knowledge` DB.

- [ ] **Step 1: Write the failing test for `get_source_defs()`**

Create `apps/api/tests/test_confluence_map_source_registry_db.py`:

```python
"""DB-touching tests for app.confluence.map_sync.get_source_defs(). Same
opt-in scratch-DB convention as test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

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


def _add_source(session, *, source_id, space_key, status="active", roots=None, explicit_pages=None):
    from app.db.models import Source

    session.add(
        Source(
            id=source_id,
            type="confluence_map",
            name=f"Confluence Map {space_key}",
            config={
                "space_key": space_key,
                "space_name": f"{space_key} name",
                "roots": roots or {"1": "root one"},
                "explicit_pages": explicit_pages or {},
            },
            status=status,
        )
    )


def test_get_source_defs_returns_all_confluence_map_sources():
    from app.confluence.map_sync import get_source_defs
    from app.db.session import session_scope

    with session_scope() as session:
        _add_source(session, source_id="confluence_map_test_a", space_key="TESTA")
        _add_source(session, source_id="confluence_map_test_b", space_key="TESTB", status="disabled")
        # A different type must never leak in.
        from app.db.models import Source
        session.add(Source(id="fs_raw", type="fs_raw", name="fs_raw", config={}, status="active"))

    defs = get_source_defs()
    assert "confluence_map_test_a" in defs
    assert "confluence_map_test_b" in defs
    assert "fs_raw" not in defs
    assert defs["confluence_map_test_a"]["space_key"] == "TESTA"
    assert defs["confluence_map_test_a"]["roots"] == {"1": "root one"}
    assert defs["confluence_map_test_a"]["explicit_pages"] == {}


def test_get_source_defs_active_only_excludes_disabled():
    from app.confluence.map_sync import get_source_defs
    from app.db.session import session_scope

    with session_scope() as session:
        _add_source(session, source_id="confluence_map_test_c", space_key="TESTC", status="active")
        _add_source(session, source_id="confluence_map_test_d", space_key="TESTD", status="disabled")

    defs = get_source_defs(active_only=True)
    assert "confluence_map_test_c" in defs
    assert "confluence_map_test_d" not in defs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch DSN> python -m pytest tests/test_confluence_map_source_registry_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_source_defs'` (or skipped with the reason above if you have no scratch DB available; in that case, proceed on code reading alone and rely on CI/a reviewer with DB access to run it before merge)

- [ ] **Step 3: Add `get_source_defs()` to `map_sync.py`**

In `apps/api/app/confluence/map_sync.py`, replace the entire `MAP_SOURCE_DEFS` block (the whole comment block + dict literal, lines ~69-233) with:

```python
def get_source_defs(active_only: bool = False) -> dict[str, dict[str, Any]]:
    """The confluence_map source registry, read from the `sources` table
    (type="confluence_map") — replaces the old hardcoded MAP_SOURCE_DEFS
    constant (see apps/api/app/confluence/map_source_seed.py, which now
    only feeds the one-time Alembic seed migration). Rows are created by
    POST /v1/confluence-map/sources (see app.routers.confluence_map) or by
    that migration — sync_map()/run_map_inventory() never create them.

    Returns {source_id: {"space_key", "space_name", "roots",
    "explicit_pages"}}, same shape callers used against MAP_SOURCE_DEFS.
    active_only=True excludes status="disabled" sources — used by
    sync_map()'s default source list so a disabled space stops being
    picked up by the next run without deleting its row or documents.
    """
    with session_scope() as session:
        rows = session.query(Source).filter(Source.type == "confluence_map").all()
        if active_only:
            rows = [r for r in rows if r.status == "active"]
        return {
            r.id: {
                "space_key": (r.config or {}).get("space_key"),
                "space_name": (r.config or {}).get("space_name"),
                "roots": (r.config or {}).get("roots") or {},
                "explicit_pages": (r.config or {}).get("explicit_pages") or {},
            }
            for r in rows
        }
```

Then update every remaining reference in the same file:

1. Remove `_ensure_source_row` entirely (lines ~629-641) — every confluence_map source row now pre-exists (seeded by the migration or created via `POST /sources`), so `sync_map()` never needs to lazily create one.
2. In `_sync_map_locked` (~line 959), change:
   ```python
   defs = source_ids or list(MAP_SOURCE_DEFS.keys())
   ```
   to:
   ```python
   active_defs = get_source_defs(active_only=True)
   defs = source_ids or list(active_defs.keys())
   ```
   and thread `active_defs` down into `_sync_map_body` as a new keyword argument `all_defs: dict[str, dict[str, Any]]` (add it to `_sync_map_body`'s signature and to the call in `_sync_map_locked`).
3. In `_sync_map_body` (~line 1005-1010), change:
   ```python
   if source_id not in MAP_SOURCE_DEFS:
       logger.error("unknown confluence_map source_id=%s (valid: %s)", source_id, list(MAP_SOURCE_DEFS))
       stats["sources"][source_id] = {"skipped": True, "reason": "unknown source_id"}
       continue
   sd = MAP_SOURCE_DEFS[source_id]
   ```
   to:
   ```python
   if source_id not in all_defs:
       logger.error("unknown or disabled confluence_map source_id=%s (valid: %s)", source_id, list(all_defs))
       stats["sources"][source_id] = {"skipped": True, "reason": "unknown or disabled source_id"}
       continue
   sd = all_defs[source_id]
   ```
4. In the same function (~line 1018), delete the `_ensure_source_row(source_id, sd["space_key"], sd["roots"])` call — the row already exists by construction.
5. In `run_map_inventory` (~line 782-784), change:
   ```python
   if source_id not in MAP_SOURCE_DEFS:
       raise ValueError(f"unknown confluence_map source_id={source_id!r}")
   sd = MAP_SOURCE_DEFS[source_id]
   ```
   to:
   ```python
   defs = get_source_defs()
   if source_id not in defs:
       raise ValueError(f"unknown confluence_map source_id={source_id!r}")
   sd = defs[source_id]
   ```
   (deliberately **not** `active_only=True` — a disabled source can still be reconciled/inventoried; disabling only pauses the daily incremental sync.)
6. Update the two docstring comments that reference `MAP_SOURCE_DEFS` (~line 752, ~line 755) to reference `get_source_defs()` instead, keeping the rest of the explanation intact.

- [ ] **Step 4: Run the new registry test to verify it passes**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch DSN> python -m pytest tests/test_confluence_map_source_registry_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Update `test_confluence_map_inventory_db.py`**

In `apps/api/tests/test_confluence_map_inventory_db.py`, in all three test functions, change:
```python
from app.confluence.map_sync import MAP_SOURCE_DEFS, run_map_inventory
```
to:
```python
from app.confluence.map_sync import get_source_defs, run_map_inventory
```
and change every:
```python
space_key = MAP_SOURCE_DEFS[source_id]["space_key"]
```
to:
```python
space_key = get_source_defs()[source_id]["space_key"]
```
(same for the one `roots = list(MAP_SOURCE_DEFS[source_id]["roots"])` line in `test_run_map_inventory_skips_archive_when_crawl_had_errors` — change to `roots = list(get_source_defs()[source_id]["roots"])`).

These tests rely on `source_id = "confluence_map_devops001"` already existing in the DB — it does, because the scratch DB has `alembic upgrade head` applied (per its own docstring), which now includes Task 1's seed migration.

- [ ] **Step 6: Run inventory tests to verify they still pass**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch DSN> python -m pytest tests/test_confluence_map_inventory_db.py -v`
Expected: PASS (3 tests) — if your scratch DB was created before Task 1's migration existed, run `alembic upgrade head` against it first.

- [ ] **Step 7: Update `test_confluence_map_sync.py`**

In `apps/api/tests/test_confluence_map_sync.py`, delete the three tests that assert on `MAP_SOURCE_DEFS`'s full shape (`test_map_source_defs_cover_all_twelve_approved_spaces`, `test_map_source_defs_each_have_own_space_key_and_roots_or_explicit_pages`, `test_map_source_defs_explicit_pages_scoped_to_the_six_github_question_pages`) — they're superseded by `test_confluence_map_source_seed.py` (Task 1) and `test_confluence_map_source_registry_db.py` (this task). Update the import line:
```python
from app.confluence.map_sync import (
    MAP_SOURCE_DEFS,
    _is_folder_title,
    build_frontmatter_confluence_map,
)
```
to:
```python
from app.confluence.map_sync import (
    _is_folder_title,
    build_frontmatter_confluence_map,
)
```
The remaining tests (`test_is_folder_title_*`, `test_frontmatter_*`) are untouched and stay DB-free.

- [ ] **Step 8: Run the full confluence_map test suite (DB-free subset)**

Run: `cd apps/api && python -m pytest tests/test_confluence_map_sync.py tests/test_confluence_map_source_seed.py tests/test_confluence_map_explicit_seeds.py tests/test_confluence_map_crawl.py -q`
Expected: all pass, no import errors.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/confluence/map_sync.py apps/api/tests/test_confluence_map_source_registry_db.py apps/api/tests/test_confluence_map_inventory_db.py apps/api/tests/test_confluence_map_sync.py
git commit -m "feat(confluence-map): read source registry from DB via get_source_defs()"
```

---

### Task 3: `map_sync_cli.py` — use the DB registry for the default source list

**Files:**
- Modify: `apps/api/app/confluence/map_sync_cli.py`

- [ ] **Step 1: Update the CLI**

In `apps/api/app/confluence/map_sync_cli.py`, change:
```python
def main(argv: list[str] | None = None) -> int:
    from app.confluence.map_sync import MAP_SOURCE_DEFS

    parser = argparse.ArgumentParser(
        description="Confluence 맵(구조 전용) 증분 동기화 — 5개 신규 공간"
    )
    parser.add_argument(
        "--raw-dir",
        default=os.getenv("RAW_DIR", "/data/raw"),
        help="Path to raw corpus root",
    )
    parser.add_argument(
        "--source-ids",
        default=",".join(MAP_SOURCE_DEFS.keys()),
        help=f"Comma list of source_id, e.g. {next(iter(MAP_SOURCE_DEFS))}",
    )
```
to:
```python
def main(argv: list[str] | None = None) -> int:
    from app.confluence.map_sync import get_source_defs

    active_defs = get_source_defs(active_only=True)

    parser = argparse.ArgumentParser(
        description="Confluence 맵(구조 전용) 증분 동기화 — 활성 소스 전체"
    )
    parser.add_argument(
        "--raw-dir",
        default=os.getenv("RAW_DIR", "/data/raw"),
        help="Path to raw corpus root",
    )
    parser.add_argument(
        "--source-ids",
        default=",".join(active_defs.keys()),
        help=f"Comma list of source_id, e.g. {next(iter(active_defs), '')}",
    )
```

(`next(iter(active_defs), '')` avoids a `StopIteration`-adjacent crash if the registry is ever empty when `--help` is run.)

- [ ] **Step 2: Sanity-check the CLI still parses `--help` without a DB**

This will only fully succeed with a reachable `DATABASE_URL` (the CLI now needs the DB to build its own `--source-ids` default), which matches how it's actually invoked in practice (`docker compose exec`, per its docstring). Confirm by reading, not running: `get_source_defs()` opens a `session_scope()` — same DB dependency `sync_map()` already has a few lines below in the same function. No new failure mode is introduced.

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/confluence/map_sync_cli.py
git commit -m "feat(confluence-map): CLI reads default source list from DB registry"
```

---

### Task 4: Admin API — extend status, add create/toggle endpoints

**Files:**
- Modify: `apps/api/app/routers/confluence_map.py`
- Test: `apps/api/tests/test_confluence_map_source_admin_endpoints_db.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_confluence_map_source_admin_endpoints_db.py`:

```python
"""DB-touching tests for the confluence_map source admin endpoints
(POST /v1/confluence-map/sources, PATCH .../sources/{id}) and the
space_name/status fields added to GET /v1/confluence-map/status. Same
opt-in scratch-DB convention as test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

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


def _admin():
    from app.auth.principal import Principal

    return Principal(sub="test-admin", name="test-admin", roles=frozenset({"admin"}))


def test_create_source_root_and_duplicate_conflicts():
    from fastapi import HTTPException
    from app.routers.confluence_map import CreateSourceBody, create_source, get_status

    body = CreateSourceBody(
        space_key="NEWSPC",
        space_name="새 팀 공간",
        page_id="900001",
        label="전체 공간 (New Space Home)",
        is_explicit_page=False,
    )
    result = create_source(body, principal=_admin())
    assert result["source_id"] == "confluence_map_newspc"

    status = get_status(principal=_admin())
    src = status["sources"]["confluence_map_newspc"]
    assert src["space_key"] == "NEWSPC"
    assert src["status"] == "active"

    with pytest.raises(HTTPException) as exc:
        create_source(body, principal=_admin())
    assert exc.value.status_code == 409


def test_create_source_explicit_page():
    from app.routers.confluence_map import CreateSourceBody, create_source
    from app.db.session import session_scope
    from app.db.models import Source

    body = CreateSourceBody(
        space_key="EXPSPC",
        space_name="explicit page space",
        page_id="900002",
        label="단일 페이지",
        is_explicit_page=True,
    )
    create_source(body, principal=_admin())

    with session_scope() as session:
        src = session.get(Source, "confluence_map_expspc")
        assert src.config["roots"] == {}
        assert src.config["explicit_pages"] == {"900002": "단일 페이지"}


def test_toggle_source_status_roundtrip():
    from fastapi import HTTPException
    from app.routers.confluence_map import (
        CreateSourceBody,
        UpdateSourceStatusBody,
        create_source,
        update_source_status,
    )

    create_source(
        CreateSourceBody(
            space_key="TOGSPC",
            space_name="toggle space",
            page_id="900003",
            label="root",
            is_explicit_page=False,
        ),
        principal=_admin(),
    )

    result = update_source_status(
        "confluence_map_togspc", UpdateSourceStatusBody(status="disabled"), principal=_admin()
    )
    assert result["status"] == "disabled"

    from app.confluence.map_sync import get_source_defs

    assert "confluence_map_togspc" not in get_source_defs(active_only=True)
    assert "confluence_map_togspc" in get_source_defs(active_only=False)

    result = update_source_status(
        "confluence_map_togspc", UpdateSourceStatusBody(status="active"), principal=_admin()
    )
    assert result["status"] == "active"

    with pytest.raises(HTTPException) as exc:
        update_source_status(
            "confluence_map_does_not_exist", UpdateSourceStatusBody(status="active"), principal=_admin()
        )
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch DSN> python -m pytest tests/test_confluence_map_source_admin_endpoints_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'CreateSourceBody'`

- [ ] **Step 3: Implement the endpoints**

In `apps/api/app/routers/confluence_map.py`:

Replace the import line:
```python
from app.confluence.map_sync import MAP_SOURCE_DEFS
```
with:
```python
import re
from typing import Literal
```
(add these near the existing `from typing import Any, Optional` import — merge into one `typing` import line — and drop the now-unused `MAP_SOURCE_DEFS` import entirely, since `get_status` below no longer needs it).

In `get_status` (the `GET /status` handler), replace:
```python
with session_scope() as session:
    rows = session.query(Source).filter(Source.type == "confluence_map").all()
    by_id = {
        r.id: {
            "space_key": (r.config or {}).get("space_key"),
            "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
            "checkpoint": (r.config or {}).get("checkpoint") or {},
        }
        for r in rows
    }
# Sources never run yet have no row — report them too so the admin page
# always shows every configured source, not just whichever have already
# synced once.
for source_id, sd in MAP_SOURCE_DEFS.items():
    by_id.setdefault(
        source_id,
        {"space_key": sd["space_key"], "last_sync_at": None, "checkpoint": {}},
    )
```
with:
```python
with session_scope() as session:
    rows = session.query(Source).filter(Source.type == "confluence_map").all()
    by_id = {
        r.id: {
            "space_key": (r.config or {}).get("space_key"),
            "space_name": (r.config or {}).get("space_name"),
            "status": r.status,
            "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
            "checkpoint": (r.config or {}).get("checkpoint") or {},
        }
        for r in rows
    }
    # Every confluence_map source now has a row up front (seeded by the
    # 20260922_0007 migration, or created via POST /sources below) — no
    # more "never run yet, report it anyway from the hardcoded defs"
    # fallback needed.
```

Then add, at the end of the file (after `get_status`):

```python
def _slugify_space_key(space_key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", space_key.lower())


class CreateSourceBody(BaseModel):
    space_key: str
    space_name: str
    page_id: str
    label: str
    is_explicit_page: bool = False


@router.post("/sources")
def create_source(
    body: CreateSourceBody,
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    _ = principal
    source_id = f"confluence_map_{_slugify_space_key(body.space_key)}"
    config: dict[str, Any] = {
        "space_key": body.space_key,
        "space_name": body.space_name,
        "roots": {} if body.is_explicit_page else {body.page_id: body.label},
        "explicit_pages": {body.page_id: body.label} if body.is_explicit_page else {},
    }
    with session_scope() as session:
        if session.get(Source, source_id):
            raise HTTPException(status_code=409, detail=f"source {source_id} already exists")
        session.add(
            Source(
                id=source_id,
                type="confluence_map",
                name=f"Confluence Map {body.space_key}",
                config=config,
                status="active",
            )
        )
    return {"source_id": source_id, "space_key": body.space_key, "status": "active"}


class UpdateSourceStatusBody(BaseModel):
    status: Literal["active", "disabled"]


@router.patch("/sources/{source_id}")
def update_source_status(
    source_id: str,
    body: UpdateSourceStatusBody,
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    _ = principal
    with session_scope() as session:
        src = session.get(Source, source_id)
        if not src or src.type != "confluence_map":
            raise HTTPException(status_code=404, detail=f"source {source_id} not found")
        src.status = body.status
    return {"source_id": source_id, "status": body.status}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch DSN> python -m pytest tests/test_confluence_map_source_admin_endpoints_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full existing confluence_map DB test files to check for regressions**

Run: `cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch DSN> python -m pytest tests/test_confluence_map_inventory_db.py tests/test_confluence_map_children_endpoint_db.py tests/test_confluence_map_source_registry_db.py tests/test_confluence_map_source_admin_endpoints_db.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/confluence_map.py apps/api/tests/test_confluence_map_source_admin_endpoints_db.py
git commit -m "feat(confluence-map): add admin endpoints to add/disable sync sources"
```

---

### Task 5: admin.html — add/toggle UI

**Files:**
- Modify: `apps/web/public/admin.html`

No automated test — this is verified manually in a browser per the steps below (admin.html has no JS test harness in this repo).

- [ ] **Step 1: Extend the sync-status table with space_name/status/toggle columns**

In `apps/web/public/admin.html`, replace the "Confluence 맵 동기화" card's `<table>` head:
```html
<thead><tr><th>source_id</th><th>space</th><th>last_sync_at</th><th>진행 상태 (checkpoint)</th></tr></thead>
```
with:
```html
<thead><tr><th>source_id</th><th>space</th><th>상태</th><th>last_sync_at</th><th>진행 상태 (checkpoint)</th><th></th></tr></thead>
```

Replace the static explanatory paragraph:
```html
<p class="meta" style="margin:4px 0 8px">
  LOOKIN/TechRepo/ServiceExcellenceTeam/ICLOUDUT/DevOps001/Openstack101/CLDENG/DFTRTS/EMCloud
  9개 공간 순차 동기화. 실행 중 중단돼도 다음 실행이 체크포인트에서 이어받음 — 동시에 두 번 실행되지 않도록
  Postgres advisory lock으로 보호됨(크론 CLI 실행과도 공유).
</p>
```
with:
```html
<p class="meta" style="margin:4px 0 8px" id="mapSourceCount">
  활성 공간 순차 동기화. 실행 중 중단돼도 다음 실행이 체크포인트에서 이어받음 — 동시에 두 번 실행되지 않도록
  Postgres advisory lock으로 보호됨(크론 CLI 실행과도 공유).
</p>
```

Add an inline "새 공간 추가" form right before the closing `</div>` of the same card (after the existing `<span id="mapMsg" ...></span>` line):
```html
    <span id="mapMsg" class="meta"></span>
    <hr style="border:none;border-top:1px solid var(--border);margin:10px 0"/>
    <strong style="font-size:13px">새 공간 추가</strong>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;align-items:center">
      <input type="text" id="newSpaceKey" placeholder="space_key (예: NEWSPACE)" style="flex:1;min-width:140px"/>
      <input type="text" id="newSpaceName" placeholder="space_name (예: 새 팀 공간)" style="flex:1;min-width:140px"/>
      <input type="text" id="newPageId" placeholder="root/page ID" style="flex:1;min-width:120px"/>
      <input type="text" id="newPageLabel" placeholder="label" style="flex:1;min-width:140px"/>
      <label class="toggle"><input type="checkbox" id="newIsExplicit"/> explicit page</label>
      <button type="button" id="btnAddSpace">추가</button>
    </div>
  </div>
```

- [ ] **Step 2: Update `fmtCheckpoint` call site and add row-render columns**

In `refreshMapSync()`, replace:
```javascript
    const sources = Object.keys(d.sources || {}).sort();
    $("mapSyncBody").innerHTML = sources.length
      ? sources.map((id) => {
          const s = d.sources[id];
          return '<tr><td>' + esc(id) + '</td><td>' + esc(s.space_key || "—") +
            '</td><td>' + fmtTs(s.last_sync_at) +
            '</td><td>' + fmtCheckpoint(s.checkpoint) + '</td></tr>';
        }).join("")
      : '<tr><td colspan="4" class="meta">데이터 없음</td></tr>';
```
with:
```javascript
    const sources = Object.keys(d.sources || {}).sort();
    const activeCount = sources.filter((id) => d.sources[id].status !== "disabled").length;
    $("mapSourceCount").textContent =
      activeCount + "개 활성 공간(전체 " + sources.length + "개) 순차 동기화. 실행 중 중단돼도 다음 실행이 " +
      "체크포인트에서 이어받음 — 동시에 두 번 실행되지 않도록 Postgres advisory lock으로 보호됨(크론 CLI 실행과도 공유).";
    $("mapSyncBody").innerHTML = sources.length
      ? sources.map((id) => {
          const s = d.sources[id];
          const disabled = s.status === "disabled";
          const statusBadge = '<span class="badge ' + (disabled ? "na" : "ok") + '">' +
            (disabled ? "비활성" : "활성") + '</span>';
          const toggleLabel = disabled ? "활성화" : "비활성화";
          return '<tr><td>' + esc(id) + '</td><td>' + esc(s.space_name || s.space_key || "—") +
            '</td><td>' + statusBadge +
            '</td><td>' + fmtTs(s.last_sync_at) +
            '</td><td>' + fmtCheckpoint(s.checkpoint) +
            '</td><td><button type="button" class="ghost" data-toggle-source="' + esc(id) +
            '" data-next-status="' + (disabled ? "active" : "disabled") + '">' + toggleLabel +
            '</button></td></tr>';
        }).join("")
      : '<tr><td colspan="6" class="meta">데이터 없음</td></tr>';
```

- [ ] **Step 3: Wire up the toggle buttons and the add-space form**

After the existing `$("btnMapRun").onclick = ...` block, add:

```javascript
$("mapSyncBody").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-toggle-source]");
  if (!btn) return;
  const sourceId = btn.getAttribute("data-toggle-source");
  const nextStatus = btn.getAttribute("data-next-status");
  btn.disabled = true;
  try {
    const r = await CitecAuth.apiFetch("/v1/confluence-map/sources/" + encodeURIComponent(sourceId), {
      method: "PATCH",
      body: { status: nextStatus },
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      $("mapMsg").textContent = "상태 변경 실패: " + (d.detail || r.status);
      btn.disabled = false;
      return;
    }
    await refreshMapSync();
  } catch (e) {
    $("mapMsg").textContent = String(e);
    btn.disabled = false;
  }
});

$("btnAddSpace").onclick = async () => {
  const spaceKey = $("newSpaceKey").value.trim();
  const spaceName = $("newSpaceName").value.trim();
  const pageId = $("newPageId").value.trim();
  const pageLabel = $("newPageLabel").value.trim();
  const isExplicit = $("newIsExplicit").checked;
  if (!spaceKey || !spaceName || !pageId || !pageLabel) {
    $("mapMsg").textContent = "space_key/space_name/page ID/label을 모두 입력하세요.";
    return;
  }
  $("mapMsg").textContent = "추가 중…";
  try {
    const r = await CitecAuth.apiFetch("/v1/confluence-map/sources", {
      method: "POST",
      body: {
        space_key: spaceKey,
        space_name: spaceName,
        page_id: pageId,
        label: pageLabel,
        is_explicit_page: isExplicit,
      },
    });
    const d = await r.json();
    if (!r.ok) {
      $("mapMsg").textContent = "추가 실패: " + (d.detail || r.status);
      return;
    }
    $("mapMsg").textContent = "공간 추가됨: " + d.source_id;
    $("newSpaceKey").value = "";
    $("newSpaceName").value = "";
    $("newPageId").value = "";
    $("newPageLabel").value = "";
    $("newIsExplicit").checked = false;
    await refreshMapSync();
  } catch (e) {
    $("mapMsg").textContent = String(e);
  }
};
```

- [ ] **Step 4: Manual verification in a browser**

Start the API + web stack per this repo's normal dev workflow (check `docs/CONFLUENCE_SYNC.md` or the repo root `docker-compose.yml`/README if unsure how this project is normally run locally), log in as an admin-role user, open `/admin.html`, and confirm:
- The Confluence 맵 동기화 table now shows a 상태 column and a toggle button per row.
- Clicking 비활성화 on a row flips its badge to "비활성" without a page reload, and the row still shows its `last_sync_at`/checkpoint.
- Clicking 활성화 flips it back.
- Filling in the "새 공간 추가" form with a fresh `space_key` and submitting adds a new row with 활성 status; submitting the same `space_key` again shows a "추가 실패: ... already exists" message via `mapMsg`.
- The summary paragraph above the table reads "N개 활성 공간(전체 M개) 순차 동기화…" and N decreases by one right after a toggle-to-disabled.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/admin.html
git commit -m "feat(admin): add/disable confluence_map sync spaces from admin.html"
```

---

### Task 6: Final regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full DB-free confluence_map + routers test set**

Run:
```bash
cd apps/api && python -m pytest tests/test_confluence_map_sync.py tests/test_confluence_map_source_seed.py tests/test_confluence_map_explicit_seeds.py tests/test_confluence_map_crawl.py -q
```
Expected: all pass.

- [ ] **Step 2: Run the full DB-backed confluence_map test set against the scratch DB**

Run:
```bash
cd apps/api && CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch DSN> python -m pytest \
  tests/test_confluence_map_inventory_db.py \
  tests/test_confluence_map_children_endpoint_db.py \
  tests/test_confluence_map_source_registry_db.py \
  tests/test_confluence_map_source_admin_endpoints_db.py \
  tests/test_search_confluence_map_freshness_db.py \
  -q
```
Expected: all pass. (`test_search_confluence_map_freshness_db.py` doesn't touch anything this plan changed, but it shares the `sources`/`documents` tables — a quick check it's unaffected is cheap insurance.)

- [ ] **Step 3: Grep for any remaining `MAP_SOURCE_DEFS` reference outside the seed module/migration/its own test**

Run: `cd apps/api && grep -rn "MAP_SOURCE_DEFS" app/ tests/ alembic/ | grep -v map_source_seed.py | grep -v 20260922_0007 | grep -v test_confluence_map_source_seed.py`
Expected: no output (a stray hit means Task 2 or 3 missed a call site — go fix it).

- [ ] **Step 4: Confirm nothing else imports `_ensure_source_row`**

Run: `cd apps/api && grep -rn "_ensure_source_row" app/ tests/`
Expected: no output.
