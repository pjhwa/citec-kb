# 지식그래프 2단계 — MCP 조회 노출(`kb_graph_explore`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 스펙(`docs/superpowers/specs/2026-10-09-knowledge-graph-mcp-explore-design.md`)대로,
Neo4j에 백필된 지식그래프를 `POST /v1/graph/explore` + MCP 툴 `kb_graph_explore` 하나로
조회 가능하게 만든다. 조회(read)만 다루며 기존 백필 파이프라인·엔드포인트·MCP 툴은 수정하지 않는다.

**Architecture:** `kb_graph_explore`(MCP, 얇은 프록시) → `POST /v1/graph/explore`(신규 API) →
`Neo4jClient.explore()`(신규 읽기 메서드, Cypher 실행만) + `app/graph/explore.py`(신규, Neo4j
의존성 없는 순수 함수 — 앵커 정규화/허브 제외/정렬/절단/evidence_grade 병합). 허브 판정은
백필 직후 1회 집계(`recompute_hub_flags()`)로 `Component.is_hub` 속성을 동적으로 채운다
(스펙 §3.1 — 하드코딩 13종 목록은 실측으로 이미 틀렸음이 확인됐으므로 쓰지 않음).

**Tech Stack:** FastAPI(`apps/api`), Neo4j Python driver(이미 `requirements.txt`에 있음),
SQLAlchemy(Postgres `documents.evidence_grade` 조회), `mcp-server/server.py`(FastMCP).

---

## 파일 구조

| 경로 | 변경 | 역할 |
|---|---|---|
| `apps/api/app/graph/pipeline.py` | 수정 | `recompute_hub_flags()` 추가 |
| `apps/api/app/graph/neo4j_client.py` | 수정 | `explore()` 읽기 전용 메서드 추가 |
| `apps/api/app/graph/sync_state.py` | 수정 | `get_latest_synced_at()` 추가 |
| `apps/api/app/graph/sync_cli.py` | 수정 | 백필 끝에 `recompute_hub_flags()` 호출 + `--skip-hub-recompute` 플래그 |
| `apps/api/app/graph/explore.py` | 신규 | Neo4j 비의존 순수 함수(앵커 정규화, 허브 제외, 정렬/절단, evidence_grade 병합) |
| `apps/api/app/routers/graph.py` | 신규 | `POST /v1/graph/explore` 엔드포인트 |
| `apps/api/app/main.py` | 수정 | 신규 라우터 등록 |
| `apps/api/tests/test_graph_explore.py` | 신규 | `app/graph/explore.py` 순수 함수 단위 테스트(CI에서 항상 실행) |
| `apps/api/tests/test_graph_hub_flags.py` | 신규 | `recompute_hub_flags()` 테스트(Neo4j 필요, skipif) |
| `apps/api/tests/test_graph_neo4j_client.py` | 수정 | `explore()` 테스트 추가(Neo4j 필요, skipif — 기존 파일의 기존 패턴 그대로) |
| `apps/api/tests/test_graph_sync_state_db.py` | 수정 | `get_latest_synced_at()` 테스트 추가(Postgres 필요, skipif — 기존 패턴) |
| `mcp-server/server.py` | 수정 | `kb_graph_explore` 툴 + `kb_tools_help()` 항목 |
| `docs/AI_AGENT_GUIDE.md` | 수정 | §4.16 신설, §6.1에 한 줄 추가, Scenario I 추가 |

**CI 영향 없음**: `apps/api/requirements-ci.txt`는 건드리지 않는다 — Neo4j가 필요한 모든 테스트는
기존 관례(`test_graph_neo4j_client.py`)처럼 테스트 함수 안에서 `from app.graph.neo4j_client import
Neo4jClient`를 지연 임포트하고 `skipif`로 막는다. `app/graph/explore.py`는 Neo4j를 전혀 import하지
않으므로 CI에서 매번 실제로 실행된다 — 이번 변경의 핵심 로직(허브 제외/정렬/절단)에 대한 실질적
커버리지는 여기서 나온다.

---

## Task 1: `recompute_hub_flags()` — degree 기반 `is_hub` 동적 집계

**Files:**
- Modify: `apps/api/app/graph/pipeline.py`
- Modify: `apps/api/app/graph/sync_cli.py`
- Test: `apps/api/tests/test_graph_hub_flags.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# apps/api/tests/test_graph_hub_flags.py
"""recompute_hub_flags() 테스트 — Neo4j 필요, 공유 인스턴스에 실데이터 있으면 안 됨
(이 테스트는 MERGE로 더미 노드를 만들고 지우지 않음 — Task 1 neo4j 서비스만 쓸 것)."""

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


def test_recompute_hub_flags_marks_high_degree_component_as_hub():
    from app.graph.pipeline import recompute_hub_flags
    from app.graph.extract import Edge

    client = _client()
    hub_name = f"HubComponent-{uuid.uuid4()}"
    quiet_name = f"QuietComponent-{uuid.uuid4()}"

    # hub_name에 5001개의 서로 다른 문서로부터 HAS_COMPONENT 엣지, quiet_name에는 1개
    for i in range(5001):
        doc = {
            "id": f"test-hub-{hub_name}-{i}", "source_type": "tech_repo", "external_id": "x",
            "title": "t", "source_uri": None, "environment": None,
            "space_key": None, "priority_tier": 1,
        }
        edges = [Edge(rel_type="HAS_COMPONENT", target_label="Component",
                       target_key="canonical_name", target_value=hub_name, tag="EXTRACTED")]
        client.merge_document(doc, edges)

    quiet_doc = {
        "id": f"test-quiet-{quiet_name}", "source_type": "tech_repo", "external_id": "x",
        "title": "t", "source_uri": None, "environment": None,
        "space_key": None, "priority_tier": 1,
    }
    client.merge_document(
        quiet_doc,
        [Edge(rel_type="HAS_COMPONENT", target_label="Component",
              target_key="canonical_name", target_value=quiet_name, tag="EXTRACTED")],
    )

    recompute_hub_flags(client)

    with client._driver.session() as session:
        hub_flag = session.run(
            "MATCH (c:Component {canonical_name: $name}) RETURN c.is_hub AS v",
            name=hub_name,
        ).single()["v"]
        quiet_flag = session.run(
            "MATCH (c:Component {canonical_name: $name}) RETURN c.is_hub AS v",
            name=quiet_name,
        ).single()["v"]
    assert hub_flag is True
    assert quiet_flag is False
    client.close()
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `GRAPH_NEO4J_TEST_URI=bolt://localhost:8579 pytest apps/api/tests/test_graph_hub_flags.py -v`
Expected: FAIL — `ImportError: cannot import name 'recompute_hub_flags'`

