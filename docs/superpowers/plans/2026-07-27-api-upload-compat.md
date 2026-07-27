# `/api/upload` wiki-qa 호환 업로드 API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** citec-wiki-qa의 `POST /api/upload` 계약(파일 업로드 → `job_id` 즉시 반환 → 백그라운드
ingest → `GET /api/ingest-status/{job_id}` SSE 폴링)을 citec-kb의 기존 `/api/*` 호환 레이어에
그대로 이식해, 이미 그 계약으로 파일을 보내고 있는 외부 시스템(기술지원이력/테크리포/DBMS튜닝)이
코드 변경 없이 citec-kb로 계속 업로드할 수 있게 한다.

**Architecture:** `app/routers/external_compat.py`(기존 wiki-qa 호환 라우터)에 3개 엔드포인트를
추가한다. 업로드된 파일은 `raw_dir/<type>/`에 저장(디스크 미러 유지)한 뒤, `IngestJob` DB row를
`pending`으로 만들어 `job_id`를 즉시 응답하고, `BackgroundTasks`로 단일 파일 파싱(`app.ingest.adapters`
신규 헬퍼) + `app.ingest.pipeline.upsert_document_from_draft` 반영을 비동기 실행한다. 이는 기존
`POST /v1/ingest/run`(`async_mode=true`)이 이미 쓰는 것과 동일한 "BackgroundTasks + DB job row"
패턴이며, Redis 큐(`app/jobs/queue.py`)는 건드리지 않는다. `vendor_docs`/`checkitems`(XLS)는
citec-kb에 파서가 없어 `501`로 명확히 거부한다(스펙 참고: `docs/superpowers/specs/2026-07-27-api-upload-compat-design.md`).

**Tech Stack:** FastAPI (`UploadFile`/`File`/`Form`/`BackgroundTasks`), SQLAlchemy 2.x ORM
(`IngestJob` 모델), 기존 `app.ingest.adapters` / `app.ingest.pipeline` 모듈.

**테스트 방침(중요):** 이 리포의 CI(`​.github/workflows/ci.yml`)는 **DB/Redis 없이** 순수 단위
테스트만 돈다(`test_mock_idp_e2e.py`처럼 DB가 필요한 테스트는 CI에서 제외됨). 따라서 이 계획의
자동 테스트는 **DB 없이 도는 순수 함수**(파일 파서, 별칭/확장자/파일명 검증)만 TDD로 작성한다.
DB에 실제로 쓰는 엔드포인트 본체(`POST /api/upload` 등)는 자동 테스트 없이 구현하고, 대신
Task 8에서 `docker compose`로 띄운 실제 서버에 curl로 수동 스모크 검증한다 — 이는 임시방편이
아니라 이 리포의 기존 테스트 전략을 그대로 따르는 것이다.

---

## Task 1: `app/ingest/adapters.py` — 단일 파일 파서 추출

**Files:**
- Modify: `apps/api/app/ingest/adapters.py:52-186` (`iter_support_history`, `iter_tech_repo`, `iter_tuning_ai`)
- Test: `apps/api/tests/test_adapters_single_file.py` (신규)

기존 `iter_support_history`/`iter_tech_repo`/`iter_tuning_ai`는 디렉토리 전체를 순회하며 파일마다
`DocumentDraft`를 만든다. 이 로직에서 "파일 1개 → `DocumentDraft` 1개" 부분만 뽑아
`parse_support_history_file(path)` / `parse_tech_repo_file(path)` / `parse_tuning_ai_file(path)`로
독립시키고, 기존 `iter_*` 함수는 이 헬퍼를 호출하는 얇은 래퍼로 만든다. 업로드 API가 디렉토리
전체를 재스캔하지 않고 새 파일 1개만 즉시 파싱할 수 있게 하기 위함이며, 기존 동작(전체 재구축)은
그대로 보존된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_adapters_single_file.py` 파일을 새로 만든다:

```python
"""Unit tests for single-file parse helpers used by /api/upload (no DB)."""

from pathlib import Path

from app.ingest.adapters import (
    parse_support_history_file,
    parse_tech_repo_file,
    parse_tuning_ai_file,
)

_SUPPORT_HISTORY_MD = """# CITECTS-9999 테스트 이슈

- **Issue Key**: CITECTS-9999
- **Status**: 닫힘
- **Component**: Redis

Redis 커넥션 타임아웃 관련 지원이력 본문입니다.
"""

