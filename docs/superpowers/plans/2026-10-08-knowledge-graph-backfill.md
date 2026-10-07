# citec-kb 지식그래프 백필 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs/2026-10-07-knowledge-graph-design.md`에 정의된 1단계
범위(스키마 + 백필 파이프라인)를 구현한다 — Postgres를 source of truth로 유지하고,
Neo4j를 파생 저장소로 추가해, 배치 스크립트 하나가 멱등하게 그래프를 채운다.

**Architecture:** 새 `apps/api/app/graph/` 패키지 — 순수 함수 추출기(`extract.py`,
`failure_bucket.py`) + 해시 기반 변경감지(`hashing.py`, `sync_state.py`) + Neo4j
클라이언트(`neo4j_client.py`) + 오케스트레이션(`pipeline.py`) + CLI(`sync_cli.py`).
기존 `scripts/map_backfill.sh` 패턴을 그대로 따라 `scripts/graph_sync.sh`가
`docker compose exec api python -m app.graph.sync_cli`를 감싼다.

**Tech Stack:** Python 3.12, SQLAlchemy 2(기존 ORM 그대로), Neo4j 5.x + 공식
`neo4j` Python 드라이버, pytest.

**스펙 범위 밖(이 플랜에도 없음):** 실시간 조회 API/MCP 도구, failure_bucket
플라이휠 훅 연동, 티켓-티켓 `SIMILAR_TO`, LLM 보강, `is_hub` 플래그 — 전부
스펙 §6에 2단계로 명시돼 있다.

---

## 파일 구조

| 파일 | 역할 |
|---|---|
| `docker-compose.yml` (수정) | `neo4j` 서비스 추가 |
| `apps/api/requirements.txt` (수정) | `neo4j` 드라이버 추가 |
| `apps/api/app/settings.py` (수정) | `neo4j_uri`/`neo4j_username`/`neo4j_password` 설정 |
| `apps/api/app/db/models.py` (수정) | `GraphSyncState` ORM 모델 추가 |
| `apps/api/alembic/versions/20261008_0010_graph_sync_state.py` (신규) | 마이그레이션 |
| `apps/api/app/graph/__init__.py` (신규) | 패키지 마커 |
| `apps/api/app/graph/hashing.py` (신규) | `compute_graph_hash()` — 변경감지용 해시 |
| `apps/api/app/graph/sync_state.py` (신규) | `graph_sync_state` CRUD |
| `apps/api/app/graph/extract.py` (신규) | Document용 순수 추출기 5개 |
| `apps/api/app/graph/failure_bucket.py` (신규) | FailureBucket용 추출기 2개(`HAS_EVIDENCE`, 중복매칭 메모) |
| `apps/api/app/graph/neo4j_client.py` (신규) | MERGE 전용 얇은 Neo4j 래퍼 + `Edge` dataclass |
| `apps/api/app/graph/pipeline.py` (신규) | 우선순위 그룹 순회 + 멱등 동기화 오케스트레이션 |
| `apps/api/app/graph/sync_cli.py` (신규) | `python -m app.graph.sync_cli` CLI |
| `scripts/graph_sync.sh` (신규) | `map_backfill.sh`와 동일한 컨테이너 실행 래퍼 |
| `apps/api/tests/test_graph_hashing.py` (신규) | |
| `apps/api/tests/test_graph_sync_state_db.py` (신규, DB opt-in) | |
| `apps/api/tests/test_graph_extract.py` (신규) | |
| `apps/api/tests/test_graph_failure_bucket.py` (신규) | |
| `apps/api/tests/test_graph_neo4j_client.py` (신규, Neo4j opt-in) | |
| `apps/api/tests/test_graph_pipeline_db.py` (신규, DB+Neo4j opt-in) | |

---

## Task 1: Neo4j 인프라 추가

**Files:**
- Modify: `docker-compose.yml`
- Modify: `apps/api/requirements.txt`
- Modify: `apps/api/app/settings.py`

- [ ] **Step 1: docker-compose.yml에 neo4j 서비스 추가**

`postgres:` 서비스 블록 바로 뒤에 추가 (스펙 §2 — 포트 8578/8579는 조직 할당
8572–8580 중 미사용분):

```yaml
  neo4j:
    image: neo4j:5.26-community
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-citecgraph}
      NEO4J_PLUGINS: '["graph-data-science"]'
    ports:
      - "8578:7474"
      - "8579:7687"
    volumes:
      - neo4jdata:/data
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
    restart: unless-stopped
```

파일 하단 `volumes:` 섹션(기존 `pgdata:` 옆)에 `neo4jdata:` 한 줄 추가.

- [ ] **Step 2: requirements.txt에 드라이버 추가**

`apps/api/requirements.txt`의 `pgvector==0.4.1` 줄 아래에 추가:

```
neo4j==5.26.0
```

- [ ] **Step 3: settings.py에 Neo4j 설정 추가**

`apps/api/app/settings.py`의 `confluence_base_url` 필드들 바로 위에 추가:

```python
    neo4j_uri: str = Field(default="bolt://localhost:8579", alias="NEO4J_URI")
    neo4j_username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field(default="citecgraph", alias="NEO4J_PASSWORD")
```

- [ ] **Step 4: 기동 확인**

```bash
docker compose up -d neo4j
docker compose exec -T api pip install -r requirements.txt  # 이미지 재빌드 전 임시 확인용이면 스킵하고 바로 build
docker compose build api
docker compose up -d api
docker compose exec -T api python -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('bolt://neo4j:7687', auth=('neo4j','citecgraph')); d.verify_connectivity(); print('OK')"
```

Expected: `OK` 출력. (컨테이너 내부 네트워크에서는 호스트명 `neo4j:7687`을 쓴다 —
`settings.neo4j_uri` 기본값은 호스트에서 테스트할 때 쓰는 `localhost:8579`이고,
컨테이너 안에서 실행되는 실제 파이프라인은 `.env`의 `NEO4J_URI=bolt://neo4j:7687`로
오버라이드해야 한다. `.env.example`에 두 줄 추가:
`NEO4J_URI=bolt://neo4j:7687`, `NEO4J_PASSWORD=citecgraph`.)

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml apps/api/requirements.txt apps/api/app/settings.py .env.example
git commit -m "feat(graph): add Neo4j service + driver + settings"
```

---

## Task 2: `graph_sync_state` 테이블

**Files:**
- Modify: `apps/api/app/db/models.py`
- Create: `apps/api/alembic/versions/20261008_0010_graph_sync_state.py`

- [ ] **Step 1: ORM 모델 추가**

`apps/api/app/db/models.py`의 `class Bundle` 선언 뒤(파일 맨 끝)에 추가:

```python
class GraphSyncState(Base):
    """app.graph 패키지가 쓰는 변경감지 테이블 — Neo4j 동기화 여부를 추적한다.

    document_id 1건당 1행. input_hash는 Document.content_hash와 다른 값이다
    (app.graph.hashing.compute_graph_hash 참고) — ancestor_ids 등 content_hash가
    일부러 빼는 필드를 포함해야 PARENT_OF 갱신을 감지할 수 있기 때문이다.
    """

    __tablename__ = "graph_sync_state"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text)