- [ ] **Step 3: `recompute_hub_flags()` 구현**

`apps/api/app/graph/pipeline.py`에 추가 (파일 맨 아래, `sync_failure_bucket` 뒤):

```python
_HUB_DEGREE_THRESHOLD = 5000


def _recompute_hub_flags_tx(tx) -> None:
    tx.run(
        """
        MATCH (c:Component)
        OPTIONAL MATCH (c)<-[:HAS_COMPONENT]-()
        WITH c, count(*) AS degree
        SET c.is_hub = (degree > $threshold)
        """,
        threshold=_HUB_DEGREE_THRESHOLD,
    )


def recompute_hub_flags(client: Neo4jClient) -> None:
    """백필 1회 실행이 끝난 뒤 호출 — 모든 Component의 HAS_COMPONENT 입력 degree를
    다시 집계해 is_hub를 갱신한다(스펙 §3.1). 하드코딩 목록이 아니라 매 실행마다
    실측으로 재계산되므로, 1단계 설계 §6의 13종 목록처럼 데이터가 바뀌면 틀려지는
    문제가 구조적으로 없다. 증분 실행 때마다 돌 필요는 없지만(비용이 전체
    Component 스캔 1회뿐이라 가벼움) 기본은 매번 실행 — sync_cli의
    --skip-hub-recompute로 끌 수 있다."""
    client._driver.session().execute_write(_recompute_hub_flags_tx)
```

- [ ] **Step 4: 테스트 재실행해 통과 확인**

Run: `GRAPH_NEO4J_TEST_URI=bolt://localhost:8579 pytest apps/api/tests/test_graph_hub_flags.py -v`
Expected: PASS

- [ ] **Step 5: `sync_cli.py`에 연동**

`apps/api/app/graph/sync_cli.py`를 수정:

```python
from app.graph.pipeline import (
    build_external_id_index,
    build_neo4j_client,
    recompute_hub_flags,
    sync_document,
    sync_failure_bucket,
)
```

`argparse` 블록에 추가:

```python
    p.add_argument(
        "--skip-hub-recompute", action="store_true",
        help="Component.is_hub 재집계 생략(디버그/부분 실행용)",
    )
```

`client.close()` 전, `finally` 블록 바로 앞에 추가 (즉 try 블록의 failure_bucket 동기화
루프 바로 다음):

```python
        if not args.skip_hub_recompute:
            recompute_hub_flags(client)
```

- [ ] **Step 6: 커밋**

```bash
git add apps/api/app/graph/pipeline.py apps/api/app/graph/sync_cli.py apps/api/tests/test_graph_hub_flags.py
git commit -m "feat(graph): add recompute_hub_flags for dynamic is_hub tagging"
```

---

## Task 2: `Neo4jClient.explore()` — 2-hop 읽기 전용 순회

**Files:**
- Modify: `apps/api/app/graph/neo4j_client.py`
- Test: `apps/api/tests/test_graph_neo4j_client.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_graph_neo4j_client.py` 끝에 추가 (기존 import들 재사용, 파일 상단의
`pytestmark`/`_client()`는 이미 있음):

