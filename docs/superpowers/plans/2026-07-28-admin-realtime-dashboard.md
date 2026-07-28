# Admin real-time dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GET /v1/ops/dashboard` admin endpoint that aggregates ingest/embedding progress, job-queue/worker status, API-process resource usage, and query-log stats, and wire `apps/web/public/admin.html` to poll it every 10s so operators get a live-updating view.

**Architecture:** One new module `apps/api/app/ops/dashboard.py` holds the aggregation logic, split into pure helper functions (unit-testable without a DB — this repo's CI runs with an unreachable `DATABASE_URL` so DB-touching code is never exercised in CI, matching the existing pattern in `app/routers/ops.py` and `app/jobs/queue.py`) and DB-session functions. `app/routers/ops.py` gets a thin new route that calls the module and wraps each section in try/except so a partial failure (e.g. missing `raw_manifest.json`) doesn't 500 the whole dashboard. The frontend change is confined to `apps/web/public/admin.html`.

**Tech Stack:** FastAPI, SQLAlchemy, vanilla JS (no build step — this repo serves static HTML/JS directly), pytest.

Design spec: `docs/superpowers/specs/2026-07-28-admin-realtime-dashboard-design.md`

---

### Task 1: Pure helper functions + unit tests

**Files:**
- Create: `apps/api/app/ops/__init__.py`
- Create: `apps/api/app/ops/dashboard.py`
- Test: `apps/api/tests/test_ops_dashboard_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_ops_dashboard_unit.py
import json

from app.ops.dashboard import (
    progress_row,
    read_raw_manifest,
    resource_snapshot,
    truncate_query_text,
)


def test_read_raw_manifest_missing_file(tmp_path):
    missing = tmp_path / "raw_manifest.json"
    assert read_raw_manifest(str(missing)) == {}


def test_read_raw_manifest_parses_source_files(tmp_path):
    manifest = tmp_path / "raw_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": {
                    "support_history": {"files": 2280, "by_ext": {".md": 2280}},
                    "vendor_docs": {"files": 0, "by_ext": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    result = read_raw_manifest(str(manifest))
    assert result == {"support_history": 2280, "vendor_docs": 0}


def test_read_raw_manifest_malformed_json(tmp_path):
    manifest = tmp_path / "raw_manifest.json"
    manifest.write_text("not json", encoding="utf-8")
    assert read_raw_manifest(str(manifest)) == {}


def test_progress_row_computes_embed_pct():
    row = progress_row(
        raw_files=100,
        documents=100,
        chunks=500,
        chunks_active=500,
        embeddings=250,
    )
    assert row["raw_files"] == 100
    assert row["documents"] == 100
    assert row["chunks_active"] == 500
    assert row["embeddings"] == 250
    assert row["embed_pct"] == 50


def test_progress_row_zero_chunks_is_100_pct():
    row = progress_row(
        raw_files=0,
        documents=0,
        chunks=0,
        chunks_active=0,
        embeddings=0,
    )
    assert row["embed_pct"] == 100


def test_progress_row_raw_files_none_when_manifest_missing():
    row = progress_row(
        raw_files=None,
        documents=5,
        chunks=10,
        chunks_active=10,
        embeddings=10,
    )
    assert row["raw_files"] is None
    assert row["embed_pct"] == 100


def test_resource_snapshot_shape(tmp_path):
    snap = resource_snapshot(str(tmp_path))
    assert "process_rss_mb" in snap
    assert "load_avg" in snap
    assert "disk" in snap
    assert snap["disk"]["path"] == str(tmp_path)
    assert snap["disk"]["total_gb"] >= 0
    assert 0 <= snap["disk"]["pct"] <= 100


def test_resource_snapshot_bad_path_returns_disk_error():
    snap = resource_snapshot("/no/such/path/at/all")
    assert snap["disk"].get("error")


def test_truncate_query_text_short_unchanged():
    assert truncate_query_text("hello") == "hello"


def test_truncate_query_text_truncates_with_ellipsis():
    text = "a" * 200
    result = truncate_query_text(text, limit=120)
    assert len(result) == 121  # 120 chars + ellipsis
    assert result.endswith("…")


def test_truncate_query_text_none_becomes_empty():
    assert truncate_query_text(None) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && python -m pytest tests/test_ops_dashboard_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ops'`

- [ ] **Step 3: Create the package and implement the pure helpers**

```python
# apps/api/app/ops/__init__.py
```

(empty file — marks `app.ops` as a package)

```python
# apps/api/app/ops/dashboard.py
"""Aggregation helpers for the admin real-time dashboard (GET /v1/ops/dashboard).

Split into pure functions (no DB/network — unit tested directly) and
session-based functions (exercised manually / in the running app, matching
the existing convention in app/routers/ops.py: this repo's CI runs with an
unreachable DATABASE_URL, so DB-touching code isn't unit tested).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("citec.ops.dashboard")


def read_raw_manifest(path: str) -> dict[str, int]:
    """Source -> raw file count from data/raw_manifest.json. {} if missing/invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    sources = data.get("sources") or {}
    out: dict[str, int] = {}
    for name, info in sources.items():
        if isinstance(info, dict) and "files" in info:
            try:
                out[name] = int(info["files"])
            except (TypeError, ValueError):
                continue
    return out


def progress_row(
    *,
    raw_files: Optional[int],
    documents: int,
    chunks: int,
    chunks_active: int,
    embeddings: int,
) -> dict[str, Any]:
    """One source_type's ingest/embed progress row. embed_pct is over active chunks."""
    embed_pct = 100 if chunks_active == 0 else round(embeddings / chunks_active * 100)
    return {
        "raw_files": raw_files,
        "documents": documents,
        "chunks": chunks,
        "chunks_active": chunks_active,
        "embeddings": embeddings,
        "embed_pct": min(100, embed_pct),
    }


def resource_snapshot(disk_path: str) -> dict[str, Any]:
    """API-process resource usage: RSS memory, host load average, disk usage at disk_path.

    Not per-container Docker stats — see design spec non-goals.
    """
    snap: dict[str, Any] = {}

    try:
        import resource as _resource

        rss_kb = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB; macOS reports bytes. This app only ships on Linux containers.
        snap["process_rss_mb"] = round(rss_kb / 1024, 1)
    except (ImportError, AttributeError, OSError):
        snap["process_rss_mb"] = None

    try:
        snap["load_avg"] = list(os.getloadavg())
    except (AttributeError, OSError):
        snap["load_avg"] = None

    try:
        total, used, _free = shutil.disk_usage(disk_path)
        total_gb = total / (1024**3)
        used_gb = used / (1024**3)
        pct = round(used / total * 100) if total else 0
        snap["disk"] = {
            "path": disk_path,
            "total_gb": round(total_gb, 1),
            "used_gb": round(used_gb, 1),
            "pct": pct,
        }
    except OSError as exc:
        snap["disk"] = {"path": disk_path, "error": str(exc)}

    return snap


def truncate_query_text(text: Optional[str], limit: int = 120) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && python -m pytest tests/test_ops_dashboard_unit.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
cd apps/api
git add app/ops/__init__.py app/ops/dashboard.py tests/test_ops_dashboard_unit.py
git commit -m "feat: add pure aggregation helpers for admin dashboard"
```

---

### Task 2: DB-backed aggregation functions

**Files:**
- Modify: `apps/api/app/ops/dashboard.py` (append)

No new unit tests here — these functions require a live Postgres session and this repo's CI intentionally runs with an unreachable `DATABASE_URL` (see `app/routers/ops.py::ops_status`, which is likewise not unit tested). Correctness is verified in Task 4's manual check against a running stack.

- [ ] **Step 1: Append DB-session functions to `apps/api/app/ops/dashboard.py`**

```python
def ingest_progress(session, raw_totals: dict[str, int]) -> dict[str, Any]:
    """Per-source_type ingest/embed progress + activity from the latest IngestJob.

    IngestJob rows are not per-source_type: a single filesystem ingest run
    (source_id is always "fs_raw", see app/ingest/pipeline.py::run_ingest)
    covers every source_type in one row, and records a per-source_type
    breakdown in IngestJob.stats["by_source"]. So instead of joining on a
    Source row (there is no per-source_type Source row to join on), take the
    single most recent IngestJob and, for each source_type, surface its
    entry from that job's by_source stats if present.
    """
    from sqlalchemy import func, select

    from app.db.models import Chunk, Document, Embedding, IngestJob

    doc_counts = dict(
        session.execute(
            select(Document.source_type, func.count()).group_by(Document.source_type)
        ).all()
    )
    chunk_counts = dict(
        session.execute(
            select(Document.source_type, func.count(Chunk.id))
            .join(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.source_type)
        ).all()
    )
    chunk_active_counts = dict(
        session.execute(
            select(Document.source_type, func.count(Chunk.id))
            .join(Chunk, Chunk.document_id == Document.id)
            .where(Chunk.is_active.is_(True))
            .group_by(Document.source_type)
        ).all()
    )
    embedding_counts = dict(
        session.execute(
            select(Document.source_type, func.count(Embedding.id))
            .join(Chunk, Chunk.document_id == Document.id)
            .join(Embedding, Embedding.chunk_id == Chunk.id)
            .where(Chunk.is_active.is_(True))
            .group_by(Document.source_type)
        ).all()
    )

    source_types = sorted(
        set(raw_totals)
        | set(doc_counts)
        | set(chunk_counts)
        | set(chunk_active_counts)
        | set(embedding_counts)
    )

    latest_job = session.execute(
        select(IngestJob).order_by(IngestJob.started_at.desc().nullslast()).limit(1)
    ).scalar_one_or_none()
    by_source_stats = {}
    if latest_job and isinstance(latest_job.stats, dict):
        by_source_stats = latest_job.stats.get("by_source") or {}

    rows: dict[str, Any] = {}
    for st in source_types:
        rows[st] = progress_row(
            raw_files=raw_totals.get(st),
            documents=int(doc_counts.get(st, 0)),
            chunks=int(chunk_counts.get(st, 0)),
            chunks_active=int(chunk_active_counts.get(st, 0)),
            embeddings=int(embedding_counts.get(st, 0)),
        )

        last_job = None
        if latest_job is not None and st in by_source_stats:
            last_job = {
                "id": latest_job.id,
                "mode": latest_job.mode,
                "status": latest_job.status,
                "started_at": latest_job.started_at.isoformat() if latest_job.started_at else None,
                "finished_at": latest_job.finished_at.isoformat() if latest_job.finished_at else None,
                "error": latest_job.error,
                "by_source": by_source_stats.get(st),
            }
        rows[st]["last_job"] = last_job

    return rows


def query_stats(session) -> dict[str, Any]:
    """Query volume/latency stats from QueryLog, last 1h/24h + recent 10."""
    from sqlalchemy import func, select

    from app.db.models import QueryLog

    now = datetime.now(timezone.utc)
    since_1h = now - timedelta(hours=1)
    since_24h = now - timedelta(hours=24)

    count_1h = session.scalar(
        select(func.count()).select_from(QueryLog).where(QueryLog.created_at >= since_1h)
    ) or 0
    count_24h = session.scalar(
        select(func.count()).select_from(QueryLog).where(QueryLog.created_at >= since_24h)
    ) or 0
    avg_latency = session.scalar(
        select(func.avg(QueryLog.latency_ms)).where(QueryLog.created_at >= since_24h)
    )

    recent_rows = session.execute(
        select(QueryLog).order_by(QueryLog.created_at.desc()).limit(10)
    ).scalars().all()

    return {
        "count_1h": int(count_1h),
        "count_24h": int(count_24h),
        "avg_latency_ms_24h": round(float(avg_latency), 1) if avg_latency is not None else None,
        "recent": [
            {
                "id": q.id,
                "query": truncate_query_text(q.query),
                "mode": q.mode,
                "latency_ms": q.latency_ms,
                "created_at": q.created_at.isoformat() if q.created_at else None,
            }
            for q in recent_rows
        ],
    }
```

- [ ] **Step 2: Byte-compile to catch syntax errors**

Run: `cd apps/api && python -m py_compile app/ops/dashboard.py`
Expected: no output (success)

- [ ] **Step 3: Commit**

```bash
cd apps/api
git add app/ops/dashboard.py
git commit -m "feat: add DB-backed ingest/query aggregation for admin dashboard"
```

---

### Task 3: Wire the `GET /v1/ops/dashboard` route

**Files:**
- Modify: `apps/api/app/routers/ops.py`
- Test: `apps/api/tests/test_ops_dashboard_auth.py`

- [ ] **Step 1: Add the route**

Append to `apps/api/app/routers/ops.py` (after the existing `ops_status` function):

```python
@router.get("/dashboard")
def ops_dashboard(
    principal: Principal = Depends(require_roles("admin")),
) -> dict[str, Any]:
    """Aggregated real-time view for the admin dashboard (admin-only)."""
    _ = principal
    from pathlib import Path

    from app.ops.dashboard import ingest_progress, query_stats, read_raw_manifest, resource_snapshot
    from app.jobs.queue import list_jobs

    settings = get_settings()
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        manifest_path = str(Path(settings.raw_dir).parent / "raw_manifest.json")
        raw_totals = read_raw_manifest(manifest_path)
        with session_scope() as session:
            result["ingest_progress"] = ingest_progress(session, raw_totals)
    except Exception as exc:  # noqa: BLE001
        result["ingest_progress"] = {"error": str(exc)}

    try:
        result["jobs"] = list_jobs(limit=20)
    except Exception as exc:  # noqa: BLE001
        result["jobs"] = {"error": str(exc)}

    try:
        result["resources"] = resource_snapshot(settings.raw_dir)
    except Exception as exc:  # noqa: BLE001
        result["resources"] = {"error": str(exc)}

    try:
        with session_scope() as session:
            result["queries"] = query_stats(session)
    except Exception as exc:  # noqa: BLE001
        result["queries"] = {"error": str(exc)}

    return result
```

Add these imports at the top of the file alongside the existing ones:

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.auth.deps import require_roles
from app.auth.principal import Principal
```

(`APIRouter` is already imported — just add `Depends` to that same import line rather than duplicating it. Check the existing `from fastapi import APIRouter` line at the top of `ops.py` and extend it to `from fastapi import APIRouter, Depends`.)

- [ ] **Step 2: Byte-compile to catch syntax errors**

Run: `cd apps/api && python -m py_compile app/routers/ops.py`
Expected: no output (success)

- [ ] **Step 3: Write an automated auth-gate test**

The full 200-response shape needs a live Postgres session (`ingest_progress`/`query_stats`
call `session_scope()`), so — matching the existing convention where `ops_status` and other
DB-touching routes aren't exercised by CI's unit tests (CI runs with an unreachable
`DATABASE_URL`) — that path is covered by the manual smoke test in Step 4, not here.
What *is* safe to test without a DB is the auth gate: `require_roles("admin")` raises
`HTTPException` before the route body ever calls `session_scope()`, so a 401/403 response
never touches the database. Mount just the `ops` router on a bare `FastAPI()` app (not
`app.main`, which has a heavy import chain) to keep this test fast and DB-free.

```python
# apps/api/tests/test_ops_dashboard_auth.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.ops import router as ops_router
from app.settings import get_settings


def setup_function():
    get_settings.cache_clear()


def teardown_function():
    get_settings.cache_clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ops_router)
    return TestClient(app)