```

- [ ] **Step 2: 마이그레이션 생성**

`apps/api/alembic/versions/20261008_0010_graph_sync_state.py` 작성 (직전 리비전은
`20260930_0009`):

```python
"""add graph_sync_state (knowledge-graph backfill change detection)

Revision ID: 20261008_0010
Revises: 20260930_0009

Purely additive — no existing table touched. See
docs/superpowers/specs/2026-10-07-knowledge-graph-design.md §3.1.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0010"
down_revision: Union[str, None] = "20260930_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graph_sync_state",
        sa.Column("document_id", sa.String(length=64), primary_key=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("graph_extractor_version", sa.String(length=32), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("graph_sync_state")
```

- [ ] **Step 3: 마이그레이션 적용 + 확인**

```bash
docker compose exec -T api alembic upgrade head
docker compose exec -T postgres psql -U citec -d citec_knowledge -c "\d graph_sync_state"
```

Expected: 컬럼 5개(`document_id`, `input_hash`, `graph_extractor_version`,
`synced_at`, `last_error`)가 보여야 한다.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/db/models.py apps/api/alembic/versions/20261008_0010_graph_sync_state.py
git commit -m "feat(graph): add graph_sync_state table"
```

---

## Task 3: `sync_state.py` — 변경감지 CRUD

**Files:**
- Create: `apps/api/app/graph/__init__.py`
- Create: `apps/api/app/graph/sync_state.py`
- Test: `apps/api/tests/test_graph_sync_state_db.py`

- [ ] **Step 1: 패키지 마커**

```bash
mkdir -p apps/api/app/graph
touch apps/api/app/graph/__init__.py
```

- [ ] **Step 2: 실패하는 테스트 작성**

`apps/api/tests/test_graph_sync_state_db.py` (기존
`test_confluence_map_inventory_db.py`와 동일한 opt-in 스캐폴딩 — 라이브 DB 보호):

```python
"""DB-touching tests for app.graph.sync_state.

Skipped unless GRAPH_TEST_DATABASE_URL points at a reachable scratch
Postgres DB with the alembic schema applied — never the live citec_knowledge DB.
"""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set GRAPH_TEST_DATABASE_URL to a scratch Postgres DB to run these",
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


def _make_document_row(session):
    from app.db.models import Document

    doc = Document(
        id=str(uuid.uuid4()),
        source_type="tech_repo",
        external_id=str(uuid.uuid4()),
        title="test doc",
        body_md="hello",
        content_hash="abc123",
    )
    session.add(doc)
    session.flush()
    return doc.id


def test_mark_synced_then_get_state_roundtrips():
    from app.db.session import session_scope
    from app.graph.sync_state import get_state, mark_synced

    with session_scope() as session:
        doc_id = _make_document_row(session)

    mark_synced(doc_id, input_hash="h1", extractor_version="v1")
    state = get_state(doc_id)
    assert state is not None
    assert state["input_hash"] == "h1"
    assert state["graph_extractor_version"] == "v1"
    assert state["last_error"] is None


def test_mark_failed_sets_error_without_clearing_hash():
    from app.db.session import session_scope
    from app.graph.sync_state import get_state, mark_failed, mark_synced

    with session_scope() as session:
        doc_id = _make_document_row(session)

    mark_synced(doc_id, input_hash="h1", extractor_version="v1")
    mark_failed(doc_id, error="neo4j connection refused")
    state = get_state(doc_id)
    assert state["last_error"] == "neo4j connection refused"
    assert state["input_hash"] == "h1"  # 직전 성공 해시는 유지


def test_get_state_returns_none_for_unknown_document():
    from app.graph.sync_state import get_state

    assert get_state("does-not-exist") is None
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

```bash
GRAPH_TEST_DATABASE_URL="$DATABASE_URL" docker compose exec -T -e GRAPH_TEST_DATABASE_URL api \
  pytest apps/api/tests/test_graph_sync_state_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.graph.sync_state'`로 FAIL.
(주의: 위 `$DATABASE_URL`은 스크래치 DB를 가리켜야 한다 — 운영/개발 DB를 그대로
넣지 말 것. 로컬에 별도 postgres 컨테이너가 없다면 기존 `postgres` 서비스의
별도 스키마나 테스트용 DB를 하나 만들어 그 DSN을 넣는다.)

- [ ] **Step 4: 구현**

`apps/api/app/graph/sync_state.py`:

```python
"""CRUD for graph_sync_state — tracks whether a document's Neo4j edges are
up to date with its current Postgres data (app.graph.hashing.compute_graph_hash)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.db.models import GraphSyncState
from app.db.session import session_scope


def get_state(document_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as session:
        row = session.get(GraphSyncState, document_id)
        if row is None:
            return None
        return {
            "document_id": row.document_id,
            "input_hash": row.input_hash,
            "graph_extractor_version": row.graph_extractor_version,
            "synced_at": row.synced_at,
            "last_error": row.last_error,
        }


def mark_synced(document_id: str, *, input_hash: str, extractor_version: str) -> None:
    with session_scope() as session:
        row = session.get(GraphSyncState, document_id)
        if row is None:
            row = GraphSyncState(document_id=document_id)
            session.add(row)
        row.input_hash = input_hash
        row.graph_extractor_version = extractor_version
        row.synced_at = datetime.now(timezone.utc)
        row.last_error = None


def mark_failed(document_id: str, *, error: str) -> None:
    with session_scope() as session:
        row = session.get(GraphSyncState, document_id)
        if row is None:
            row = GraphSyncState(document_id=document_id)
            session.add(row)
        row.last_error = error[:2000]
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
GRAPH_TEST_DATABASE_URL="$DATABASE_URL" docker compose exec -T -e GRAPH_TEST_DATABASE_URL api \
  pytest apps/api/tests/test_graph_sync_state_db.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/graph/__init__.py apps/api/app/graph/sync_state.py apps/api/tests/test_graph_sync_state_db.py
git commit -m "feat(graph): add graph_sync_state CRUD"
```

---

## Task 4: `hashing.py` — 변경감지 해시

**Files:**
- Create: `apps/api/app/graph/hashing.py`
- Test: `apps/api/tests/test_graph_hashing.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from app.graph.hashing import compute_graph_hash


def test_hash_changes_when_ancestor_ids_change():
    h1 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1", "2"]})
    h2 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1", "2", "3"]})
    assert h1 != h2


def test_hash_stable_for_identical_input():
    h1 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1"]})
    h2 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1"]})
    assert h1 == h2


def test_hash_changes_when_content_hash_changes_but_extra_same():
    h1 = compute_graph_hash("content-hash-1", {"components": ["Redis"]})
    h2 = compute_graph_hash("content-hash-2", {"components": ["Redis"]})
    assert h1 != h2


def test_hash_ignores_extra_key_ordering():
    h1 = compute_graph_hash("c", {"a": 1, "b": 2})
    h2 = compute_graph_hash("c", {"b": 2, "a": 1})
    assert h1 == h2
```

- [ ] **Step 2: 실패 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_hashing.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.graph.hashing'`

- [ ] **Step 3: 구현**

```python
"""Change-detection hash for app.graph.sync_state.

Deliberately NOT Document.content_hash: that hash excludes ancestor_ids on
purpose (apps/api/app/ingest/adapters.py:43-47, to avoid re-chunking on a
page move), but PARENT_OF must react to ancestor_ids changes — so this hash
includes content_hash AND whatever structured fields (ancestor_ids,
components, evidence_ref, area/category*) the caller passes in `extra`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_graph_hash(content_hash: str, extra: dict[str, Any]) -> str:
    payload = {"content_hash": content_hash, "extra": extra}
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_hashing.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/hashing.py apps/api/tests/test_graph_hashing.py
git commit -m "feat(graph): add compute_graph_hash"
```

---

## Task 5: `extract.py` — `extract_hierarchy`

**Files:**
- Create: `apps/api/app/graph/extract.py`
- Test: `apps/api/tests/test_graph_extract.py`

**Edge 표현**: 모든 추출기가 공유하는 작은 dataclass. 이 태스크에서 함께 정의한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_graph_extract.py`:

```python
from app.graph.extract import Edge, extract_hierarchy


def test_extract_hierarchy_returns_parent_of_edges_in_order():
    doc = {"id": "d1", "metadata": {"ancestor_ids": ["root", "mid", "d1"]}}
    edges = extract_hierarchy(doc)
    assert edges == [
        Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
             target_value="root", tag="EXTRACTED"),
        Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
             target_value="mid", tag="EXTRACTED"),
    ]


def test_extract_hierarchy_returns_empty_when_no_ancestor_ids():
    doc = {"id": "d1", "metadata": {}}
    assert extract_hierarchy(doc) == []