```python
def test_explore_returns_1hop_and_2hop_neighbors_with_hop_distance():
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    fb_id = f"test-fb-{uuid.uuid4()}"
    doc_id = f"test-doc-{uuid.uuid4()}"
    comp_name = f"TestComp-{uuid.uuid4()}"

    # fb_id -[HAS_EVIDENCE]-> doc_id -[HAS_COMPONENT]-> comp_name (2hop from fb_id)
    client.merge_failure_bucket(
        {"id": fb_id, "bucket_name": "b", "fb_domain": "network", "protocol": None,
         "environment": None, "evidence_ref": None},
        [Edge(rel_type="HAS_EVIDENCE", target_label="Document", target_key="id",
              target_value=doc_id, tag="EXTRACTED")],
    )
    client.merge_document(
        {"id": doc_id, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )

    result = client.explore("FailureBucket", "id", fb_id, max_hops=2)

    doc_hops = {n["id"]: n["hops"] for n in result["documents"]}
    comp_hops = {n["canonical_name"]: n["hops"] for n in result["components"]}
    assert doc_hops.get(doc_id) == 1
    assert comp_hops.get(comp_name) == 2
    client.close()


def test_explore_excludes_hub_components_from_results():
    from app.graph.extract import Edge
    from app.graph.pipeline import recompute_hub_flags

    client = _client()
    client.ensure_constraints()
    doc_id = f"test-doc-{uuid.uuid4()}"
    hub_name = f"HubComp-{uuid.uuid4()}"
    for i in range(5001):
        client.merge_document(
            {"id": f"test-fan-{hub_name}-{i}", "source_type": "tech_repo", "external_id": "x",
             "title": "t", "source_uri": None, "environment": None, "space_key": None,
             "priority_tier": 1},
            [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                  target_value=hub_name, tag="EXTRACTED")],
        )
    client.merge_document(
        {"id": doc_id, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=hub_name, tag="EXTRACTED")],
    )
    recompute_hub_flags(client)

    result = client.explore("Document", "id", doc_id, max_hops=2)

    names = {n["canonical_name"] for n in result["components"]}
    assert hub_name not in names
    assert hub_name in result["excluded_hub_components"]
    client.close()
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `GRAPH_NEO4J_TEST_URI=bolt://localhost:8579 pytest apps/api/tests/test_graph_neo4j_client.py -v -k explore`
Expected: FAIL — `AttributeError: 'Neo4jClient' object has no attribute 'explore'`

- [ ] **Step 3: `explore()` 구현**

`apps/api/app/graph/neo4j_client.py`에 추가. 먼저 파일 상단 `_CONSTRAINTS` 아래에 레이블별 반환
속성 매핑을 추가:

```python
_RETURN_PROPS = {
    "Document": ["id", "title", "source_type"],
    "Component": ["canonical_name", "is_hub"],
    "FailureBucket": ["id", "bucket_name"],
    "BusinessEntity": ["id"],
}
```

`Neo4jClient` 클래스에 메서드 추가 (`merge_failure_bucket` 메서드 뒤):

```python
    def explore(self, anchor_label: str, anchor_key: str, anchor_value: str, *, max_hops: int = 2) -> dict:
        """읽기 전용 2-hop 순회. anchor_label/anchor_key는 이 모듈의 고정 4-레이블 enum뿐이라
        f-string 삽입이 안전하다(merge_* 메서드와 동일한 전제)."""
        query = f"""
        MATCH (a:{anchor_label} {{{anchor_key}: $value}})
        OPTIONAL MATCH (a)-[r1]-(n1)
        WHERE n1 <> a
        WITH a, collect(DISTINCT {{node: n1, relation: type(r1), hops: 1}}) AS hop1
        OPTIONAL MATCH (a)-[]-()-[r2]-(n2)
        WHERE n2 <> a
        WITH a, hop1, collect(DISTINCT {{node: n2, relation: type(r2), hops: 2}}) AS hop2
        RETURN a AS anchor, hop1 + hop2 AS neighbors
        """
        with self._driver.session() as session:
            record = session.run(query, value=anchor_value).single()
        if record is None or record["anchor"] is None:
            return {
                "found": False, "documents": [], "components": [], "failure_buckets": [],
                "excluded_hub_components": [], "truncated": False,
            }

        best_by_id: dict[int, dict] = {}
        for entry in record["neighbors"]:
            node = entry["node"]
            if node is None:
                continue
            elem_id = node.element_id
            hops = entry["hops"]
            if elem_id not in best_by_id or hops < best_by_id[elem_id]["hops"]:
                best_by_id[elem_id] = {"node": node, "relation": entry["relation"], "hops": hops}

        documents: list[dict] = []
        components: list[dict] = []
        failure_buckets: list[dict] = []
        excluded_hub_components: list[str] = []
        for entry in best_by_id.values():
            node = entry["node"]
            labels = set(node.labels)
            if "Document" in labels:
                documents.append({
                    "id": node["id"], "title": node.get("title"), "source_type": node.get("source_type"),
                    "relation": entry["relation"], "hops": entry["hops"],
                })
            elif "Component" in labels:
                if node.get("is_hub"):
                    excluded_hub_components.append(node["canonical_name"])
                    continue
                components.append({
                    "canonical_name": node["canonical_name"],
                    "relation": entry["relation"], "hops": entry["hops"],
                })
            elif "FailureBucket" in labels:
                failure_buckets.append({
                    "id": node["id"], "bucket_name": node.get("bucket_name"),
                    "relation": entry["relation"], "hops": entry["hops"],
                })

        return {
            "found": True,
            "documents": documents, "components": components, "failure_buckets": failure_buckets,
            "excluded_hub_components": sorted(set(excluded_hub_components)),
            "truncated": False,  # cap/truncate는 app.graph.explore(순수 함수)에서 처리
        }
```

- [ ] **Step 4: 테스트 재실행해 통과 확인**

Run: `GRAPH_NEO4J_TEST_URI=bolt://localhost:8579 pytest apps/api/tests/test_graph_neo4j_client.py -v -k explore`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/graph/neo4j_client.py apps/api/tests/test_graph_neo4j_client.py
git commit -m "feat(graph): add Neo4jClient.explore() read-only 2hop traversal"
```

---

## Task 3: `sync_state.get_latest_synced_at()` — 신선도(`as_of`) 조회

**Files:**
- Modify: `apps/api/app/graph/sync_state.py`
- Test: `apps/api/tests/test_graph_sync_state_db.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_graph_sync_state_db.py` 끝에 추가 (파일 상단의 `pytestmark`/DSN 설정은
이미 있음):

```python
def test_get_latest_synced_at_returns_max_synced_at():
    from app.graph.sync_state import get_latest_synced_at, mark_synced

    mark_synced(f"doc-a-{uuid.uuid4()}", input_hash="h1", extractor_version="v1")
    latest_id = f"doc-b-{uuid.uuid4()}"
    mark_synced(latest_id, input_hash="h2", extractor_version="v1")

    result = get_latest_synced_at()
    assert result is not None