_TECH_REPO_MD = """---
제목 : 커널 파라미터 튜닝
Page ID : 148554390
URL : https://confluence.example/pages/148554390
디렉토리 : 루트 > 기술문서 > 운영체제 > 커널
---
# 커널 파라미터 튜닝

sysctl 파라미터 튜닝 본문입니다.
"""

_TUNING_AI_MD = """---
issue_id: ISS-1234
domain: oracle
---
# OOM 이슈 분석

Oracle OOM 이슈 분석 본문입니다.
"""


def test_parse_support_history_file(tmp_path: Path):
    p = tmp_path / "CITECTS-9999.md"
    p.write_text(_SUPPORT_HISTORY_MD, encoding="utf-8")

    draft = parse_support_history_file(p)

    assert draft.source_type == "support_history"
    assert draft.external_id == "CITECTS-9999"
    assert draft.title == "CITECTS-9999 테스트 이슈"
    assert draft.evidence_grade == "A"  # Status: 닫힘
    assert draft.work_type == "Redis"
    assert "Redis 커넥션 타임아웃" in draft.body_md
    assert draft.content_hash  # finalize() was called


def test_parse_tech_repo_file(tmp_path: Path):
    p = tmp_path / "148554390_kernel.md"
    p.write_text(_TECH_REPO_MD, encoding="utf-8")

    draft = parse_tech_repo_file(p)

    assert draft.source_type == "tech_repo"
    assert draft.external_id == "148554390"
    assert draft.title == "커널 파라미터 튜닝"
    assert draft.domain == "os"
    assert draft.path_l2 == "운영체제"
    assert draft.path_l3 == "운영체제 > 커널"
    assert draft.source_uri == "https://confluence.example/pages/148554390"
    assert draft.content_hash


def test_parse_tuning_ai_file(tmp_path: Path):
    p = tmp_path / "ISS-1234.md"
    p.write_text(_TUNING_AI_MD, encoding="utf-8")

    draft = parse_tuning_ai_file(p)

    assert draft.source_type == "tuning_ai"
    assert draft.external_id == "ISS-1234"
    assert draft.title == "OOM 이슈 분석"
    assert draft.domain == "oracle"
    assert draft.content_hash


def test_iter_support_history_matches_single_file_parse(tmp_path: Path):
    """Regression: directory-scan path must still work after the refactor."""
    d = tmp_path / "support_history"
    d.mkdir()
    (d / "CITECTS-9999.md").write_text(_SUPPORT_HISTORY_MD, encoding="utf-8")

    from app.ingest.adapters import iter_support_history

    drafts = list(iter_support_history(tmp_path))
    assert len(drafts) == 1
    assert drafts[0].external_id == "CITECTS-9999"
    assert drafts[0].content_hash == parse_support_history_file(d / "CITECTS-9999.md").content_hash
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_adapters_single_file.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_support_history_file' from 'app.ingest.adapters'`

- [ ] **Step 3: `adapters.py`에서 단일 파일 파서 추출**

`apps/api/app/ingest/adapters.py`의 `iter_support_history` (라인 52-79)를 아래로 교체:

```python
def parse_support_history_file(path: Path) -> DocumentDraft:
    raw = _read(path)
    title_m = re.search(r"^#\s+(.+)$", raw, re.M)
    title = title_m.group(1).strip() if title_m else path.stem
    meta: dict[str, Any] = {"filename": path.name}
    for m in _META_LINE.finditer(raw):
        meta[m.group(1).strip()] = m.group(2).strip()
    issue_key = meta.get("Issue Key") or path.stem
    body = clean_md(raw)
    grade = "A" if meta.get("Status") in ("닫힘", "Resolved", "Done") else "B"
    work = meta.get("Component")
    return DocumentDraft(
        source_type="support_history",
        external_id=str(issue_key),
        title=_clip(title, 1000),
        body_md=body,
        metadata=meta,
        source_uri=f"file://support_history/{path.name}",
        evidence_grade=grade,
        work_type=work,
        environment="csp" if re.search(r"SCP|클라우드", title + body[:2000], re.I) else None,
    ).finalize()


def iter_support_history(root: Path) -> Iterator[DocumentDraft]:
    d = root / "support_history"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        if path.name.startswith("."):
            continue
        yield parse_support_history_file(path)
```

`iter_tech_repo` (라인 82-125, `iter_confluence_docs` 시작 전까지)를 아래로 교체:

```python
def parse_tech_repo_file(path: Path) -> DocumentDraft:
    raw = _read(path)
    meta: dict[str, Any] = {"filename": path.name}
    body = raw
    fm = _FRONT_YAML.match(raw)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" in line or "：" in line:
                # "구분 : 컨플루언스"
                parts = re.split(r"[:：]", line, maxsplit=1)
                if len(parts) == 2:
                    meta[parts[0].strip()] = parts[1].strip()
        body = raw[fm.end() :]
    page_id = meta.get("Page ID") or path.stem.replace("confluence_", "")
    title = meta.get("제목") or ""
    if not title or len(title) < 2:
        h = re.search(r"^#{1,3}\s+(.+)$", body, re.M)
        title = h.group(1).strip() if h else path.stem
    # Never use huge log lines as title
    if len(title) > 200:
        title = title[:200]
    directory = meta.get("디렉토리") or ""
    path_parts = [p.strip() for p in directory.split(">") if p.strip()]
    path_l2 = path_parts[2] if len(path_parts) >= 3 else (path_parts[-1] if path_parts else None)
    path_l3 = (
        f"{path_parts[2]} > {path_parts[3]}" if len(path_parts) >= 4 else path_l2
    )
    body = clean_md(body)
    return DocumentDraft(
        source_type="tech_repo",
        external_id=str(page_id),
        title=_clip(title or path.stem, 1000),
        body_md=body,
        metadata=meta,
        source_uri=meta.get("URL") or f"file://tech_repo/{path.name}",
        evidence_grade="A",
        path_l2=path_l2,
        path_l3=path_l3,
        domain=_domain_from_path(directory),
    ).finalize()


def iter_tech_repo(root: Path) -> Iterator[DocumentDraft]:
    d = root / "tech_repo"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        yield parse_tech_repo_file(path)
```

`iter_tuning_ai` (라인 157-186)를 아래로 교체:

```python
def parse_tuning_ai_file(path: Path) -> DocumentDraft:
    raw = _read(path)
    meta: dict[str, Any] = {"filename": path.name}
    body = raw
    fm = _FRONT_YAML.match(raw)
    if fm:
        # YAML-ish key: value
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
        body = raw[fm.end() :]
    issue_id = meta.get("issue_id") or path.stem
    title_m = re.search(r"^#\s+(.+)$", body, re.M)
    title = title_m.group(1).strip() if title_m else path.stem
    return DocumentDraft(
        source_type="tuning_ai",
        external_id=str(issue_id),
        title=_clip(title, 1000),
        body_md=clean_md(body),
        metadata=meta,
        source_uri=f"file://tuning_ai/{path.name}",
        evidence_grade="A-",
        domain=meta.get("domain"),
        environment=None,
    ).finalize()


def iter_tuning_ai(root: Path) -> Iterator[DocumentDraft]:
    d = root / "tuning_ai"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        yield parse_tuning_ai_file(path)
```

`_domain_from_path`는 이미 파일 하단(구 라인 247)에 정의되어 있으므로 그대로 둔다(정의 순서상
`parse_tech_repo_file`이 이를 참조해도 모듈 로드 시점에는 문제없음 — 함수 바디 안의 참조는
호출 시점에 해석됨).

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_adapters_single_file.py -v`
Expected: `4 passed`

- [ ] **Step 5: 기존 어댑터 테스트 회귀 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py`
Expected: 전체 통과 (리팩터 전 통과하던 테스트 수와 동일, 신규 4개 추가)

- [ ] **Step 6: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/ingest/adapters.py apps/api/tests/test_adapters_single_file.py
git commit -m "refactor(ingest): extract single-file parsers for support_history/tech_repo/tuning_ai"
```

---

## Task 2: 업로드 검증 헬퍼 (`source_type` 별칭, 확장자, 파일명)

**Files:**
- Modify: `apps/api/app/routers/external_compat.py` (상단 import 블록 + 새 섹션 추가)
- Test: `apps/api/tests/test_external_compat.py`

`_map_section`/`_SECTION_MAP`은 **검색용** wiki-qa section → citec-kb source_type 매핑이며,
업로드용 별칭 표(citec-wiki-qa README "별칭" 절)와는 값이 다르다(예: `confluence_docs`가
검색에서는 그대로 통과하지만 업로드에서는 `tech_repo`로 정규화되어야 함). 그래서 업로드 전용
매핑 `_UPLOAD_ALIASES`를 별도로 둔다.

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_external_compat.py` 끝에 추가:

```python
import pytest
from fastapi import HTTPException

from app.routers.external_compat import (
    _resolve_upload_source_type,
    _safe_upload_filename,
    _validate_upload_extension,
)


def test_resolve_upload_source_type_aliases():
    assert _resolve_upload_source_type("support_history") == "support_history"
    assert _resolve_upload_source_type("support") == "support_history"
    assert _resolve_upload_source_type("incident_reports") == "support_history"
    assert _resolve_upload_source_type("incident") == "support_history"
    assert _resolve_upload_source_type("tech_repo") == "tech_repo"
    assert _resolve_upload_source_type("confluence_docs") == "tech_repo"
    assert _resolve_upload_source_type("confluence") == "tech_repo"
    assert _resolve_upload_source_type("techrepo") == "tech_repo"
    assert _resolve_upload_source_type("tech-repo") == "tech_repo"
    assert _resolve_upload_source_type("tuning_ai") == "tuning_ai"
    assert _resolve_upload_source_type("sql_tuning") == "tuning_ai"
    assert _resolve_upload_source_type("sql") == "tuning_ai"
    assert _resolve_upload_source_type("issue_analysis") == "tuning_ai"
    assert _resolve_upload_source_type("dbms_tuning") == "tuning_ai"
    assert _resolve_upload_source_type("dbms-tuning") == "tuning_ai"
    assert _resolve_upload_source_type("tuning-ai") == "tuning_ai"


def test_resolve_upload_source_type_default_is_support_history():
    assert _resolve_upload_source_type("") == "support_history"


def test_resolve_upload_source_type_unknown_is_400():
    with pytest.raises(HTTPException) as exc:
        _resolve_upload_source_type("totally-bogus-type")
    assert exc.value.status_code == 400


def test_resolve_upload_source_type_known_unimplemented_is_501():
    with pytest.raises(HTTPException) as exc:
        _resolve_upload_source_type("vendor_docs")
    assert exc.value.status_code == 501

    with pytest.raises(HTTPException) as exc:
        _resolve_upload_source_type("checkitems")
    assert exc.value.status_code == 501


def test_safe_upload_filename_strips_path_traversal():
    assert _safe_upload_filename("../../etc/passwd") == "passwd"
    assert _safe_upload_filename("a/b/c.md") == "c.md"
    assert _safe_upload_filename("CITECTS-1234.md") == "CITECTS-1234.md"


def test_safe_upload_filename_rejects_empty():
    with pytest.raises(HTTPException) as exc:
        _safe_upload_filename("")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        _safe_upload_filename(None)
    assert exc.value.status_code == 400


def test_validate_upload_extension_accepts_md_txt():
    _validate_upload_extension("foo.md")
    _validate_upload_extension("foo.txt")


def test_validate_upload_extension_xls_is_501():
    with pytest.raises(HTTPException) as exc:
        _validate_upload_extension("checkitems.xlsx")
    assert exc.value.status_code == 501


def test_validate_upload_extension_unknown_is_400():
    with pytest.raises(HTTPException) as exc:
        _validate_upload_extension("foo.pdf")
    assert exc.value.status_code == 400
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_external_compat.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_upload_source_type' from 'app.routers.external_compat'`

- [ ] **Step 3: `external_compat.py`에 검증 헬퍼 추가**

파일 상단 import 블록(`apps/api/app/routers/external_compat.py:1-30`)을 아래로 교체:

```python
"""wiki-qa compatible external integration API surface.

Exposes `/api/*` paths that mirror citec-wiki-qa endpoints used by MCP and
other external systems, mapping onto citec-kb hybrid search / RAG / insights.

Native citec-kb APIs remain under `/v1/*`. Prefer `/v1/*` for new integrations;
use `/api/*` when migrating clients that already call wiki-qa.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import __version__
from app.audit.log import list_recent_queries, log_query_answer
from app.db.models import Document, Feedback, IngestJob, Insight
from app.db.session import session_scope
from app.doc_access import attach_document_access, document_access
from app.ingest.adapters import (
    parse_support_history_file,
    parse_tech_repo_file,
    parse_tuning_ai_file,
)
from app.ingest.pipeline import upsert_document_from_draft
from app.rag.pipeline import run_fast_rag, stream_rag
from app.retrieval.multi_query import multi_hybrid_search
from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search
from app.settings import get_settings

router = APIRouter(tags=["external-compat (wiki-qa)"])
```

파일에서 `_TEMPLATE_LABELS = {...}` 정의 바로 뒤(기존 라인 61 부근, `_map_section` 함수 시작
직전)에 아래 블록을 추가:

```python
# ── Upload (wiki-qa POST /api/upload compat) ────────────────────────

# wiki-qa upload alias table (README "별칭" 절) → citec-kb internal source_type.
# NOTE: distinct from _SECTION_MAP above, which is for *search* section names.
_UPLOAD_ALIASES: dict[str, str] = {
    "support_history": "support_history",
    "support": "support_history",
    "incident_reports": "support_history",
    "incident": "support_history",
    "tech_repo": "tech_repo",
    "confluence_docs": "tech_repo",
    "confluence": "tech_repo",
    "techrepo": "tech_repo",
    "tech-repo": "tech_repo",
    "tuning_ai": "tuning_ai",
    "sql_tuning": "tuning_ai",
    "sql": "tuning_ai",
    "issue_analysis": "tuning_ai",
    "dbms_tuning": "tuning_ai",
    "dbms-tuning": "tuning_ai",
    "tuning-ai": "tuning_ai",
}

# Known wiki-qa values with no native citec-kb parser yet — reject with 501,
# not a generic 400, so callers can tell "typo" apart from "not built yet".
_UPLOAD_NOT_IMPLEMENTED = {"vendor_docs", "vendor", "checkitems"}

_UPLOAD_PARSERS = {
    "support_history": parse_support_history_file,
    "tech_repo": parse_tech_repo_file,
    "tuning_ai": parse_tuning_ai_file,
}

_UPLOAD_RAW_SUBDIR = {
    "support_history": "support_history",
    "tech_repo": "tech_repo",
    "tuning_ai": "tuning_ai",
}

_UPLOAD_ALLOWED_EXT = {".md", ".txt"}


def _resolve_upload_source_type(source_type: str) -> str:
    key = (source_type or "").strip().lower() or "support_history"
    if key in _UPLOAD_NOT_IMPLEMENTED:
        raise HTTPException(
            status_code=501,
            detail=(
                f"source_type '{key}' 은(는) 이 호환 엔드포인트에서 아직 지원하지 않습니다."
            ),
        )
    internal = _UPLOAD_ALIASES.get(key)
    if not internal:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 source_type: {source_type}")
    return internal


def _safe_upload_filename(filename: str | None) -> str:
    name = Path((filename or "").strip()).name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="파일명이 유효하지 않습니다.")
    return name


def _validate_upload_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext in {".xls", ".xlsx"}:
        raise HTTPException(
            status_code=501,
            detail="checkitems(XLS) 업로드는 이 호환 엔드포인트에서 아직 지원하지 않습니다.",
        )
    if ext not in _UPLOAD_ALLOWED_EXT:
        raise HTTPException(
            status_code=400, detail=f"지원하지 않는 파일 형식: {ext or '(확장자 없음)'}"
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_external_compat.py -v`
Expected: 전체 통과 (기존 4개 + 신규 9개)

- [ ] **Step 5: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/routers/external_compat.py apps/api/tests/test_external_compat.py
git commit -m "feat(external-compat): add /api/upload validation helpers (source_type alias, ext, filename)"
```

---

## Task 3: `POST /api/upload` + 백그라운드 ingest 실행기

**Files:**
- Modify: `apps/api/app/routers/external_compat.py` (Task 2에서 추가한 검증 블록 뒤에 이어서 추가)

이 태스크는 DB에 쓰는 엔드포인트라 자동 단위 테스트를 붙이지 않는다(위 "테스트 방침" 참고).
Task 8에서 실제 서버로 수동 검증한다.

- [ ] **Step 1: 백그라운드 ingest 실행기 + 엔드포인트 구현**

Task 2에서 추가한 검증 헬퍼 블록 바로 뒤에 이어서 추가:

```python
def _run_upload_ingest_job(job_id: str, internal_type: str, path: Path) -> None:
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if job:
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)

    parser = _UPLOAD_PARSERS[internal_type]
    try:
        draft = parser(path)
        result = upsert_document_from_draft(draft, source_id="api_upload")
        with session_scope() as session:
            job = session.get(IngestJob, job_id)
            if job:
                job.status = "success"
                job.finished_at = datetime.now(timezone.utc)
                job.stats = {**(job.stats or {}), **result}
    except Exception as exc:  # noqa: BLE001
        with session_scope() as session:
            job = session.get(IngestJob, job_id)
            if job:
                job.status = "failed"
                job.finished_at = datetime.now(timezone.utc)
                job.error = str(exc)


def _enqueue_upload(
    background: BackgroundTasks, file: UploadFile, source_type: str
) -> dict[str, Any]:
    internal_type = _resolve_upload_source_type(source_type)
    filename = _safe_upload_filename(file.filename)
    _validate_upload_extension(filename)

    settings = get_settings()
    raw_dir = Path(settings.raw_dir) / _UPLOAD_RAW_SUBDIR[internal_type]
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / filename
    dest.write_bytes(file.file.read())

    job_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(
            IngestJob(
                id=job_id,
                source_id=None,
                mode="upload",
                status="pending",
                stats={"filename": filename, "source_type": internal_type},
            )
        )

    background.add_task(_run_upload_ingest_job, job_id, internal_type, dest)
    return {"job_id": job_id, "filename": filename, "status": "queued"}


@router.post("/api/upload")
def api_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    source_type: str = Form(default="support_history"),
) -> dict[str, Any]:
    """wiki-qa `POST /api/upload` compat — single file, queued background ingest."""
    return _enqueue_upload(background, file, source_type)
```

- [ ] **Step 2: 임포트 확인 (구문 오류만 우선 배제)**

Run: `cd apps/api && PYTHONPATH=. python -c "import app.routers.external_compat"`
Expected: 에러 없이 종료 (0)

- [ ] **Step 3: 기존 단위 테스트 회귀 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py`
Expected: 전체 통과 (Task 1/2 테스트 포함, 새 실패 없음)

- [ ] **Step 4: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/routers/external_compat.py
git commit -m "feat(external-compat): add POST /api/upload (wiki-qa compat, background ingest)"
```

---

## Task 4: `GET /api/ingest-status/{job_id}` (SSE)

**Files:**
- Modify: `apps/api/app/routers/external_compat.py`

- [ ] **Step 1: SSE 상태 엔드포인트 구현**

`api_upload` 함수 바로 뒤에 추가:

```python
@router.get("/api/ingest-status/{job_id}")
def api_ingest_status(job_id: str) -> StreamingResponse:
    """wiki-qa `GET /api/ingest-status/{job_id}` compat — SSE progress."""
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job를 찾을 수 없습니다.")

    def event_gen():
        for _ in range(120):
            with session_scope() as session:
                job = session.get(IngestJob, job_id)
                status = job.status if job else "failed"
                error = job.error if job else "job not found"
                stats = dict(job.stats or {}) if job else {}
            if status == "success":
                yield _sse({"type": "done", "status": "done", **stats})
                return
            if status == "failed":
                yield _sse({"type": "error", "text": error or "ingest failed", "error": error})
                return
            yield _sse({"type": "log", "text": f"📥 ingest 진행 중… ({status})"})
            time.sleep(1)
        yield _sse({"type": "log", "text": "⏳ 상태 확인 시간 초과 — 다시 폴링하세요."})

    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

`_sse` 헬퍼는 이미 파일 하단에 정의되어 있으므로(`def _sse(obj: dict[str, Any]) -> str:`) 재사용한다.

- [ ] **Step 2: 임포트 확인**

Run: `cd apps/api && PYTHONPATH=. python -c "import app.routers.external_compat"`
Expected: 에러 없이 종료 (0)

- [ ] **Step 3: 기존 단위 테스트 회귀 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py`
Expected: 전체 통과

- [ ] **Step 4: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/routers/external_compat.py
git commit -m "feat(external-compat): add GET /api/ingest-status/{job_id} SSE (wiki-qa compat)"
```

---

## Task 5: `POST /api/upload-multiple`

**Files:**
- Modify: `apps/api/app/routers/external_compat.py`

- [ ] **Step 1: 다중 업로드 엔드포인트 구현**

`api_ingest_status` 함수 바로 뒤에 추가:

```python
@router.post("/api/upload-multiple")
def api_upload_multiple(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    source_type: str = Form(default="support_history"),
) -> dict[str, Any]:
    """wiki-qa `POST /api/upload-multiple` compat — same source_type for all files."""
    jobs: list[dict[str, Any]] = []
    for f in files:
        try:
            jobs.append(_enqueue_upload(background, f, source_type))
        except HTTPException as exc:
            jobs.append(
                {
                    "filename": f.filename,
                    "status": "rejected",
                    "error": exc.detail,
                }
            )
    return {"jobs": jobs}
```

- [ ] **Step 2: 임포트 확인**

Run: `cd apps/api && PYTHONPATH=. python -c "import app.routers.external_compat"`
Expected: 에러 없이 종료 (0)

- [ ] **Step 3: 기존 단위 테스트 회귀 확인**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py`
Expected: 전체 통과

- [ ] **Step 4: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/routers/external_compat.py
git commit -m "feat(external-compat): add POST /api/upload-multiple (wiki-qa compat)"
```

---

## Task 6: `/v1/external/catalog`에 업로드 엔드포인트 등록

**Files:**
- Modify: `apps/api/app/routers/external_compat.py:667-704` (`v1_ext_catalog`)

- [ ] **Step 1: catalog 딕셔너리 갱신**

`v1_ext_catalog` 함수의 `"wiki_qa_compat"` 딕셔너리에서 다음 줄:

```python
            "GET /api/recent-questions": "recent audited queries",
```

을 아래로 교체:

```python
            "GET /api/recent-questions": "recent audited queries",
            "POST /api/upload": (
                "single-file upload (support_history/tech_repo/tuning_ai + aliases) "
                "→ background ingest, returns job_id"
            ),
            "GET /api/ingest-status/{job_id}": "SSE ingest progress for /api/upload job",
            "POST /api/upload-multiple": "multi-file upload, field 'files', shared source_type",
```

- [ ] **Step 2: 임포트 확인**

Run: `cd apps/api && PYTHONPATH=. python -c "import app.routers.external_compat"`
Expected: 에러 없이 종료 (0)

- [ ] **Step 3: Commit**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/app/routers/external_compat.py
git commit -m "docs(external-compat): list upload endpoints in /v1/external/catalog"
```

---

## Task 7: `docs/EXTERNAL_API.md` 갱신

**Files:**
- Modify: `docs/EXTERNAL_API.md`

- [ ] **Step 1: "검색 · 문서" 절과 "Q&A (SSE)" 절 사이에 "업로드" 절 삽입**

`docs/EXTERNAL_API.md`에서 `### Q&A (SSE) — MCP \`wiki_ask\`` 줄(현재 95번째 줄) 바로 앞에
아래 절을 삽입:

```markdown
### 업로드 — wiki-qa `POST /api/upload` 호환

기술지원이력 / 테크리포 / DBMS튜닝 문서를 외부 시스템이 파일로 업로드하는 경로입니다.

| Method | Path | 설명 |
|--------|------|------|
| POST | `/api/upload` | 파일 1개 업로드 → `job_id` 즉시 반환, 백그라운드 ingest |
| GET | `/api/ingest-status/{job_id}` | SSE로 ingest 진행상황 추적 |
| POST | `/api/upload-multiple` | 파일 여러 개(`files` 필드), 동일 `source_type` 적용 |

**`source_type` 지원 값 / 별칭** (citec-wiki-qa README 별칭 표와 동일):

| 값/별칭 | citec-kb 내부 source_type |
|---------|---------------------------|
| `support_history`, `support` | `support_history` |
| `incident_reports`, `incident` | `support_history` |
| `tech_repo`, `confluence_docs`, `confluence`, `techrepo`, `tech-repo` | `tech_repo` |
| `tuning_ai`, `sql_tuning`, `sql`, `issue_analysis`, `dbms_tuning`, `dbms-tuning`, `tuning-ai` | `tuning_ai` |

허용 확장자: `.md`, `.txt`.

> **범위 제외 (이번 라운드)**: `vendor_docs`(`vendor`)와 `checkitems`(`.xls`/`.xlsx`)는 citec-kb에
> 대응 파서가 아직 없어 `501 Not Implemented`로 거부됩니다. 그 외 알 수 없는 `source_type` 값은
> 기존과 동일하게 `400 Bad Request`.

**요청 예시**

```bash
curl -X POST http://<host>/api/upload \
     -F "file=@CITECTS-1234.md" \
     -F "source_type=support_history"

curl -X POST http://<host>/api/upload \
     -F "file=@148554390_kernel_params.txt" \
     -F "source_type=tech_repo"

curl -X POST http://<host>/api/upload \
     -F "file=@ISS-5678.md" \
     -F "source_type=tuning_ai"
```

**응답**

```json
{ "job_id": "a1b2c3d4-...", "filename": "148554390_kernel_params.txt", "status": "queued" }
```

**진행상황 추적**

```bash
curl -N http://<host>/api/ingest-status/a1b2c3d4-...
# data: {"type":"log","text":"📥 ingest 진행 중… (running)"}
# data: {"type":"done","status":"done","document_id":"...","action":"inserted"}
```

```

- [ ] **Step 2: Commit**

```bash
cd /home/citec/dev/citec-kb
git add docs/EXTERNAL_API.md
git commit -m "docs: document POST /api/upload wiki-qa compat endpoints"
```

---

## Task 8: 실제 서버 기동 후 수동 스모크 검증

**Files:** 없음(검증만).

DB 필요 없이 도는 자동 테스트는 Task 1~2에서 이미 커밋됐다. 이 태스크는 Task 3~5에서 만든
실제 엔드포인트가 살아있는 Postgres에 대해 진짜로 동작하는지 확인한다.

- [ ] **Step 1: 스택 기동**

```bash
cd /home/citec/dev/citec-kb
docker compose up -d --build api
```

Expected: `api` 컨테이너가 healthy 상태로 뜬다 (`docker compose ps`).

- [ ] **Step 2: support_history 업로드 → 즉시 job_id 확인**

```bash
cat > /tmp/CITECTS-9999.md <<'EOF'
# CITECTS-9999 테스트 이슈

- **Issue Key**: CITECTS-9999
- **Status**: 닫힘
- **Component**: Redis

Redis 커넥션 타임아웃 관련 지원이력 본문입니다.
EOF

curl -s -X POST http://localhost:8573/api/upload \
     -F "file=@/tmp/CITECTS-9999.md" \
     -F "source_type=support" | tee /tmp/upload_resp.json
```

Expected: `{"job_id": "...", "filename": "CITECTS-9999.md", "status": "queued"}`
(`source_type=support` 별칭이 `support_history`로 정규화되어 처리됨을 확인)

- [ ] **Step 3: SSE로 완료까지 추적**

```bash
JOB_ID=$(python3 -c "import json;print(json.load(open('/tmp/upload_resp.json'))['job_id'])")
curl -N --max-time 15 "http://localhost:8573/api/ingest-status/$JOB_ID"
```

Expected: `log` 이벤트 0~n개 후 `data: {"type":"done","status":"done","document_id":"...", ...}`

- [ ] **Step 4: 문서가 검색에 실제로 반영됐는지 확인**

```bash
curl -s 'http://localhost:8573/api/wiki/search?q=Redis+커넥션+타임아웃&section=support_history&limit=3' \
  | jq '.results[].external_id'
```

Expected: 결과 목록에 `"CITECTS-9999"` 포함

- [ ] **Step 5: 미지원 타입 501, 알 수 없는 타입 400 확인**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8573/api/upload \
     -F "file=@/tmp/CITECTS-9999.md" -F "source_type=vendor_docs"
# expected: 501

curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8573/api/upload \
     -F "file=@/tmp/CITECTS-9999.md" -F "source_type=totally-bogus"
# expected: 400
```

- [ ] **Step 6: `raw_dir`에 실제 파일이 저장됐는지 확인**

```bash
docker compose exec api ls -la /data/raw/support_history/ | grep CITECTS-9999
```

Expected: `CITECTS-9999.md` 존재

이 태스크에는 커밋할 코드 변경이 없다(검증 전용). 문제가 발견되면 해당 Task로 돌아가 수정 후
재검증한다.

---

## Self-Review 결과

- **스펙 커버리지**: 설계 문서(`2026-07-27-api-upload-compat-design.md`)의 3개 엔드포인트,
  별칭 표, 501/400 구분, raw 저장, 멱등(content_hash), 문서 갱신, 순수 단위 테스트 범위 —
  모두 Task 1~8에 반영됨. `IngestJob` 재사용(Redis 큐 미사용)도 반영됨.
- **플레이스홀더 스캔**: 없음 — 모든 스텝에 실행 가능한 전체 코드/명령 포함.
- **타입/시그니처 일관성**: `parse_support_history_file`/`parse_tech_repo_file`/`parse_tuning_ai_file`
  이름이 Task 1(정의)·Task 2(`_UPLOAD_PARSERS` 참조)·Task 3(import)에서 동일하게 사용됨.
  `_resolve_upload_source_type`/`_safe_upload_filename`/`_validate_upload_extension`도
  Task 2(정의)·Task 3(`_enqueue_upload`에서 사용)에서 시그니처 일치.