def test_extract_hierarchy_excludes_self_from_ancestor_chain():
    # adapters.py appends the page's own id as the last element of ancestor_ids
    doc = {"id": "d1", "metadata": {"ancestor_ids": ["d1"]}}
    assert extract_hierarchy(doc) == []
```

- [ ] **Step 2: 실패 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_extract.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.graph.extract'`

- [ ] **Step 3: 구현**

```python
"""Pure extractor functions: Document dict in -> list[Edge] out.

No DB, no Neo4j connection here — app.graph.pipeline supplies whatever
lookups each extractor needs (lexicon map, external_id index, …) as plain
arguments, so every function here stays independently unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    rel_type: str
    target_label: str
    target_key: str
    target_value: str
    tag: str  # "EXTRACTED" | "INFERRED"


def extract_hierarchy(doc: dict) -> list[Edge]:
    """metadata["ancestor_ids"] -> PARENT_OF(ancestor -> doc) for each ancestor.

    adapters.py builds ancestor_ids as [root, ..., parent, self] (see
    apps/api/app/confluence/map_sync.py build_frontmatter_confluence_map and
    apps/api/app/ingest/adapters.py:233-237/294-298/351-355) — the last
    element is the page's own id, which this function drops.
    """
    ancestor_ids = (doc.get("metadata") or {}).get("ancestor_ids") or []
    chain = [a for a in ancestor_ids if a != doc["id"]]
    return [
        Edge(
            rel_type="PARENT_OF",
            target_label="Document",
            target_key="id",
            target_value=ancestor_id,
            tag="EXTRACTED",
        )
        for ancestor_id in chain
    ]
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_extract.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/extract.py apps/api/tests/test_graph_extract.py
git commit -m "feat(graph): add extract_hierarchy (PARENT_OF)"
```

---

## Task 6: `extract.py` — `extract_structured_components`

**Files:**
- Modify: `apps/api/app/graph/extract.py`
- Modify: `apps/api/tests/test_graph_extract.py`

**스펙 근거**: §3.3 `HAS_COMPONENT`(EXTRACTED) — `issue_frames.components[]` 또는
`checkitems.area/category*`.

- [ ] **Step 1: 테스트 추가**

```python
from app.graph.extract import extract_structured_components


def test_extract_structured_components_from_issue_frame():
    doc = {"id": "d1", "source_type": "incident_reports"}
    issue_frame = {"components": ["Redis", "Network"]}
    edges = extract_structured_components(doc, issue_frame=issue_frame, checkitem=None)
    assert edges == [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="Redis", tag="EXTRACTED"),
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="Network", tag="EXTRACTED"),
    ]


def test_extract_structured_components_from_checkitem_area():
    doc = {"id": "d2", "source_type": "checkitem"}
    checkitem = {"area": "3PAR", "category_1": "구성"}
    edges = extract_structured_components(doc, issue_frame=None, checkitem=checkitem)
    assert edges == [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="3PAR", tag="EXTRACTED"),
    ]
    # category_1("구성"/"Configuration" 등 5개 PISA 평가축)은 컴포넌트가 아니므로 안 나온다


def test_extract_structured_components_returns_empty_when_no_frame_or_checkitem():
    doc = {"id": "d3", "source_type": "tech_repo"}
    assert extract_structured_components(doc, issue_frame=None, checkitem=None) == []


def test_extract_structured_components_dedupes_empty_strings():
    doc = {"id": "d4", "source_type": "incident_reports"}
    issue_frame = {"components": ["Redis", "", "Redis"]}
    edges = extract_structured_components(doc, issue_frame=issue_frame, checkitem=None)
    assert [e.target_value for e in edges] == ["Redis"]
```

(이 import 블록은 파일 맨 위 `from app.graph.extract import Edge, extract_hierarchy`
줄을 `from app.graph.extract import Edge, extract_hierarchy, extract_structured_components`로
바꿔서 합친다.)