def test_get_latest_synced_at_returns_none_when_no_rows():
    from app.graph.sync_state import get_latest_synced_at
    from app.db.session import session_scope
    from app.db.models import GraphSyncState

    with session_scope() as session:
        session.query(GraphSyncState).delete()

    assert get_latest_synced_at() is None
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `GRAPH_TEST_DATABASE_URL=postgresql+psycopg://... pytest apps/api/tests/test_graph_sync_state_db.py -v -k latest_synced_at`
Expected: FAIL — `ImportError: cannot import name 'get_latest_synced_at'`

- [ ] **Step 3: 구현**

`apps/api/app/graph/sync_state.py` 끝에 추가:

```python
def get_latest_synced_at() -> Optional[datetime]:
    """graph_sync_state 전체에서 가장 최근 synced_at. 행이 하나도 없으면(백필 전) None —
    호출자(라우터)가 "아직 동기화된 적 없음"으로 처리한다."""
    with session_scope() as session:
        return session.query(func.max(GraphSyncState.synced_at)).scalar()
```

파일 상단 import에 `func` 추가:

```python
from sqlalchemy import func
```

- [ ] **Step 4: 테스트 재실행해 통과 확인**

Run: `GRAPH_TEST_DATABASE_URL=postgresql+psycopg://... pytest apps/api/tests/test_graph_sync_state_db.py -v -k latest_synced_at`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/graph/sync_state.py apps/api/tests/test_graph_sync_state_db.py
git commit -m "feat(graph): add get_latest_synced_at for explore endpoint freshness"
```

---

## Task 4: `app/graph/explore.py` — 순수 함수(앵커 정규화, 정렬/절단, evidence_grade 병합)

이 태스크의 모든 테스트는 CI에서 매번 실행된다(Neo4j/Postgres 불필요).

**Files:**
- Create: `apps/api/app/graph/explore.py`
- Test: `apps/api/tests/test_graph_explore.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# apps/api/tests/test_graph_explore.py
from __future__ import annotations

from app.graph.explore import (
    MAX_RESULTS_PER_CATEGORY,
    enrich_with_evidence_grade,
    resolve_component_anchor,
    shape_explore_result,
)


def test_resolve_component_anchor_uses_lexicon_variant():
    lexicon_map = {"netapp": ["NetApp"], "넷앱": ["NetApp"]}
    assert resolve_component_anchor("넷앱", lexicon_map) == "NetApp"
    assert resolve_component_anchor("NetApp", lexicon_map) == "NetApp"


def test_resolve_component_anchor_falls_back_to_input_when_not_in_lexicon():
    assert resolve_component_anchor("Unknown-Thing", {}) == "Unknown-Thing"


def test_shape_explore_result_sorts_by_hops_then_evidence_grade():
    raw = {
        "found": True,
        "documents": [
            {"id": "d1", "title": "t1", "source_type": "tech_repo", "relation": "HAS_EVIDENCE", "hops": 2},
            {"id": "d2", "title": "t2", "source_type": "tech_repo", "relation": "HAS_EVIDENCE", "hops": 1},
        ],
        "components": [], "failure_buckets": [], "excluded_hub_components": [],
    }
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert [d["id"] for d in shaped["documents"]] == ["d2", "d1"]
    assert shaped["as_of"] == "2026-10-09"


def test_shape_explore_result_truncates_at_cap():
    docs = [
        {"id": f"d{i}", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES", "hops": 1}
        for i in range(MAX_RESULTS_PER_CATEGORY + 1)
    ]
    raw = {"found": True, "documents": docs, "components": [], "failure_buckets": [],
           "excluded_hub_components": []}
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert len(shaped["documents"]) == MAX_RESULTS_PER_CATEGORY
    assert shaped["truncated"] is True


def test_shape_explore_result_not_found():
    raw = {"found": False, "documents": [], "components": [], "failure_buckets": [],
           "excluded_hub_components": []}
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "missing"})
    assert shaped["found"] is False


def test_enrich_with_evidence_grade_merges_by_id():
    documents = [{"id": "d1", "title": "t"}, {"id": "d2", "title": "t2"}]
    grade_by_id = {"d1": "A"}
    enriched = enrich_with_evidence_grade(documents, grade_by_id)
    assert enriched[0]["evidence_grade"] == "A"
    assert enriched[1]["evidence_grade"] is None
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `pytest apps/api/tests/test_graph_explore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.graph.explore'`

- [ ] **Step 3: 구현**