def test_dashboard_401_when_anonymous_and_auth_enforced(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    get_settings.cache_clear()
    r = _client().get("/v1/ops/dashboard")
    assert r.status_code == 401


def test_dashboard_403_when_role_insufficient(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv(
        "AUTH_TOKENS_JSON",
        '{"tok-v": {"sub": "v1", "name": "V", "roles": ["viewer"]}}',
    )
    get_settings.cache_clear()
    r = _client().get(
        "/v1/ops/dashboard", headers={"Authorization": "Bearer tok-v"}
    )
    assert r.status_code == 403


def test_dashboard_allowed_when_auth_off_reaches_db_call(monkeypatch):
    """AUTH_MODE=off grants admin, so the request passes the auth gate and the
    route body runs. Its DB-touching sections (ingest_progress/query_stats)
    each wrap their session_scope() call in try/except and degrade to an
    {"error": ...} value instead of raising, so the response is still 200 —
    but with error strings in ingest_progress/queries against this test's
    unreachable DATABASE_URL. That distinguishes "passed the auth gate and
    ran" from the 401/403 cases above, without needing a live database."""
    monkeypatch.setenv("AUTH_MODE", "off")
    get_settings.cache_clear()
    r = _client().get("/v1/ops/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body["ingest_progress"]
    assert "error" in body["queries"]
    # resources has no DB dependency, so it should succeed normally.
    assert "error" not in body["resources"]
```

Run: `cd apps/api && python -m pytest tests/test_ops_dashboard_auth.py -v`
Expected: PASS (3 tests) — the CI env vars in `.github/workflows/ci.yml` already point
`DATABASE_URL` at an unreachable port, which is exactly what `test_dashboard_allowed_when_auth_off_reaches_db_call` relies on.

- [ ] **Step 4: Manual smoke test against a running stack**

Run: `docker compose up -d postgres redis api` (or however this repo's dev stack is normally started — see `docs/DEPLOY.md`), then:

```bash
curl -s http://localhost:8000/v1/ops/dashboard | python -m json.tool
```

Expected: with `AUTH_MODE=off` (default), a 200 response with `generated_at`, `ingest_progress`, `jobs`, `resources`, `queries` keys, no top-level 500.

- [ ] **Step 5: Commit**

```bash
cd apps/api
git add app/routers/ops.py tests/test_ops_dashboard_auth.py
git commit -m "feat: add GET /v1/ops/dashboard route with admin-gate tests"
```

---

### Task 4: Frontend — auto-refreshing admin dashboard

**Files:**
- Modify: `apps/web/public/admin.html`

- [ ] **Step 1: Replace the file contents**

The existing Session/Platform/Quick-links cards and the `ops/status` / `auth/status` / `job log` `<pre>` panels stay. Add: an auto-refresh toggle, a resources card, an ingest/embedding progress table, a job-queue panel, and a query-stats panel — all sourced from the new `/v1/ops/dashboard` endpoint.

```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Admin / Ops — CI-TEC Knowledge</title>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#047857; --bad:#b91c1c; --warn:#b45309; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; align-items:center; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
  .wrap { max-width:1200px; margin:0 auto; padding:24px 16px 60px; }
  h1 { font-size:1.4rem; margin:0 0 8px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:12px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
  .badge { display:inline-block; font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px; margin-right:4px; }
  .ok { background:#d1fae5; color:var(--ok); }
  .bad { background:#fee2e2; color:var(--bad); }
  .warn { background:#fef3c7; color:var(--warn); }
  .na { background:#f1f5f9; color:var(--muted); }
  button { background:var(--primary); color:#fff; border:0; border-radius:10px; padding:8px 14px; font-weight:700; cursor:pointer; margin:4px 4px 4px 0; }
  button.ghost { background:#fff; color:var(--primary); border:1px solid var(--border); }
  pre { background:#f8fafc; border:1px solid var(--border); border-radius:8px; padding:10px; font-size:11px; overflow:auto; max-height:280px; }
  .meta { color:var(--muted); font-size:12px; }
  .row { display:flex; justify-content:space-between; gap:8px; padding:6px 0; border-bottom:1px solid var(--border); font-size:14px; }
  .row:last-child { border-bottom:0; }
  a.link { color:var(--primary); font-weight:600; }
  table.tbl { width:100%; border-collapse:collapse; font-size:12px; }
  table.tbl th { text-align:left; padding:5px 8px; border-bottom:1px solid var(--border); color:var(--muted); font-size:11px; }
  table.tbl td { padding:5px 8px; border-bottom:1px solid var(--border); vertical-align:middle; }
  .pb { height:6px; background:#eef2f7; border-radius:3px; overflow:hidden; min-width:80px; }
  .pb-f { height:100%; border-radius:3px; background:var(--primary); }
  .job-c { background:#f8fafc; border:1px solid var(--border); border-radius:8px; padding:8px 10px; margin-bottom:6px; font-size:12px; }
  .job-top { display:flex; justify-content:space-between; margin-bottom:4px; }
  label.toggle { display:flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); }
</style>
</head>
<body>
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/insights.html">Insight</a>
  <a href="/bundles.html">번들</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
<div class="wrap">
  <h1>Admin / Ops 대시보드</h1>
  <p class="sub">운영자 페르소나 · health · auth · worker · pilot 링크 · job ping (admin 토큰 시)</p>

  <div style="margin-bottom:12px">
    <button type="button" id="btnRefresh">전체 새로고침</button>
    <button type="button" class="ghost" id="btnPing">Job ping</button>
    <button type="button" class="ghost" id="btnJob">Job status poll</button>
    <label class="toggle"><input type="checkbox" id="chkAuto" checked/> 자동 갱신 (10s)</label>
    <span id="msg" class="meta"></span>
  </div>

  <div class="grid">
    <div class="card">
      <strong>Session</strong>
      <div class="row"><span>principal</span><span id="who" class="meta">…</span></div>
      <div class="row"><span>auth_mode</span><span id="amode" class="badge na">…</span></div>
      <div class="row"><span>enforced</span><span id="aenf" class="badge na">…</span></div>
      <div class="row"><span>oidc configured</span><span id="aoidc" class="badge na">…</span></div>
    </div>
    <div class="card">
      <strong>Platform</strong>
      <div class="row"><span>API health</span><span id="hapi" class="badge na">…</span></div>
      <div class="row"><span>Postgres</span><span id="hpg" class="badge na">…</span></div>
      <div class="row"><span>Redis</span><span id="hredis" class="badge na">…</span></div>
      <div class="row"><span>LLM</span><span id="hllm" class="badge na">…</span></div>
      <div class="row"><span>Worker</span><span id="hworker" class="badge na">…</span></div>
      <div class="row"><span>Pilot ready</span><span id="hpilot" class="badge na">…</span></div>
    </div>
    <div class="card">
      <strong>리소스 (API 프로세스 기준)</strong>
      <div class="row"><span>RSS 메모리</span><span id="rRss" class="meta">…</span></div>
      <div class="row"><span>Load avg (1m)</span><span id="rLoad" class="meta">…</span></div>
      <div class="row"><span>디스크 (raw_dir)</span><span id="rDiskPct" class="meta">…</span></div>
      <div class="pb"><div class="pb-f" id="rDiskBar" style="width:0%"></div></div>
      <div class="row"><span>쿼리 (1h / 24h)</span><span id="qCounts" class="meta">…</span></div>
      <div class="row"><span>평균 지연 (24h)</span><span id="qLatency" class="meta">…</span></div>
    </div>
    <div class="card">
      <strong>Quick links</strong>
      <p class="meta" style="line-height:1.8">
        <a class="link" href="/insights.html">Insight 승인</a><br/>
        <a class="link" href="/login.html">Login / SSO</a><br/>
        <a class="link" href="/docs/oidc-idp-setup.html">OIDC IdP 가이드</a><br/>
        <a class="link" href="/docs/implementation-plan.html">구현 계획</a><br/>
        <a class="link" href="/docs/phase2-pilot-checklist.html">파일럿 체크리스트</a><br/>
        <a class="link" href="/docs/pilot-signoff.html">도메인 사인 증거 팩</a><br/>
        <a class="link" href="/api/docs" target="_blank">Swagger</a>
      </p>
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <strong>인제스트 / 임베딩 진행률</strong>
    <table class="tbl">
      <thead><tr><th>소스</th><th>raw 파일</th><th>문서</th><th>청크(활성)</th><th>임베딩</th><th>진행률</th><th>최근 잡</th></tr></thead>
      <tbody id="ingestBody"><tr><td colspan="7" class="meta">로딩 중…</td></tr></tbody>
    </table>
  </div>

  <div class="grid" style="margin-top:12px">
    <div class="card">
      <strong>작업 큐</strong>
      <div class="row"><span>queue_length</span><span id="jQueueLen" class="meta">…</span></div>
      <div class="row"><span>worker heartbeat</span><span id="jWorker" class="badge na">…</span></div>
      <div id="jobList" style="margin-top:8px"></div>
    </div>
    <div class="card">
      <strong>최근 쿼리 (최대 10건)</strong>
      <table class="tbl">
        <thead><tr><th>시각</th><th>모드</th><th>지연</th><th>쿼리</th></tr></thead>
        <tbody id="queryBody"><tr><td colspan="4" class="meta">로딩 중…</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <strong>ops/status</strong>
    <pre id="ops">…</pre>
  </div>
  <div class="card" style="margin-top:12px">
    <strong>auth/status</strong>
    <pre id="auth">…</pre>
  </div>
  <div class="card" style="margin-top:12px">
    <strong>job log</strong>
    <pre id="job">—</pre>
  </div>
</div>
<script src="/js/auth.js"></script>
<script>
const $ = (id) => document.getElementById(id);
function badge(el, ok, text) {
  el.className = "badge " + (ok === true ? "ok" : ok === false ? "bad" : "na");
  el.textContent = text;
}
function fmtTs(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString("ko-KR"); } catch (e) { return iso; }
}
function jobBadgeClass(status) {
  if (status === "success" || status === "done" || status === "completed") return "ok";
  if (status === "failed" || status === "error") return "bad";
  if (status === "running" || status === "in_progress" || status === "queued") return "warn";
  return "na";
}

async function refresh() {
  $("msg").textContent = "loading…";
  try {
    const me = await CitecAuth.me();
    const p = (me.data && me.data.principal) || {};
    $("who").textContent = (p.sub || "?") + " · " + ((p.roles || []).join(",") || "—") + " · " + (p.auth_via || "");

    const auth = await CitecAuth.status();
    $("auth").textContent = JSON.stringify(auth, null, 2);
    badge($("amode"), null, auth.auth_mode || "?");
    badge($("aenf"), !!auth.enforced, String(!!auth.enforced));
    const oc = !!(auth.oidc && auth.oidc.configured);
    badge($("aoidc"), oc, oc ? "yes" : "no");

    const hr = await fetch("/v1/health");
    const health = await hr.json();
    badge($("hapi"), health.status === "ok", health.status || "?");
    badge($("hpg"), !!(health.checks && health.checks.postgres && health.checks.postgres.ok), "pg");
    badge($("hredis"), !!(health.checks && health.checks.redis && health.checks.redis.ok), "redis");
    badge($("hllm"), !!(health.checks && health.checks.llm && health.checks.llm.ok), (health.checks && health.checks.llm && health.checks.llm.backend) || "llm");

    const or = await CitecAuth.apiFetch("/v1/ops/status");
    const ops = await or.json();
    $("ops").textContent = JSON.stringify(ops, null, 2);
    const w = ops.checks && ops.checks.worker;
    badge($("hworker"), !!(w && w.ok), w && w.ok ? "up" : "down");
    badge($("hpilot"), !!ops.pilot_engineering_ready, ops.pilot_engineering_ready ? "ready" : "no");

    await refreshDashboard();

    $("msg").textContent = "ok " + new Date().toLocaleTimeString();
  } catch (e) {
    $("msg").textContent = String(e);
  }
}

async function refreshDashboard() {
  const dr = await CitecAuth.apiFetch("/v1/ops/dashboard");
  if (dr.status === 401 || dr.status === 403) {
    $("ingestBody").innerHTML = '<tr><td colspan="7" class="meta">관리자 권한 필요 (로그인 후 admin 역할 필요)</td></tr>';
    $("queryBody").innerHTML = '<tr><td colspan="4" class="meta">관리자 권한 필요</td></tr>';
    return;
  }
  const d = await dr.json();

  const res = d.resources || {};
  $("rRss").textContent = res.process_rss_mb != null ? res.process_rss_mb + " MB" : "—";
  $("rLoad").textContent = (res.load_avg && res.load_avg.length) ? res.load_avg[0].toFixed(2) : "—";
  const disk = res.disk || {};
  if (disk.pct != null) {
    $("rDiskPct").textContent = disk.used_gb + " / " + disk.total_gb + " GB (" + disk.pct + "%)";
    $("rDiskBar").style.width = disk.pct + "%";
  } else {
    $("rDiskPct").textContent = disk.error || "—";
  }

  const q = d.queries || {};
  $("qCounts").textContent = (q.count_1h != null ? q.count_1h : "—") + " / " + (q.count_24h != null ? q.count_24h : "—");
  $("qLatency").textContent = q.avg_latency_ms_24h != null ? q.avg_latency_ms_24h + " ms" : "—";

  const ip = d.ingest_progress || {};
  if (ip.error) {
    $("ingestBody").innerHTML = '<tr><td colspan="7" class="meta">' + ip.error + '</td></tr>';
  } else {
    const sources = Object.keys(ip).sort();
    $("ingestBody").innerHTML = sources.length
      ? sources.map((s) => {
          const row = ip[s];
          const job = row.last_job;
          const jobLabel = job
            ? '<span class="badge ' + jobBadgeClass(job.status) + '">' + job.status + '</span> ' + fmtTs(job.finished_at || job.started_at)
            : '<span class="meta">—</span>';
          return '<tr><td>' + s + '</td><td>' + (row.raw_files != null ? row.raw_files : '—') +
            '</td><td>' + row.documents + '</td><td>' + row.chunks_active +
            '</td><td>' + row.embeddings +
            '</td><td><div class="pb"><div class="pb-f" style="width:' + row.embed_pct + '%"></div></div>' + row.embed_pct + '%</td>' +
            '<td>' + jobLabel + '</td></tr>';
        }).join("")
      : '<tr><td colspan="7" class="meta">데이터 없음</td></tr>';
  }

  const jobs = d.jobs || {};
  if (jobs.error) {
    $("jQueueLen").textContent = jobs.error;
    badge($("jWorker"), false, "error");
    $("jobList").innerHTML = "";
  } else {
    $("jQueueLen").textContent = jobs.queue_length != null ? jobs.queue_length : "—";
    const w = jobs.worker || {};
    badge($("jWorker"), !!w.ok, w.ok ? "up (" + (w.age_sec != null ? w.age_sec + "s ago" : "—") + ")" : "down");
    const items = jobs.items || [];
    $("jobList").innerHTML = items.length
      ? items.slice(0, 8).map((j) => {
          return '<div class="job-c"><div class="job-top"><span>' + (j.type || j.id || "—") +
            '</span><span class="badge ' + jobBadgeClass(j.status) + '">' + (j.status || "—") + '</span></div>' +
            '<div class="meta">' + (j.id || "") + (j.error ? " · " + j.error : "") + '</div></div>';
        }).join("")
      : '<div class="meta">활성 잡 없음</div>';
  }

  const qb = (d.queries || {}).recent || [];
  $("queryBody").innerHTML = qb.length
    ? qb.map((r) => {
        return '<tr><td>' + fmtTs(r.created_at) + '</td><td>' + (r.mode || "—") +
          '</td><td>' + (r.latency_ms != null ? r.latency_ms + "ms" : "—") +
          '</td><td>' + (r.query || "") + '</td></tr>';
      }).join("")
    : '<tr><td colspan="4" class="meta">기록 없음</td></tr>';
}

let lastJobId = null;
$("btnPing").onclick = async () => {
  try {
    const r = await CitecAuth.apiFetch("/v1/jobs", { method: "POST", body: { type: "ping" } });
    const d = await r.json();
    $("job").textContent = JSON.stringify(d, null, 2);
    lastJobId = d.id || null;
    if (!r.ok) $("msg").textContent = "job " + r.status + " (admin role required when AUTH enforced)";
    else $("msg").textContent = "queued " + lastJobId;
  } catch (e) {
    $("msg").textContent = String(e);
  }
};
$("btnJob").onclick = async () => {
  if (!lastJobId) { $("msg").textContent = "no job id — ping first"; return; }
  const r = await CitecAuth.apiFetch("/v1/jobs/" + lastJobId);
  const d = await r.json();
  $("job").textContent = JSON.stringify(d, null, 2);
};
$("btnRefresh").onclick = () => refresh();

let _timer = null;
function scheduleAuto() {
  if (_timer) clearInterval(_timer);
  if (!$("chkAuto").checked) return;
  _timer = setInterval(refresh, 10000);
}
$("chkAuto").onchange = scheduleAuto;

CitecAuth.mountChip(".top");
refresh();
scheduleAuto();
</script>
</body>
</html>
```

- [ ] **Step 2: Manual browser check**

With the dev stack running (`docker compose up -d`) and logged in as an admin (see `docs/OIDC_IDP_SETUP.md` or `AUTH_MODE=off` for local testing where every request is treated as admin), open `http://localhost:<web-port>/admin.html` and confirm:
- Ingest progress table populates with source rows and a progress bar.
- Job queue panel shows worker heartbeat status.
- Recent queries table populates after running a search.
- Unchecking "자동 갱신 (10s)" stops further requests (watch browser Network tab); rechecking resumes them.
- With `AUTH_MODE=apikey` (or another enforced mode) and no token, the ingest/query panels show "관리자 권한 필요" instead of breaking the page.

- [ ] **Step 3: Commit**

```bash
git add apps/web/public/admin.html
git commit -m "feat: auto-refreshing admin dashboard (ingest/embed progress, jobs, resources, queries)"
```

---

### Task 5: Final check — full test suite

**Files:** none (verification only)

- [ ] **Step 1: Run the full API unit test suite**

Run: `cd apps/api && python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py`
Expected: all tests pass, including the 13 new tests from Task 1.

- [ ] **Step 2: Confirm no stray debug code**

Run: `cd apps/api && grep -rn "console.log\|print(" app/ops/dashboard.py`
Expected: no output (the module only uses `logger`, not raw prints).