- [ ] **Step 2: 실패 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_extract.py -v
```

Expected: 새 4개 테스트가 `ImportError`로 FAIL.

- [ ] **Step 3: 구현** (`extract.py` 맨 아래에 추가)

```python
def extract_structured_components(
    doc: dict, *, issue_frame: dict | None, checkitem: dict | None
) -> list[Edge]:
    """issue_frames.components[] 또는 checkitems.area 중 해당하는 쪽만 본다.
    checkitems.category/category_1/subcategory는 PISA 평가축(구성/운영/가용성/
    결함 및 오류/성능 및 용량)이라 컴포넌트가 아니다 — area만 쓴다."""
    values: list[str] = []
    if issue_frame:
        values.extend(issue_frame.get("components") or [])
    if checkitem and checkitem.get("area"):
        values.append(checkitem["area"])

    seen: set[str] = set()
    edges: list[Edge] = []
    for v in values:
        v = (v or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        edges.append(
            Edge(
                rel_type="HAS_COMPONENT",
                target_label="Component",
                target_key="canonical_name",
                target_value=v,
                tag="EXTRACTED",
            )
        )
    return edges
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_extract.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/extract.py apps/api/tests/test_graph_extract.py
git commit -m "feat(graph): add extract_structured_components (HAS_COMPONENT)"
```

---

## Task 7: `extract.py` — `extract_business_entities`

**Files:**
- Modify: `apps/api/app/graph/extract.py`
- Modify: `apps/api/tests/test_graph_extract.py`

**스펙 근거**: §3.3 `MENTIONS_ENTITY`(EXTRACTED) — `document_entities` 미러.
`entities.type`이 `business_system`/`platform`인 행만 BusinessEntity로 라우팅한다
(§3.2 — `component`/`tech_term`은 Task 8에서 Component로 간다).

- [ ] **Step 1: 테스트 추가**

```python
from app.graph.extract import extract_business_entities


def test_extract_business_entities_includes_business_and_platform_only():
    doc = {"id": "d1"}
    document_entities = [
        {"entity_id": "sys:monimo", "entity_type": "business_system"},
        {"entity_id": "sys:scp", "entity_type": "platform"},
        {"entity_id": "sys:redis", "entity_type": "component"},
    ]
    edges = extract_business_entities(doc, document_entities=document_entities)
    assert edges == [
        Edge(rel_type="MENTIONS_ENTITY", target_label="BusinessEntity", target_key="id",
             target_value="sys:monimo", tag="EXTRACTED"),
        Edge(rel_type="MENTIONS_ENTITY", target_label="BusinessEntity", target_key="id",
             target_value="sys:scp", tag="EXTRACTED"),
    ]


def test_extract_business_entities_empty_when_no_rows():
    assert extract_business_entities({"id": "d1"}, document_entities=[]) == []
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

```python
_BUSINESS_ENTITY_TYPES = {"business_system", "platform"}


def extract_business_entities(doc: dict, *, document_entities: list[dict]) -> list[Edge]:
    """document_entities 미러. entities.type이 business_system/platform인 행만
    BusinessEntity로 — component/tech_term은 Component 쪽(Task 8)에서 다룬다."""
    edges: list[Edge] = []
    for row in document_entities:
        if row.get("entity_type") not in _BUSINESS_ENTITY_TYPES:
            continue
        edges.append(
            Edge(
                rel_type="MENTIONS_ENTITY",
                target_label="BusinessEntity",
                target_key="id",
                target_value=row["entity_id"],
                tag="EXTRACTED",
            )
        )
    return edges
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_extract.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/extract.py apps/api/tests/test_graph_extract.py
git commit -m "feat(graph): add extract_business_entities (MENTIONS_ENTITY)"
```

---

## Task 8: `extract.py` — `extract_lexicon_components`

**Files:**
- Modify: `apps/api/app/graph/extract.py`
- Modify: `apps/api/tests/test_graph_extract.py`

**스펙 근거**: §3.3 `HAS_COMPONENT`(INFERRED) — 본문 + `lexicon_terms` 매칭.
`app.lexicon.seed.load_lexicon_map()`이 이미 "토큰(소문자) → [canonical, 변형들]"
맵을 만들어준다 — 그걸 그대로 입력으로 받는다(새 사전 로직을 또 만들지 않음).

- [ ] **Step 1: 테스트 추가**

```python
from app.graph.extract import extract_lexicon_components


def test_extract_lexicon_components_matches_canonical_and_variant():
    doc = {"id": "d1", "body_md": "Redis timeout 발생, 레디스 재기동함"}
    lexicon_map = {
        "redis": ["Redis", "레디스", "redis"],
        "레디스": ["Redis", "레디스", "redis"],
        "timeout": ["timeout", "타임아웃", "time-out", "timed out"],
    }
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    canonicals = sorted({e.target_value for e in edges})
    assert canonicals == ["Redis", "timeout"]
    assert all(e.tag == "INFERRED" for e in edges)


def test_extract_lexicon_components_empty_body_returns_empty():
    doc = {"id": "d1", "body_md": ""}
    assert extract_lexicon_components(doc, lexicon_map={"redis": ["Redis"]}) == []


def test_extract_lexicon_components_no_match_returns_empty():
    doc = {"id": "d1", "body_md": "전혀 관련 없는 본문"}
    assert extract_lexicon_components(doc, lexicon_map={"redis": ["Redis"]}) == []
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

```python
import re

_TOKEN_RE = re.compile(r"[A-Za-z가-힣0-9_/\-\.]+")


def extract_lexicon_components(doc: dict, *, lexicon_map: dict[str, list[str]]) -> list[Edge]:
    """app.lexicon.seed.load_lexicon_map()의 출력(토큰 소문자 -> [canonical,...])을
    그대로 입력받아 본문에서 매칭된 canonical들을 HAS_COMPONENT(INFERRED)로 낸다."""
    body = doc.get("body_md") or ""
    if not body:
        return []
    tokens = {t.lower() for t in _TOKEN_RE.findall(body)}
    canonicals: set[str] = set()
    for tok in tokens:
        variants = lexicon_map.get(tok)
        if variants:
            canonicals.add(variants[0])  # load_lexicon_map()의 variants[0] == canonical
    return [
        Edge(
            rel_type="HAS_COMPONENT",
            target_label="Component",
            target_key="canonical_name",
            target_value=c,
            tag="INFERRED",
        )
        for c in sorted(canonicals)
    ]
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_extract.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/extract.py apps/api/tests/test_graph_extract.py
git commit -m "feat(graph): add extract_lexicon_components (HAS_COMPONENT INFERRED)"
```

---

## Task 9: `extract.py` — `extract_references`

**Files:**
- Modify: `apps/api/app/graph/extract.py`
- Modify: `apps/api/tests/test_graph_extract.py`

**스펙 근거**: §3.3 `REFERENCES`(EXTRACTED) — 본문 내 `CITECTS-\d+` 패턴.
타겟은 `(source_type, external_id)` 쌍으로 찾아야 하므로, pipeline.py가 만들어
넘기는 `external_id_index: dict[str, str]`(= `f"citects-{번호}" -> document_id`,
이미 소문자 정규화)을 받는다.

- [ ] **Step 1: 테스트 추가**

```python
from app.graph.extract import extract_references


def test_extract_references_finds_ticket_id_and_resolves_via_index():
    doc = {"id": "d1", "body_md": "CITECTS-1234 사례와 유사함. citects-5678도 참고"}
    index = {"citects-1234": "doc-a", "citects-5678": "doc-b"}
    edges = extract_references(doc, external_id_index=index)
    assert sorted(e.target_value for e in edges) == ["doc-a", "doc-b"]
    assert all(e.rel_type == "REFERENCES" and e.target_label == "Document" for e in edges)


def test_extract_references_skips_unresolved_ids():
    doc = {"id": "d1", "body_md": "CITECTS-9999 참고"}
    edges = extract_references(doc, external_id_index={})
    assert edges == []


def test_extract_references_empty_body_returns_empty():
    doc = {"id": "d1", "body_md": ""}
    assert extract_references(doc, external_id_index={"citects-1": "x"}) == []
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

```python
_TICKET_ID_RE = re.compile(r"CITECTS-\d+", re.IGNORECASE)


def extract_references(doc: dict, *, external_id_index: dict[str, str]) -> list[Edge]:
    """본문 내 CITECTS-#### 언급을 external_id_index로 해석해 REFERENCES로.
    해석 안 되는 ID(코퍼스 밖이거나 아직 동기화 안 됨)는 조용히 건너뛴다."""
    body = doc.get("body_md") or ""
    if not body:
        return []
    found = {m.group(0).lower() for m in _TICKET_ID_RE.finditer(body)}
    target_ids = sorted({external_id_index[f] for f in found if f in external_id_index})
    return [
        Edge(
            rel_type="REFERENCES",
            target_label="Document",
            target_key="id",
            target_value=t,
            tag="EXTRACTED",
        )
        for t in target_ids
    ]
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_extract.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/extract.py apps/api/tests/test_graph_extract.py
git commit -m "feat(graph): add extract_references"
```

---

## Task 10: `failure_bucket.py` — `extract_evidence`

**Files:**
- Create: `apps/api/app/graph/failure_bucket.py`
- Test: `apps/api/tests/test_graph_failure_bucket.py`

**스펙 근거**: §3.3/§4.2 `HAS_EVIDENCE`(FailureBucket→Document, EXTRACTED).
`evidence_ref` 접두어 중 `confluence:`/`citects-`만 `documents.external_id`로
해석 가능 — `capture:`/`log:`/`legacy:`는 애초에 Document가 아니라서 건너뛴다.
같은 `external_id`가 여러 `source_type`에 있으면 `evidence_grade` 높은 쪽
(A>B>C) 우선(§4.2).

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_graph_failure_bucket.py`:

```python
from app.graph.extract import Edge
from app.graph.failure_bucket import extract_evidence


def _candidates(*rows):
    """pipeline.py가 넘기는 형태: external_id -> [{"document_id","evidence_grade"}, ...]"""
    out: dict[str, list[dict]] = {}
    for external_id, document_id, grade in rows:
        out.setdefault(external_id, []).append(
            {"document_id": document_id, "evidence_grade": grade}
        )
    return out


def test_extract_evidence_resolves_confluence_prefix():
    bucket = {"id": "fb1", "evidence_ref": "confluence:LOOKIN/2465855011#관측9"}
    index = _candidates(("2465855011", "doc-a", "A"))
    edges = extract_evidence(bucket, external_id_index=index)
    assert edges == [
        Edge(rel_type="HAS_EVIDENCE", target_label="Document", target_key="id",
             target_value="doc-a", tag="EXTRACTED")
    ]


def test_extract_evidence_prefers_higher_evidence_grade_on_ambiguity():
    bucket = {"id": "fb1", "evidence_ref": "confluence:LOOKIN/2465855011"}
    index = _candidates(
        ("2465855011", "doc-c-grade", "C"),
        ("2465855011", "doc-a-grade", "A"),
    )
    edges = extract_evidence(bucket, external_id_index=index)
    assert [e.target_value for e in edges] == ["doc-a-grade"]


def test_extract_evidence_resolves_citects_prefix():
    bucket = {"id": "fb1", "evidence_ref": "CITECTS-2481 참고"}
    index = _candidates(("citects-2481", "doc-b", "A"))
    edges = extract_evidence(bucket, external_id_index=index)
    assert [e.target_value for e in edges] == ["doc-b"]


def test_extract_evidence_skips_non_document_prefixes():
    bucket = {"id": "fb1", "evidence_ref": "capture:CLOUD.pcap#frame=9985"}
    assert extract_evidence(bucket, external_id_index={}) == []


def test_extract_evidence_skips_legacy_placeholder():
    bucket = {"id": "fb1", "evidence_ref": "legacy:pre-migration"}
    assert extract_evidence(bucket, external_id_index={}) == []
```

(주의: `CITECTS-2481`은 ticket 번호만 소문자로 정규화해 `citects-2481` 형태로
인덱스에 들어있다고 가정한다 — `confluence:` 쪽 페이지ID는 그대로 숫자 문자열.)

- [ ] **Step 2: 실패 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_failure_bucket.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.graph.failure_bucket'`

- [ ] **Step 3: 구현**

```python
"""FailureBucket 전용 추출기 — (:FailureBucket)은 (:Document)와 별도 레이블이라
app.graph.extract의 Document 추출기와 입력 타입이 다르다(스펙 §3.2)."""

from __future__ import annotations

import re

from app.graph.extract import Edge

_GRADE_RANK = {"A": 0, "A-": 1, "B": 2, "C": 3, "machine": 4, "draft": 5}
_CONFLUENCE_RE = re.compile(r"confluence:[A-Za-z0-9_]+/(\d+)", re.IGNORECASE)
_CITECTS_RE = re.compile(r"citects-\d+", re.IGNORECASE)


def best_candidate(candidates: list[dict]) -> str | None:
    if not candidates:
        return None
    best = min(candidates, key=lambda c: _GRADE_RANK.get(c["evidence_grade"], 99))
    return best["document_id"]


def extract_evidence(bucket: dict, *, external_id_index: dict[str, list[dict]]) -> list[Edge]:
    """evidence_ref에서 confluence:<space>/<pageId> 또는 CITECTS-#### 를 뽑아
    external_id_index(= external_id -> [{"document_id","evidence_grade"}, ...])로
    해석한다. capture:/log:/legacy: 등은 코퍼스 밖 아티팩트라 건너뛴다."""
    ref = bucket.get("evidence_ref") or ""
    targets: list[str] = []

    m = _CONFLUENCE_RE.search(ref)
    if m:
        page_id = m.group(1)
        best = best_candidate(external_id_index.get(page_id, []))
        if best:
            targets.append(best)

    for m in _CITECTS_RE.finditer(ref):
        key = m.group(0).lower()
        best = best_candidate(external_id_index.get(key, []))
        if best:
            targets.append(best)

    seen: set[str] = set()
    edges: list[Edge] = []
    for t in targets:
        if t in seen:
            continue
        seen.add(t)
        edges.append(
            Edge(
                rel_type="HAS_EVIDENCE",
                target_label="Document",
                target_key="id",
                target_value=t,
                tag="EXTRACTED",
            )
        )
    return edges
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_failure_bucket.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/failure_bucket.py apps/api/tests/test_graph_failure_bucket.py
git commit -m "feat(graph): add extract_evidence (HAS_EVIDENCE)"
```

---

## Task 11: `failure_bucket.py` — `extract_similar_to`

**Files:**
- Modify: `apps/api/app/graph/failure_bucket.py`
- Modify: `apps/api/tests/test_graph_failure_bucket.py`

**스펙 근거**: §3.3 `SIMILAR_TO`(FailureBucket↔FailureBucket, INFERRED) — 기존
`app.failure_buckets.match.rank_buckets()` 점수 ≥ 0.75(`_DUPLICATE_SCORE_THRESHOLD`,
`apps/api/app/failure_buckets/service.py:99`와 반드시 같은 값 유지).

- [ ] **Step 1: 테스트 추가**

```python
from app.graph.failure_bucket import extract_similar_to


def test_extract_similar_to_links_above_threshold():
    bucket = {
        "id": "fb1", "bucket_name": "b1", "symptom": "",
        "discriminating_signals": ["RST 직전 idle 60초 이상"],
    }
    others = [
        {"id": "fb2", "bucket_name": "b2", "confidence": 0.9,
         "discriminating_signals": ["RST 직전 idle 60초 이상"], "counter_signals": []},
    ]
    edges = extract_similar_to(bucket, other_buckets=others)
    assert len(edges) == 1
    assert edges[0].rel_type == "SIMILAR_TO"
    assert edges[0].target_label == "FailureBucket"
    assert edges[0].target_value == "fb2"
    assert edges[0].tag == "INFERRED"


def test_extract_similar_to_excludes_self():
    bucket = {"id": "fb1", "bucket_name": "b1", "symptom": "", "discriminating_signals": ["x"]}
    edges = extract_similar_to(bucket, other_buckets=[bucket])
    assert edges == []


def test_extract_similar_to_below_threshold_returns_empty():
    bucket = {"id": "fb1", "bucket_name": "b1", "symptom": "", "discriminating_signals": ["x"]}
    others = [
        {"id": "fb2", "bucket_name": "b2", "confidence": 0.1,
         "discriminating_signals": ["완전히 무관"], "counter_signals": []},
    ]
    assert extract_similar_to(bucket, other_buckets=others) == []
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

```python
from app.failure_buckets.match import rank_buckets

_SIMILAR_TO_THRESHOLD = 0.75  # apps/api/app/failure_buckets/service.py:99 와 동일하게 유지


def extract_similar_to(bucket: dict, *, other_buckets: list[dict]) -> list[Edge]:
    """app.failure_buckets.match.rank_buckets()를 그대로 재사용 — 새 스코어러를
    만들지 않는다. others에서 자기 자신(id 동일)은 제외."""
    candidates = [b for b in other_buckets if b.get("id") != bucket.get("id")]
    if not candidates:
        return []
    ranked = rank_buckets(
        observed_signals=bucket.get("discriminating_signals") or [],
        symptom=bucket.get("symptom") or "",
        buckets=candidates,
        top_k=len(candidates),
    )
    return [
        Edge(
            rel_type="SIMILAR_TO",
            target_label="FailureBucket",
            target_key="id",
            target_value=r["bucket_id"],
            tag="INFERRED",
        )
        for r in ranked
        if r["confidence"] >= _SIMILAR_TO_THRESHOLD
    ]
```

- [ ] **Step 4: 통과 확인**

```bash
docker compose exec -T api pytest apps/api/tests/test_graph_failure_bucket.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/failure_bucket.py apps/api/tests/test_graph_failure_bucket.py
git commit -m "feat(graph): add extract_similar_to (SIMILAR_TO)"
```

---

## Task 12: `neo4j_client.py` — MERGE 전용 래퍼

**Files:**
- Create: `apps/api/app/graph/neo4j_client.py`
- Test: `apps/api/tests/test_graph_neo4j_client.py`

**중요**: Cypher 쿼리에 `rel_type`/`target_label`을 f-string으로 끼워 넣는다 —
Neo4j는 라벨/관계타입을 파라미터로 못 받기 때문이다. 이 값들은 전부 이 저장소
자신의 추출기 코드가 만드는 고정된 enum(`"PARENT_OF"`, `"HAS_COMPONENT"`, ...)이라
사용자 입력이 섞이지 않는다 — 절대 외부 입력을 이 값으로 쓰지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_graph_neo4j_client.py` (Neo4j opt-in — Task 1에서 띄운
컨테이너가 없으면 스킵):

```python
"""Neo4j-touching tests. Skipped unless GRAPH_NEO4J_TEST_URI is set and
reachable — point it at the Task 1 neo4j service, never a shared instance
with real data (these tests MERGE then leave test nodes behind)."""

from __future__ import annotations

import os
import uuid

import pytest

_NEO4J_URI = os.environ.get("GRAPH_NEO4J_TEST_URI")

pytestmark = pytest.mark.skipif(
    not _NEO4J_URI, reason="set GRAPH_NEO4J_TEST_URI to run these"
)


def _client():
    from app.graph.neo4j_client import Neo4jClient

    return Neo4jClient(
        uri=_NEO4J_URI,
        user=os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"),
    )


def test_merge_document_creates_node_and_component_edge():
    from app.graph.extract import Edge

    client = _client()
    doc_id = f"test-{uuid.uuid4()}"
    doc = {
        "id": doc_id, "source_type": "tech_repo", "external_id": "x",
        "title": "t", "source_uri": None, "environment": None,
        "space_key": None, "priority_tier": 1,
    }
    edges = [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value=f"TestComponent-{doc_id}", tag="EXTRACTED"),
    ]
    client.merge_document(doc, edges)
    with client._driver.session() as session:
        rec = session.run(
            "MATCH (d:Document {id: $id})-[r:HAS_COMPONENT]->(c:Component) "
            "RETURN c.canonical_name AS name, r.tag AS tag",
            id=doc_id,
        ).single()
    assert rec["name"] == f"TestComponent-{doc_id}"
    assert rec["tag"] == "EXTRACTED"
    client.close()


def test_merge_document_is_idempotent():
    from app.graph.extract import Edge

    client = _client()
    doc_id = f"test-{uuid.uuid4()}"
    doc = {
        "id": doc_id, "source_type": "tech_repo", "external_id": "x",
        "title": "t", "source_uri": None, "environment": None,
        "space_key": None, "priority_tier": 1,
    }
    edges = [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="Redis", tag="EXTRACTED"),
    ]
    client.merge_document(doc, edges)
    client.merge_document(doc, edges)  # 두 번째 호출도 에러 없이, 중복 생성 없이
    with client._driver.session() as session:
        count = session.run(
            "MATCH (:Document {id: $id})-[r:HAS_COMPONENT]->(:Component) RETURN count(r) AS n",
            id=doc_id,
        ).single()["n"]
    assert count == 1
    client.close()
```

- [ ] **Step 2: 실패 확인**

```bash
GRAPH_NEO4J_TEST_URI="bolt://neo4j:7687" docker compose exec -T \
  -e GRAPH_NEO4J_TEST_URI api pytest apps/api/tests/test_graph_neo4j_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.graph.neo4j_client'`

- [ ] **Step 3: 구현**

```python
"""Thin Neo4j wrapper — only the MERGE operations app.graph.pipeline needs."""

from __future__ import annotations

from typing import Iterable

from neo4j import GraphDatabase

from app.graph.extract import Edge
from app.settings import get_settings

_DOCUMENT_SET_CLAUSE = """
    SET d.source_type = $source_type,
        d.external_id = $external_id,
        d.title = $title,
        d.source_uri = $source_uri,
        d.environment = $environment,
        d.space_key = $space_key,
        d.priority_tier = $priority_tier
"""


class Neo4jClient:
    def __init__(
        self, uri: str | None = None, user: str | None = None, password: str | None = None
    ) -> None:
        settings = get_settings()
        self._driver = GraphDatabase.driver(
            uri or settings.neo4j_uri,
            auth=(user or settings.neo4j_username, password or settings.neo4j_password),
        )

    def close(self) -> None:
        self._driver.close()

    def merge_document(self, doc: dict, edges: Iterable[Edge]) -> None:
        with self._driver.session() as session:
            session.execute_write(_merge_document_tx, doc, list(edges))

    def merge_failure_bucket(self, bucket: dict, edges: Iterable[Edge]) -> None:
        with self._driver.session() as session:
            session.execute_write(_merge_failure_bucket_tx, bucket, list(edges))


def _merge_edges(tx, source_label: str, source_key: str, source_value: str, edges: list[Edge]) -> None:
    for e in edges:
        # target_label/rel_type은 이 저장소의 고정 enum("PARENT_OF" 등)뿐이라
        # f-string 삽입이 안전하다 — 외부 입력이 이 값으로 들어오는 경로는 없다.
        tx.run(f"MERGE (t:{e.target_label} {{{e.target_key}: $value}})", value=e.target_value)
        tx.run(
            f"""
            MATCH (s:{source_label} {{{source_key}: $source_value}})
            MATCH (t:{e.target_label} {{{e.target_key}: $target_value}})
            MERGE (s)-[r:{e.rel_type}]->(t)
            SET r.tag = $tag
            """,
            source_value=source_value,
            target_value=e.target_value,
            tag=e.tag,
        )


def _merge_document_tx(tx, doc: dict, edges: list[Edge]) -> None:
    tx.run(f"MERGE (d:Document {{id: $id}}){_DOCUMENT_SET_CLAUSE}", **doc)
    _merge_edges(tx, "Document", "id", doc["id"], edges)


def _merge_failure_bucket_tx(tx, bucket: dict, edges: list[Edge]) -> None:
    tx.run(
        """
        MERGE (b:FailureBucket {id: $id})
        SET b.bucket_name = $bucket_name,
            b.fb_domain = $fb_domain,
            b.protocol = $protocol,
            b.environment = $environment,
            b.evidence_ref = $evidence_ref
        """,
        **bucket,
    )
    _merge_edges(tx, "FailureBucket", "id", bucket["id"], edges)
```

- [ ] **Step 4: 통과 확인**

```bash
GRAPH_NEO4J_TEST_URI="bolt://neo4j:7687" docker compose exec -T \
  -e GRAPH_NEO4J_TEST_URI api pytest apps/api/tests/test_graph_neo4j_client.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/neo4j_client.py apps/api/tests/test_graph_neo4j_client.py
git commit -m "feat(graph): add Neo4jClient (merge_document/merge_failure_bucket)"
```

---

## Task 13: `pipeline.py` — 오케스트레이션

**Files:**
- Create: `apps/api/app/graph/pipeline.py`
- Test: `apps/api/tests/test_graph_pipeline_db.py`

이 태스크가 전부를 묶는다: §0.1 우선순위 그룹, §4.1 멱등 루프, §4.4 에러 격리.
`priority_tier`/`space_key` 계산(§3.2)도 여기서 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_graph_pipeline_db.py` (DB opt-in, Neo4j opt-in — 둘 다 없으면
스킵):

```python
"""End-to-end pipeline test against a scratch Postgres DB + the Task 1 Neo4j.

Skipped unless both GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI are set.
"""

from __future__ import annotations

import os
import uuid

import pytest

_PG_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")
_NEO4J_URI = os.environ.get("GRAPH_NEO4J_TEST_URI")

pytestmark = pytest.mark.skipif(
    not (_PG_DSN and _NEO4J_URI),
    reason="set GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI to run these",
)

if _PG_DSN:
    os.environ["DATABASE_URL"] = _PG_DSN


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


def test_compute_priority_tier_classifies_lookin_and_techrepo_as_tier_1():
    from app.graph.pipeline import compute_priority_tier

    assert compute_priority_tier("tech_repo", space_key=None) == 1
    assert compute_priority_tier("confluence_docs", space_key=None) == 1
    assert compute_priority_tier("checkitem", space_key=None) == 1
    assert compute_priority_tier("confluence_map", space_key="LOOKIN") == 1
    assert compute_priority_tier("confluence_map", space_key="TechRepo") == 1
    assert compute_priority_tier("confluence_map", space_key="ICLOUDUT") == 3
    assert compute_priority_tier("incident_reports", space_key=None) == 2
    assert compute_priority_tier("support_history", space_key=None) == 3


def test_sync_one_document_creates_node_and_is_idempotent():
    import app.graph.pipeline as pipeline
    from app.db.models import Document
    from app.db.session import session_scope

    doc_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(
            Document(
                id=doc_id, source_type="tech_repo", external_id=str(uuid.uuid4()),
                title="테스트 문서", body_md="Redis 장애", content_hash="h1",
            )
        )

    client = pipeline.build_neo4j_client(
        uri=_NEO4J_URI,
        user=os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"),
    )
    try:
        first = pipeline.sync_document(doc_id, client=client)
        assert first == "synced"
        second = pipeline.sync_document(doc_id, client=client)
        assert second == "skipped"  # input_hash 안 바뀜 — 재추출 안 함
    finally:
        client.close()
```

- [ ] **Step 2: 실패 확인**

```bash
GRAPH_TEST_DATABASE_URL="$DATABASE_URL" GRAPH_NEO4J_TEST_URI="bolt://neo4j:7687" \
  docker compose exec -T -e GRAPH_TEST_DATABASE_URL -e GRAPH_NEO4J_TEST_URI api \
  pytest apps/api/tests/test_graph_pipeline_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.graph.pipeline'`

- [ ] **Step 3: 구현**

```python
"""Orchestration: priority groups (spec §0.1) + per-document idempotent sync
(spec §4.1/§4.4). This is the only module that touches both Postgres and
Neo4j — every extractor it calls stays a pure function."""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import select

from app.db.models import (
    Checkitem,
    Document,
    DocumentEntity,
    Entity,
    FailureBucket,
    IssueFrame,
)
from app.db.session import session_scope
from app.graph import sync_state
from app.graph.extract import (
    extract_business_entities,
    extract_hierarchy,
    extract_lexicon_components,
    extract_references,
    extract_structured_components,
)
from app.graph.failure_bucket import extract_evidence, extract_similar_to
from app.graph.hashing import compute_graph_hash
from app.graph.neo4j_client import Neo4jClient
from app.lexicon.seed import load_lexicon_map

logger = logging.getLogger("citec.graph")

EXTRACTOR_VERSION = "2026-10-08.1"

# 스펙 §0.1 — CI-TEC 1차 산출물이 confluence_map의 유일한 두 "승격" 공간이다.
_TIER_1_SOURCE_TYPES = {"tech_repo", "confluence_docs", "checkitem"}
_TIER_1_CONFLUENCE_MAP_SPACES = {"LOOKIN", "TechRepo"}
_TIER_2_SOURCE_TYPES = {"incident_reports"}


def compute_priority_tier(source_type: str, *, space_key: str | None) -> Literal[1, 2, 3]:
    if source_type in _TIER_1_SOURCE_TYPES:
        return 1
    if source_type == "confluence_map":
        return 1 if space_key in _TIER_1_CONFLUENCE_MAP_SPACES else 3
    if source_type in _TIER_2_SOURCE_TYPES:
        return 2
    return 3


def build_neo4j_client(*, uri: str | None = None, user: str | None = None, password: str | None = None) -> Neo4jClient:
    return Neo4jClient(uri=uri, user=user, password=password)


def build_external_id_index() -> dict[str, list[dict]]:
    """external_id(소문자 정규화) -> [{"document_id","evidence_grade"}, ...].
    extract_evidence가 그대로 쓰고, extract_references는 app.graph.failure_bucket.
    best_candidate()로 단일 document_id만 뽑아 쓴다(§4.2 — evidence_grade 높은
    쪽 우선). 세션당 한 번만 만들어 모든 문서/버킷이 공유한다."""
    with session_scope() as session:
        rows = session.execute(
            select(Document.id, Document.external_id, Document.evidence_grade)
        ).all()
    index: dict[str, list[dict]] = {}
    for doc_id, external_id, grade in rows:
        key = (external_id or "").strip().lower()
        if not key:
            continue
        index.setdefault(key, []).append({"document_id": doc_id, "evidence_grade": grade})
    return index


def _simple_reference_index(rich_index: dict[str, list[dict]]) -> dict[str, str]:
    """extract_references가 쓰는 "단일 best document_id" 매핑으로 축약."""
    from app.graph.failure_bucket import best_candidate

    out: dict[str, str] = {}
    for key, candidates in rich_index.items():
        best = best_candidate(candidates)
        if best:
            out[key] = best
    return out


def sync_document(
    document_id: str, *, client: Neo4jClient, external_id_index: dict[str, list[dict]] | None = None
) -> Literal["synced", "skipped", "failed"]:
    """문서 1건 동기화 — 문서 단위 격리(§4.4): 실패해도 예외를 밖으로 던지지 않고
    "failed"를 반환해, pipeline을 호출하는 쪽(향후 CLI)이 배치를 멈추지 않게 한다."""
    try:
        with session_scope() as session:
            doc = session.get(Document, document_id)
            if doc is None:
                return "failed"
            issue_frame = session.scalar(
                select(IssueFrame).where(IssueFrame.document_id == document_id)
            )
            checkitem = session.scalar(
                select(Checkitem).where(Checkitem.document_id == document_id)
            )
            doc_entities_rows = list(
                session.execute(
                    select(DocumentEntity.entity_id, Entity.type)
                    .join(Entity, Entity.id == DocumentEntity.entity_id)
                    .where(DocumentEntity.document_id == document_id)
                ).all()
            )
            metadata = dict(doc.metadata_ or {})
            space_key = metadata.get("space_key")
            doc_dict = {
                "id": doc.id,
                "source_type": doc.source_type,
                "external_id": doc.external_id,
                "title": doc.title,
                "body_md": doc.body_md,
                "source_uri": doc.source_uri,
                "environment": doc.environment,
                "space_key": space_key,
                "priority_tier": compute_priority_tier(doc.source_type, space_key=space_key),
                "metadata": metadata,
            }
            issue_frame_dict = (
                {"components": list(issue_frame.components or [])} if issue_frame else None
            )
            checkitem_dict = {"area": checkitem.area} if checkitem else None
            document_entities = [
                {"entity_id": row.entity_id, "entity_type": row.type} for row in doc_entities_rows
            ]

            extra_hash_input = {
                "ancestor_ids": metadata.get("ancestor_ids"),
                "components": issue_frame_dict.get("components") if issue_frame_dict else None,
                "area": checkitem_dict.get("area") if checkitem_dict else None,
            }
            input_hash = compute_graph_hash(doc.content_hash, extra_hash_input)

        state = sync_state.get_state(document_id)
        if state and state["input_hash"] == input_hash:
            return "skipped"

        lexicon_map = load_lexicon_map()
        reference_index = _simple_reference_index(external_id_index or {})
        edges = (
            extract_hierarchy(doc_dict)
            + extract_structured_components(doc_dict, issue_frame=issue_frame_dict, checkitem=checkitem_dict)
            + extract_business_entities(doc_dict, document_entities=document_entities)
            + extract_lexicon_components(doc_dict, lexicon_map=lexicon_map)
            + extract_references(doc_dict, external_id_index=reference_index)
        )
        client.merge_document(doc_dict, edges)
        sync_state.mark_synced(document_id, input_hash=input_hash, extractor_version=EXTRACTOR_VERSION)
        return "synced"
    except Exception as exc:  # noqa: BLE001 — §4.4: 문서 단위 격리, 배치 안 죽인다
        logger.exception("graph sync failed for document_id=%s", document_id)
        sync_state.mark_failed(document_id, error=str(exc))
        return "failed"


def sync_failure_bucket(
    bucket_id: str,
    *,
    client: Neo4jClient,
    all_buckets: list[dict],
    external_id_index: dict[str, list[dict]] | None = None,
) -> Literal["synced", "failed"]:
    try:
        with session_scope() as session:
            bucket = session.get(FailureBucket, bucket_id)
            if bucket is None:
                return "failed"
            bucket_dict = {
                "id": bucket.id,
                "bucket_name": bucket.bucket_name,
                "fb_domain": bucket.fb_domain,
                "protocol": bucket.protocol,
                "environment": bucket.environment,
                "evidence_ref": bucket.evidence_ref,
                "symptom": bucket.symptom,
                "discriminating_signals": list(bucket.discriminating_signals or []),
            }
        edges = extract_evidence(
            bucket_dict, external_id_index=external_id_index or {}
        ) + extract_similar_to(bucket_dict, other_buckets=all_buckets)
        client.merge_failure_bucket(bucket_dict, edges)
        return "synced"
    except Exception as exc:  # noqa: BLE001
        logger.exception("graph sync failed for failure_bucket_id=%s", bucket_id)
        return "failed"
```

`build_external_id_index()`는 `documents` 72만~건을 전부 읽어와 메모리에 들고
있는다 — 1단계 규모(114,930건, 대부분 짧은 id/문자열)에서는 수십 MB 수준이라
괜찮지만, 코퍼스가 훨씬 커지면 재검토가 필요하다는 걸 Task 14에서 다시 언급한다.

- [ ] **Step 4: 통과 확인**

```bash
GRAPH_TEST_DATABASE_URL="$DATABASE_URL" GRAPH_NEO4J_TEST_URI="bolt://neo4j:7687" \
  docker compose exec -T -e GRAPH_TEST_DATABASE_URL -e GRAPH_NEO4J_TEST_URI api \
  pytest apps/api/tests/test_graph_pipeline_db.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/pipeline.py apps/api/tests/test_graph_pipeline_db.py
git commit -m "feat(graph): add pipeline orchestration (sync_document/sync_failure_bucket)"
```

---

## Task 14: CLI + 실행 스크립트

**Files:**
- Create: `apps/api/app/graph/sync_cli.py`
- Create: `scripts/graph_sync.sh`

- [ ] **Step 1: CLI 작성**

`apps/api/app/graph/sync_cli.py`:

```python
"""CLI: python -m app.graph.sync_cli — scripts/graph_sync.sh가 컨테이너 안에서 호출."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy import select

from app.db.models import Document, FailureBucket
from app.db.session import session_scope
from app.graph.pipeline import (
    build_external_id_index,
    build_neo4j_client,
    sync_document,
    sync_failure_bucket,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="대상 건수만 세고 종료")
    p.add_argument("--source-ids", help="쉼표구분 document_id만 처리(디버그용)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    with session_scope() as session:
        doc_ids = (
            [s.strip() for s in args.source_ids.split(",") if s.strip()]
            if args.source_ids
            else [row[0] for row in session.execute(select(Document.id)).all()]
        )
        bucket_ids = [row[0] for row in session.execute(select(FailureBucket.id)).all()]

    if args.dry_run:
        print(json.dumps({"documents": len(doc_ids), "failure_buckets": len(bucket_ids)}))
        return 0

    client = build_neo4j_client()
    external_id_index = build_external_id_index()
    stats = {"synced": 0, "skipped": 0, "failed": 0}
    try:
        for doc_id in doc_ids:
            result = sync_document(doc_id, client=client, external_id_index=external_id_index)
            stats[result] += 1

        all_buckets: list[dict] = []
        with session_scope() as session:
            for b in session.scalars(select(FailureBucket)).all():
                all_buckets.append(
                    {
                        "id": b.id,
                        "bucket_name": b.bucket_name,
                        "confidence": b.confidence,
                        "discriminating_signals": list(b.discriminating_signals or []),
                        "counter_signals": list(b.counter_signals or []),
                    }
                )
        fb_stats = {"synced": 0, "failed": 0}
        for bucket_id in bucket_ids:
            result = sync_failure_bucket(
                bucket_id, client=client, all_buckets=all_buckets, external_id_index=external_id_index
            )
            fb_stats[result] += 1
    finally:
        client.close()

    print(json.dumps({"documents": stats, "failure_buckets": fb_stats}, ensure_ascii=False))
    return 0 if stats["failed"] == 0 and fb_stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 실행 스크립트 작성**

`scripts/graph_sync.sh` (`scripts/map_backfill.sh`와 동일한 컨테이너 실행 패턴 —
포그라운드 전용으로 시작한다, 백그라운드 데몬화는 2단계에서 필요해지면 추가):

```bash
#!/usr/bin/env bash
# graph_sync.sh — Postgres -> Neo4j 지식그래프 백필 (app.graph.sync_cli).
#
#   scripts/graph_sync.sh --dry-run
#   scripts/graph_sync.sh
#   scripts/graph_sync.sh --source-ids doc-id-1,doc-id-2
set -euo pipefail

PROJECT_DIR="${GRAPH_SYNC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE="${GRAPH_SYNC_SERVICE:-api}"

cd "$PROJECT_DIR"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "중단: ${SERVICE} 컨테이너가 실행 중이 아닙니다." >&2
  exit 1
fi

docker compose exec -T "$SERVICE" python -m app.graph.sync_cli "$@"
```

```bash
chmod +x scripts/graph_sync.sh
```

- [ ] **Step 3: dry-run으로 확인**

```bash
scripts/graph_sync.sh --dry-run
```

Expected: `{"documents": 114930, "failure_buckets": 8}`(§0.2 반입 후 실제 건수와
일치해야 한다 — 다르면 DB 연결이 다른 곳을 보고 있다는 뜻).

- [ ] **Step 4: 소규모 실제 실행으로 확인**

```bash
# 문서 하나만 골라서(§4.1 --source-ids) 실제로 Neo4j에 들어가는지 확인
DOC_ID=$(docker compose exec -T postgres psql -U citec -d citec_knowledge -t -A \
  -c "select id from documents where source_type='failure_bucket' limit 0;" )
# tech_repo 문서 하나로:
DOC_ID=$(docker compose exec -T postgres psql -U citec -d citec_knowledge -t -A \
  -c "select id from documents where source_type='tech_repo' limit 1;")
scripts/graph_sync.sh --source-ids "$DOC_ID" -v
docker compose exec -T postgres psql -U citec -d citec_knowledge -c \
  "select * from graph_sync_state where document_id='${DOC_ID}';"
```

Expected: `graph_sync_state`에 해당 `document_id` 행이 생기고 `last_error`가 NULL.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/graph/sync_cli.py scripts/graph_sync.sh
git commit -m "feat(graph): add sync_cli + graph_sync.sh wrapper"
```

---

## 마지막: 스펙 대조 셀프 리뷰

- §3.1 `graph_sync_state` 스키마 — Task 2 ✅
- §3.2 노드 4종 — Document(Task 13 `sync_document`)/Component(Task 6,8)/
  BusinessEntity(Task 7)/FailureBucket(Task 10,11,13 `sync_failure_bucket`) ✅.
  `space_key`/`priority_tier`는 Task 13 `compute_priority_tier` ✅
- §3.3 엣지 6종 — PARENT_OF(Task 5)/HAS_COMPONENT×2(Task 6,8)/MENTIONS_ENTITY(Task 7)/
  HAS_EVIDENCE(Task 10)/SIMILAR_TO(Task 11)/REFERENCES(Task 9) ✅
- §4.1 우선순위 그룹 — Task 13 `compute_priority_tier`로 분류는 되지만, **CLI가
  실제로 그룹 순서대로 도는 루프는 이 플랜에 없다**(Task 14는 전체를 한 번에
  순회) — 알려진 축소점으로 남긴다(배치가 끝까지 안 돌 걱정이 없는 1단계
  검증 범위에서는 순서가 결과에 영향 없음, §4.1 "그룹 순서가 결과를 바꾸는 건
  아니다"와 일치)
- §4.2 추출기 체인 6개 — 전부 구현(Task 5~11) ✅. `external_id_index`는 Task 13
  `build_external_id_index()`가 세션당 1회 구성해 Task 14 CLI에서
  `sync_document`/`sync_failure_bucket` 양쪽에 공유 주입 — `extract_references`/
  `extract_evidence`가 빈 매핑으로 끝나는 경로는 테스트 전용(각 함수의 "no match"
  단위 테스트)이고, 실제 배치 실행에서는 항상 실제 인덱스가 들어간다
- §4.4 멱등성/에러 처리 — 문서 단위 try/except(Task 13), MERGE 키=Postgres id
  그대로(Task 12), 스텁 노드는 `_merge_edges`의 `MERGE (t:Label {key: value})`가
  해당(Task 12) ✅. **prune_orphans(§4.1 주석)는 이 플랜에 없음** — 1단계는
  삭제된 문서 정리를 다루지 않는다(신규 기능이라 기존 삭제 이력이 없어 당장
  필요치 않음, 2단계로 미룸)
- §5 테스트 전략 — 추출기 단위 테스트(Task 5~11), 멱등성 테스트(Task 12,13),
  통합 테스트(Task 13). **해시 민감도 회귀 테스트**는 Task 4에 포함 ✅.
  **dev/prod 비대칭 fixture**(본문 없음/있음)는 Task 9(`extract_references`)의
  "empty body" 테스트로 일부 커버 — §5가 요구하는 "본문이 채워진 confluence_map
  실사례 fixture"는 **이 플랜에 없음**(2단계 조회 레이어 테스트에서 다룬다)
- §6 범위 밖 전부 — 이 플랜에 없음(설계상 의도대로) ✅