```python
# apps/api/app/graph/explore.py
"""kb_graph_explore가 쓰는 순수 로직 — Neo4j/Postgres 연결 전혀 없음, 전부 dict 입출력.
app.graph.neo4j_client.Neo4jClient.explore()의 raw 결과를 받아 API 응답 모양으로
다듬는다(정렬/절단/evidence_grade 병합) — app.graph.extract의 "추출기는 순수 함수"
원칙을 조회 경로에도 그대로 적용한다."""

from __future__ import annotations

from typing import Any, Optional

MAX_RESULTS_PER_CATEGORY = 50

_GRADE_ORDER = {"A": 0, "B": 1, "C": 2}


def resolve_component_anchor(anchor_value: str, lexicon_map: dict[str, list[str]]) -> str:
    """"넷앱"/"netapp"처럼 lexicon 변형어로 들어와도 canonical_name으로 정규화한다.
    lexicon_map은 app.lexicon.seed.load_lexicon_map()의 출력(토큰 소문자 -> [canonical, ...])과
    동일한 형태 — variants[0]이 canonical이라는 계약도 그대로 재사용한다. 매칭이 없으면
    원문 그대로 반환해, 호출자(Neo4jClient.explore)가 대소문자 무시 매칭을 한 번 더 시도할
    여지를 남긴다."""
    variants = lexicon_map.get(anchor_value.strip().lower())
    if variants:
        return variants[0]
    return anchor_value


def _sort_key(item: dict) -> tuple:
    grade = _GRADE_ORDER.get(item.get("evidence_grade") or "", 3)
    return (item.get("hops", 99), grade)


def _cap(items: list[dict]) -> tuple[list[dict], bool]:
    if len(items) <= MAX_RESULTS_PER_CATEGORY:
        return items, False
    return items[:MAX_RESULTS_PER_CATEGORY], True


def shape_explore_result(raw: dict[str, Any], *, as_of: Optional[str], anchor: dict[str, Any]) -> dict[str, Any]:
    """Neo4jClient.explore()의 raw 출력(found/documents/components/failure_buckets/
    excluded_hub_components) -> API 응답 모양(as_of/anchor/정렬/절단 포함)."""
    if not raw.get("found"):
        return {
            "as_of": as_of, "anchor": anchor, "found": False,
            "documents": [], "components": [], "failure_buckets": [],
            "excluded_hub_components": raw.get("excluded_hub_components") or [],
            "truncated": False,
        }

    documents = sorted(raw.get("documents") or [], key=_sort_key)
    components = sorted(raw.get("components") or [], key=_sort_key)
    failure_buckets = sorted(raw.get("failure_buckets") or [], key=_sort_key)

    documents, doc_trunc = _cap(documents)
    components, comp_trunc = _cap(components)
    failure_buckets, fb_trunc = _cap(failure_buckets)

    return {
        "as_of": as_of, "anchor": anchor, "found": True,
        "documents": documents, "components": components, "failure_buckets": failure_buckets,
        "excluded_hub_components": raw.get("excluded_hub_components") or [],
        "truncated": doc_trunc or comp_trunc or fb_trunc,
    }


def enrich_with_evidence_grade(documents: list[dict], grade_by_id: dict[str, str]) -> list[dict]:
    """Document 노드는 evidence_grade를 안 들고 있다(Postgres만 source of truth,
    1단계 설계 §0 원칙) — 그래서 호출자가 Neo4j 결과의 document id들로 Postgres를
    배치 조회한 뒤 이 함수로 병합한다. 못 찾으면 None(삭제된 문서 등 엣지 케이스)."""
    return [{**doc, "evidence_grade": grade_by_id.get(doc["id"])} for doc in documents]
```

- [ ] **Step 4: 테스트 재실행해 통과 확인**

Run: `pytest apps/api/tests/test_graph_explore.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/graph/explore.py apps/api/tests/test_graph_explore.py
git commit -m "feat(graph): add pure explore-shaping helpers (sort/truncate/hub-filter/evidence-grade merge)"
```

---

## Task 5: `POST /v1/graph/explore` 라우터

**Files:**
- Create: `apps/api/app/routers/graph.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_graph_router.py`

- [ ] **Step 1: 실패하는 테스트 작성**

이 테스트는 `app.main`을 임포트하므로(라우터 체인 전체가 따라옴) 기존
`test_health_active_documents_count_db.py` 관례와 동일하게 지연 임포트 + skipif로
막는다 — CI에서는 항상 스킵되고, Neo4j+Postgres 둘 다 준비된 로컬 환경에서만 돈다.

```python
# apps/api/tests/test_graph_router.py
"""POST /v1/graph/explore 통합 테스트 — Neo4j + Postgres(scratch) 둘 다 필요."""

from __future__ import annotations

import os
import uuid

import pytest

_NEO4J_URI = os.environ.get("GRAPH_NEO4J_TEST_URI")
_PG_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not (_NEO4J_URI and _PG_DSN),
    reason="set GRAPH_NEO4J_TEST_URI and GRAPH_TEST_DATABASE_URL to run these",
)

if _PG_DSN:
    os.environ["DATABASE_URL"] = _PG_DSN
if _NEO4J_URI:
    os.environ["NEO4J_URI"] = _NEO4J_URI


def test_explore_unknown_failure_bucket_returns_404():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "failure_bucket", "anchor_value": f"missing-{uuid.uuid4()}"},
    )
    assert resp.status_code == 404


def test_explore_symptom_text_with_no_component_match_returns_empty():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "symptom_text", "anchor_value": "asdkjaslkdjzzxxqqxx 전혀 매칭 안 되는 텍스트"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["documents"] == []
    assert body["components"] == []
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `GRAPH_NEO4J_TEST_URI=bolt://localhost:8579 GRAPH_TEST_DATABASE_URL=postgresql+psycopg://... pytest apps/api/tests/test_graph_router.py -v`
Expected: FAIL — 404 라우트 자체가 없어 `assert resp.status_code == 404`가
`assert 404 == 404`가 아니라 `assert <starlette 404 "Not Found">`로 통과해버릴 수 있음에
주의 — 실제로는 두 번째 테스트가 `KeyError`/500으로 먼저 실패해 라우트 부재가 드러난다.

- [ ] **Step 3: 구현**

```python
# apps/api/app/routers/graph.py
"""POST /v1/graph/explore — 지식그래프 조회(읽기 전용). 쓰기 경로는 scripts/graph_sync.sh
배치뿐이고 이 라우터는 전혀 건드리지 않는다(1단계 설계 §0 원칙 유지)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope
from app.graph.explore import (
    enrich_with_evidence_grade,
    resolve_component_anchor,
    shape_explore_result,
)
from app.graph.extract import extract_lexicon_components
from app.graph.neo4j_client import Neo4jClient
from app.graph.sync_state import get_latest_synced_at
from app.lexicon.seed import load_lexicon_map

router = APIRouter(prefix="/v1", tags=["graph"])

_ANCHOR_LABEL_KEY = {
    "failure_bucket": ("FailureBucket", "id"),
    "document": ("Document", "id"),
    "component": ("Component", "canonical_name"),
}


class GraphExploreBody(BaseModel):
    anchor_type: Literal["failure_bucket", "document", "component", "symptom_text"]
    anchor_value: str = Field(..., min_length=1, max_length=2000)


def _build_client() -> Neo4jClient:
    return Neo4jClient()


def _resolve_document_anchor_value(anchor_value: str) -> str:
    """document 앵커는 documents.id 또는 external_id 둘 다 받는다 — Neo4j의
    Document.id는 항상 documents.id이므로, external_id로 왔으면 Postgres에서
    한 번 찾아 id로 바꾼다."""
    with session_scope() as session:
        doc = session.get(Document, anchor_value)
        if doc is not None:
            return doc.id
        doc = session.scalar(select(Document).where(Document.external_id == anchor_value))
        return doc.id if doc is not None else anchor_value


def _fetch_evidence_grades(document_ids: list[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    with session_scope() as session:
        rows = session.execute(
            select(Document.id, Document.evidence_grade).where(Document.id.in_(document_ids))
        ).all()
    return {row[0]: row[1] for row in rows}


@router.post("/graph/explore")
def graph_explore(body: GraphExploreBody) -> dict[str, Any]:
    as_of = get_latest_synced_at()
    as_of_str = as_of.date().isoformat() if as_of else None
    lexicon_map = load_lexicon_map()

    anchors: list[tuple[str, str, str]] = []  # (label, key, value) — symptom_text는 여러 개일 수 있음
    if body.anchor_type == "symptom_text":
        fake_doc = {"body_md": body.anchor_value}
        edges = extract_lexicon_components(fake_doc, lexicon_map=lexicon_map)
        if not edges:
            return shape_explore_result(
                {"found": True, "documents": [], "components": [], "failure_buckets": [],
                 "excluded_hub_components": []},
                as_of=as_of_str,
                anchor={"type": "symptom_text", "resolved_id": None, "matched_components": []},
            )
        anchors = [("Component", "canonical_name", e.target_value) for e in edges]
    elif body.anchor_type == "component":
        canonical = resolve_component_anchor(body.anchor_value, lexicon_map)
        anchors = [("Component", "canonical_name", canonical)]
    elif body.anchor_type == "document":
        anchors = [("Document", "id", _resolve_document_anchor_value(body.anchor_value))]
    else:  # failure_bucket
        anchors = [("FailureBucket", "id", body.anchor_value)]

    client = _build_client()
    try:
        merged = {"found": False, "documents": [], "components": [], "failure_buckets": [],
                  "excluded_hub_components": []}
        for label, key, value in anchors:
            raw = client.explore(label, key, value, max_hops=2)
            if not raw.get("found"):
                continue
            merged["found"] = True
            merged["documents"].extend(raw["documents"])
            merged["components"].extend(raw["components"])
            merged["failure_buckets"].extend(raw["failure_buckets"])
            merged["excluded_hub_components"].extend(raw["excluded_hub_components"])
    except Exception as exc:  # noqa: BLE001 — Neo4j 장애가 기존 검색/API에 안 퍼지게 격리
        raise HTTPException(status_code=503, detail=f"그래프 저장소 연결 실패: {exc}") from exc
    finally:
        client.close()

    if not merged["found"] and body.anchor_type != "symptom_text":
        raise HTTPException(status_code=404, detail="anchor_value를 그래프에서 찾을 수 없습니다")

    doc_ids = [d["id"] for d in merged["documents"]]
    merged["documents"] = enrich_with_evidence_grade(merged["documents"], _fetch_evidence_grades(doc_ids))

    anchor_info: dict[str, Any] = {"type": body.anchor_type, "resolved_id": body.anchor_value}
    if body.anchor_type == "symptom_text":
        anchor_info["matched_components"] = [a[2] for a in anchors]

    return shape_explore_result(merged, as_of=as_of_str, anchor=anchor_info)
```

`apps/api/app/main.py`에 등록. import 블록(`from app.routers import external_compat as
external_compat_router  # noqa: E402` 바로 뒤)에 추가:

```python
from app.routers import graph as graph_router  # noqa: E402
```

`app.include_router(external_compat_router.router)` 바로 뒤에 추가:

```python
app.include_router(graph_router.router)
```

- [ ] **Step 4: 테스트 재실행해 통과 확인**

Run: `GRAPH_NEO4J_TEST_URI=bolt://localhost:8579 GRAPH_TEST_DATABASE_URL=postgresql+psycopg://... pytest apps/api/tests/test_graph_router.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 기존 테스트 스위트 전체가 깨지지 않았는지 확인**

Run: `cd apps/api && pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py`
Expected: PASS, 실패 0건 (CI와 동일 커맨드 — `requirements-ci.txt`만으로 로컬 재현 가능)

- [ ] **Step 6: 커밋**

```bash
git add apps/api/app/routers/graph.py apps/api/app/main.py apps/api/tests/test_graph_router.py
git commit -m "feat(graph): add POST /v1/graph/explore endpoint"
```

---

## Task 6: MCP 툴 `kb_graph_explore`

**Files:**
- Modify: `mcp-server/server.py`

- [ ] **Step 1: 툴 함수 추가**

`mcp-server/server.py`의 `kb_similar_incident` 함수 뒤에 추가 (같은 `_client()`/`_err()` 패턴):

```python
@mcp.tool()
async def kb_graph_explore(
    anchor_type: str,
    anchor_value: str,
) -> str:
    """장애 분석 시 연관 컴포넌트·문서·과거 장애를 지식그래프에서 탐색한다(2hop).

    anchor_type: "failure_bucket" | "document" | "component" | "symptom_text"
    anchor_value: 각각 FailureBucket.id / documents.id 또는 external_id /
                  컴포넌트 이름(예: "NetApp", "넷앱") / 자유 텍스트 증상 설명

    그래프는 하루 1회 배치로 갱신되므로(응답의 as_of 날짜 참고) 그 이후 변경은
    반영 안 돼 있을 수 있다 — 최종 확인은 kb_search/kb_get_document로.
    쓰임새: kb_match_failure_bucket/kb_similar_incident로 1차 후보를 찾은 다음,
    그 주변을 더 깊이 파는 용도. 검색의 대체재가 아니다.
    """
    if anchor_type not in ("failure_bucket", "document", "component", "symptom_text"):
        return f"오류: anchor_type은 failure_bucket|document|component|symptom_text 중 하나여야 합니다 (받음: {anchor_type})"
    if not (anchor_value or "").strip():
        return "오류: anchor_value가 비어 있습니다."
    try:
        async with _client(timeout=30.0) as client:
            resp = await client.post(
                "/v1/graph/explore",
                json={"anchor_type": anchor_type, "anchor_value": anchor_value.strip()},
            )
            if resp.status_code == 404:
                return f"그래프에서 '{anchor_value}'를 찾을 수 없습니다 (as-of 데이터 기준)."
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    lines = [f"as_of: {data.get('as_of') or '(아직 백필 안 됨)'}"]
    if data.get("excluded_hub_components"):
        lines.append(f"제외된 범용 컴포넌트: {', '.join(data['excluded_hub_components'])}")

    docs = data.get("documents") or []
    lines.append(f"\n연관 문서 ({len(docs)}건{'+' if data.get('truncated') else ''}):")
    for d in docs[:20]:
        lines.append(
            f"  - [{d.get('hops')}hop/{d.get('relation')}] {d.get('title')} "
            f"({d.get('source_type')}, evidence_grade={d.get('evidence_grade')}) id={d.get('id')}"
        )

    comps = data.get("components") or []
    lines.append(f"\n연관 컴포넌트 ({len(comps)}건):")
    for c in comps[:20]:
        lines.append(f"  - [{c.get('hops')}hop/{c.get('relation')}] {c.get('canonical_name')}")

    fbs = data.get("failure_buckets") or []
    lines.append(f"\n연관 과거 장애 ({len(fbs)}건):")
    for b in fbs[:20]:
        lines.append(f"  - [{b.get('hops')}hop/{b.get('relation')}] {b.get('bucket_name')} id={b.get('id')}")

    return "\n".join(lines)
```

- [ ] **Step 2: `kb_tools_help()`에 섹션 추가**

`kb_tools_help()` 반환 문자열의 `[실패 버킷 ...]` 섹션 바로 뒤에 추가:

```
[지식그래프 — 연관 탐색]
  kb_graph_explore(anchor_type=, anchor_value=)
                  anchor_type: failure_bucket|document|component|symptom_text
                  장애 분석 시 연관 컴포넌트/문서/과거 장애를 2hop까지 탐색.
                  kb_match_failure_bucket/kb_similar_incident로 1차 후보를 찾은
                  다음 단계로 쓸 것 — 검색 대체재 아님. 하루 1회 배치 갱신(as_of 확인).
```

- [ ] **Step 3: 수동 스모크 테스트**

```bash
docker compose up -d api mcp neo4j
# neo4j에 테스트 데이터가 있는 상태에서:
docker compose exec mcp python -c "
import asyncio, server
print(asyncio.run(server.kb_graph_explore('component', 'NetApp')))
"
```
Expected: 에러 없이 문자열 반환(연관 문서/컴포넌트 라인 포함).

- [ ] **Step 4: 커밋**

```bash
git add mcp-server/server.py
git commit -m "feat(mcp): add kb_graph_explore tool + kb_tools_help entry"
```

---

## Task 7: `docs/AI_AGENT_GUIDE.md` 반영

**Files:**
- Modify: `docs/AI_AGENT_GUIDE.md`

- [ ] **Step 1: §4.16 신설**

"### 4.15 Failure buckets ..." 섹션 끝(다음 "---" 구분선 앞)에 추가:

```markdown
### 4.16 `kb_graph_explore` — 지식그래프 연관 탐색

`kb_match_failure_bucket`/`kb_similar_incident`로 1차 후보를 찾은 **다음 단계**로 쓴다 —
검색의 대체재가 아니라 "찾은 것의 주변을 더 깊이 파는" 용도다.

- `kb_graph_explore(anchor_type=, anchor_value=)` — `anchor_type`은 `failure_bucket`/
  `document`/`component`/`symptom_text` 중 하나. 2hop까지 순회하며, 범용 컴포넌트
  (`Network`/`Storage` 등 degree 수천 이상)는 기본 제외하고 응답에 `제외된 범용
  컴포넌트`로만 표시한다.
- 응답의 `as_of`는 그래프의 마지막 배치 동기화 날짜다 — **실시간이 아니다**(최대 ~1일
  지연 가능). 최종 사실 확인은 `kb_search`/`kb_get_document`로.
- `component`/`symptom_text` 입력은 기존 lexicon 사전으로 변형어("넷앱"→`NetApp`)까지
  해석한다.

**API:** `POST /v1/graph/explore`
```

- [ ] **Step 2: §6.1에 한 줄 추가**

"### 6.1 `evidence_grade` ..." 섹션 끝에 문단 추가:

```markdown
`kb_graph_explore`가 반환하는 문서 결과의 `evidence_grade`도 이 등급 체계를 그대로
재사용한다 — 그래프 자체는 구조(관계)만 저장하고, 신뢰도 판단 기준은 항상 이 절의
Postgres 값이 유일한 출처다.
```

- [ ] **Step 3: Scenario I 추가**

"### Scenario H ..." 다음, "## 9. Anti-patterns" 앞에 추가:

```markdown
### Scenario I — "이 failure_bucket과 관련된 다른 장애·문서가 더 있나?"

1. `kb_get_failure_bucket(bucket_id="FB-12")`로 버킷 확인.
2. `kb_graph_explore(anchor_type="failure_bucket", anchor_value="FB-12")`로 2hop 연관
   문서/컴포넌트/과거 장애 탐색.
3. 범용 컴포넌트가 "제외된 범용 컴포넌트"에 뜨면 — 그건 애초에 의미 없는 결과이니
   무시하고, 나머지 구체적 컴포넌트/문서로만 답변 구성.
4. `as_of`가 오늘보다 오래됐으면 응답에 그 사실을 한 줄 명시(신선도 캐비엇).
```

- [ ] **Step 4: 커밋**

```bash
git add docs/AI_AGENT_GUIDE.md
git commit -m "docs: document kb_graph_explore in AI_AGENT_GUIDE"
```

---

## Task 8: 최종 통합 점검

**Files:** 없음(검증만)

- [ ] **Step 1: 전체 유닛 테스트(CI와 동일 커맨드)**

```bash
cd apps/api
pip install -r requirements-ci.txt
python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py
```
Expected: 전부 PASS, `test_graph_explore.py`(Task 4)의 6개 테스트가 스킵 없이 실제로
실행됐는지 확인(`-v`로 재실행해 `test_graph_explore.py ... PASSED` 확인).

- [ ] **Step 2: 로컬 Neo4j+Postgres로 전체 그래프 테스트(스킵 없이)**

```bash
GRAPH_NEO4J_TEST_URI=bolt://localhost:8579 \
GRAPH_TEST_DATABASE_URL=postgresql+psycopg://citec:citec@localhost:18574/citec_knowledge \
pytest apps/api/tests -k graph -v
```
Expected: `test_graph_hub_flags.py`/`test_graph_neo4j_client.py`/`test_graph_sync_state_db.py`/
`test_graph_router.py` 전부 PASS, 스킵 0건.

- [ ] **Step 3: 실데이터로 백필 재실행 + 허브 재집계 눈으로 확인**

```bash
scripts/graph_sync.sh --dry-run   # 건수만 재확인
# 로컬 검증 Neo4j에서:
docker compose exec neo4j cypher-shell -u neo4j -p citecgraph \
  "MATCH (c:Component) WHERE c.is_hub = true RETURN c.canonical_name ORDER BY c.canonical_name"
```
Expected: 1단계 REPORT.md 실측 상위권(`SCP`/`Network`/`Storage`/`OpenStack`/`Cluster`/
`Kubernetes`/`Firewall`/`VMware` 등)이 `true`로 나오는지 확인 — 설계 스펙 §3.1에서
지적한 대로 §6의 13종 하드코딩 목록과 실제 집계가 다름을 직접 확인하는 단계.

- [ ] **Step 4: MCP 스모크(Task 6 Step 3 재확인) + `docs/KNOWLEDGE_GRAPH_PRODUCTION_ROLLOUT.md` 갱신 필요 여부 검토**

이번 변경은 신규 엔드포인트/MCP 툴만 추가하고 기존 백필 절차는 바꾸지 않으므로
운영 반영 절차 자체(백필 순서·cron)는 그대로다 — 다만 `docs/
KNOWLEDGE_GRAPH_PRODUCTION_ROLLOUT.md`에 "백필 뒤 `recompute_hub_flags()`가 자동
실행됨"을 한 줄 추가할지는 다음 롤아웃 문서 갱신 때 반영(이 플랜의 범위 밖 — 별도
확인 필요 시 사용자에게 보고).
