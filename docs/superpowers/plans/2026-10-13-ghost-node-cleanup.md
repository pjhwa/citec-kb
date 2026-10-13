# 유령 Document 노드 정리(ghost_node_audit.py --execute) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scripts/verify/ghost_node_audit.py`에 안전한 `--execute` 삭제 경로를 추가해, 운영 Neo4j에 쌓인 78,493건의 고립 유령 `Document` 노드(G01 수정 이전 raw-id 스텁 잔존)를 사람이 직접 승인·실행할 수 있게 한다.

**Architecture:** 기존 읍기 전용 dry-run 쿼리에 `labels(d) = ['Document']` 가드를 추가하고, 판정 로직을 `_audit(driver)` 헬퍼로 뽑아 dry-run/execute가 공유한다. `--execute`는 sync_cli와 동일한 Postgres advisory lock을 먼저 잡고, 그 시점에 판정 쿼리를 다시 실행해 삭제 대상을 확정한 뒤, 삭제 직전 manifest를 저장하고 5,000건 배치로 `DETACH DELETE`한다. 배치별 트랜잭션 분리로 중간 실패 시 재실행이 idempotent하다.

**Tech Stack:** Python 3.12, `neo4j` 드라이버(raw, `Neo4jClient` 래퍼 미사용 — 기존 스크립트 스타일 유지), SQLAlchemy(`app.db.session.get_engine`, advisory lock용), pytest(스크래치 Neo4j+Postgres 대상 DB-touching 통합 테스트).

---

### 사전 준비: 스크래치 테스트 환경

구현 전에 격리된 Neo4j + Postgres를 띄운다(운영/dev 공유 인스턴스 절대 아님 — `docs/features/graph-evidence.md` "검증 방법" 절과 동일한 절차).

```bash
docker run -d --name citec-graph-test-neo4j -e NEO4J_AUTH=neo4j/citecgraph \
  -p 17687:7687 -p 17474:7474 neo4j:5.26-community

docker compose exec -T postgres psql -U citec -d postgres -c "CREATE DATABASE citec_graph_test;"
docker compose exec -T -e DATABASE_URL="postgresql+psycopg://citec:citec@postgres:5432/citec_graph_test" \
  api alembic upgrade head
```

아래 모든 `pytest`/`python` 실행은 이 환경변수를 export한 셸에서 실행한다(파일마다 반복 표기하지 않음):

```bash
cd apps/api
export GRAPH_TEST_DATABASE_URL="postgresql+psycopg://citec:citec@localhost:8574/citec_graph_test"
export GRAPH_NEO4J_TEST_URI="bolt://localhost:17687"
export GRAPH_NEO4J_TEST_USER=neo4j
export GRAPH_NEO4J_TEST_PASSWORD=citecgraph
export DATABASE_URL="$GRAPH_TEST_DATABASE_URL"
export NEO4J_URI="$GRAPH_NEO4J_TEST_URI"
export NEO4J_USERNAME="$GRAPH_NEO4J_TEST_USER"
export NEO4J_PASSWORD="$GRAPH_NEO4J_TEST_PASSWORD"
PY=/tmp/graph-ci-venv/bin/python
[ -x "$PY" ] || PY=python3
```

---

### Task 1: 판정 쿼리에 `labels()` 가드 추가 + 테스트 가능한 `_audit()` 헬퍼로 리팩터

**Files:**
- Modify: `scripts/verify/ghost_node_audit.py`
- Create: `apps/api/tests/test_ghost_node_audit_db.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_ghost_node_audit_db.py`를 새로 만든다. `scripts/verify/ghost_node_audit.py`는 `apps/api` 패키지 밖에 있으므로, 기존 저장소 관례(`test_confluence_map_source_seed_migration_db.py`가 alembic 마이그레이션 파일을 `importlib`로 직접 로드하는 방식)와 동일하게 `importlib.util`로 파일 경로에서 모듈을 로드한다.

```python
"""scripts/verify/ghost_node_audit.py --execute 경로의 DB-touching 테스트
(2026-10-13 유령 노드 정리 설계).

Skipped unless both GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI are set
-- 스크래치 Neo4j + Postgres에만 쓴다, 운영/dev 공유 인스턴스는 절대 안 됨.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest

_PG_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")
_NEO4J_URI = os.environ.get("GRAPH_NEO4J_TEST_URI")

pytestmark = pytest.mark.skipif(
    not (_PG_DSN and _NEO4J_URI),
    reason="set GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI to run these",
)

if _PG_DSN:
    os.environ["DATABASE_URL"] = _PG_DSN
if _NEO4J_URI:
    os.environ["NEO4J_URI"] = _NEO4J_URI
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"))


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


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "verify" / "ghost_node_audit.py"
)


def _load_ghost_audit_module():
    spec = importlib.util.spec_from_file_location("ghost_node_audit", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _driver():
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        _NEO4J_URI,
        auth=(os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )


def test_audit_excludes_multi_label_node_even_if_source_type_null_and_isolated():
    """labels() 가드 — Document 외 다른 label도 가진 노드는 source_type이
    NULL이고 관계가 0개라도 유령으로 치지 말아야 한다(실제로 다른 용도로
    쓰이는 노드를 잘못 지우는 사고를 막는 핵심 안전장치)."""
    mod = _load_ghost_audit_module()
    driver = _driver()
    true_ghost_id = f"ghost-{uuid.uuid4()}"
    multi_label_id = f"multi-{uuid.uuid4()}"
    try:
        with driver.session() as session:
            session.run("CREATE (:Document {id: $id})", id=true_ghost_id)
            session.run(
                "CREATE (:Document:Component {id: $id})", id=multi_label_id
            )

        result = mod._audit(driver)
        ids = {g["id"] for g in result["would_delete"]}
        assert true_ghost_id in ids
        assert multi_label_id not in ids
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (d:Document) WHERE d.id IN [$a, $b] DETACH DELETE d",
                a=true_ghost_id, b=multi_label_id,
            )
        driver.close()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

```bash
$PY -m pytest tests/test_ghost_node_audit_db.py -q
```
Expected: FAIL — `AttributeError: module 'ghost_node_audit' has no attribute '_audit'` (아직 `_audit()` 헬퍼가 없음).

- [ ] **Step 3: `scripts/verify/ghost_node_audit.py`에 `_audit()` 헬퍼 추가 + 쿼리에 `labels()` 가드 적용**

`_GHOST_QUERY`/`_COUNT_QUERY` 정의를 다음으로 교체한다:

```python
_GHOST_QUERY = """
MATCH (d:Document)
WHERE d.source_type IS NULL AND labels(d) = ['Document']
OPTIONAL MATCH (d)-[r]-()
WITH d, count(r) AS degree
WHERE degree = 0
RETURN d.id AS id, d.external_id AS external_id, d.title AS title
ORDER BY d.id
"""

_COUNT_QUERY = """
MATCH (d:Document)
WHERE d.source_type IS NULL AND labels(d) = ['Document']
OPTIONAL MATCH (d)-[r]-()
WITH d, count(r) AS degree
RETURN count(d) AS total_null_source_type,
       count(CASE WHEN degree = 0 THEN 1 END) AS isolated_count
"""
```

`main()` 바로 앞에 `_audit()` 헬퍼를 추가하고(판정 로직을 dry-run/execute가 공유하도록 뽑아냄), `main()` 본문에서 중복되던 쿼리 실행 블록을 이 헬퍼 호출로 교체한다:

```python
def _audit(driver) -> dict:
    with driver.session() as session:
        counts = session.run(_COUNT_QUERY).single()
        rows = list(session.run(_GHOST_QUERY))
    ghosts = [{"id": r["id"], "external_id": r["external_id"], "title": r["title"]} for r in rows]
    return {
        "total_null_source_type": counts["total_null_source_type"],
        "isolated_count": counts["isolated_count"],
        "would_delete_count": len(ghosts),
        "would_delete": ghosts,
    }
```

`main()`의 기존 본문 중 아래 블록:

```python
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            counts = session.run(_COUNT_QUERY).single()
            rows = list(session.run(_GHOST_QUERY))
    finally:
        driver.close()

    ghosts = [{"id": r["id"], "external_id": r["external_id"], "title": r["title"]} for r in rows]

    manifest = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "uri": uri,
        "total_null_source_type": counts["total_null_source_type"],
        "isolated_count": counts["isolated_count"],
        "would_delete_count": len(ghosts),
        "would_delete": ghosts,
        "restore_plan": (
```

를 아래로 교체한다(driver는 아직 안 닫음 — Task 2에서 `--execute` 분기가 이어서 씀):

```python
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        result = _audit(driver)
    finally:
        driver.close()

    manifest = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "uri": uri,
        "total_null_source_type": result["total_null_source_type"],
        "isolated_count": result["isolated_count"],
        "would_delete_count": result["would_delete_count"],
        "would_delete": result["would_delete"],
        "restore_plan": (
```

그 아래 `print(...)` 블록의 `counts['total_null_source_type']`/`counts['isolated_count']`도 `result[...]`로 바꾼다:

```python
    print(f"Document(source_type IS NULL) 총 {result['total_null_source_type']}건")
    print(f"그중 완전히 고립(관계 0개)된 유령: {result['isolated_count']}건")
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

```bash
$PY -m pytest tests/test_ghost_node_audit_db.py -q
```
Expected: PASS (1 passed)

- [ ] **Step 5: 기존 동작 회귀 확인 — dry-run이 여전히 정상 동작하는지**

```bash
cd /home/citec/dev/citec-kb
/tmp/graph-ci-venv/bin/python scripts/verify/ghost_node_audit.py --out /tmp/ghost_audit_task1.json 2>&1 | head -5
cd apps/api  # 다음 Step부터는 pytest를 계속 apps/api에서 돌리므로 원래 디렉터리로 복귀
```
(venv가 없으면 `python3`로 대체. 환경변수 `GRAPH_NEO4J_TEST_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD`는 위 "사전 준비"에서 이미 export됨)
Expected: "Document(source_type IS NULL) 총 0건" (스크래치 Neo4j는 비어있으므로 0건이 정상 — 에러 없이 끝나는지만 확인)

- [ ] **Step 6: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add scripts/verify/ghost_node_audit.py apps/api/tests/test_ghost_node_audit_db.py
git commit -m "fix(graph): ghost_node_audit — add labels() guard, extract testable _audit()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: advisory lock + 실시간 재조회 + 삭제 확인 게이트

**Files:**
- Modify: `scripts/verify/ghost_node_audit.py`
- Test: `apps/api/tests/test_ghost_node_audit_db.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_ghost_node_audit_db.py` 맨 아래에 추가:

```python
def test_execute_deletion_cancelled_without_exact_confirmation():
    """"delete" 외의 입력(또는 거부)이면 아무것도 지우지 않아야 한다."""
    mod = _load_ghost_audit_module()
    driver = _driver()
    node_id = f"cancel-{uuid.uuid4()}"
    try:
        with driver.session() as session:
            session.run("CREATE (:Document {id: $id})", id=node_id)

        result = mod._execute_deletion(driver, confirm=lambda _prompt: False)
        assert result == {"requested": 1, "deleted": 0, "cancelled": True}

        with driver.session() as session:
            count = session.run(
                "MATCH (d:Document {id: $id}) RETURN count(d) AS c", id=node_id
            ).single()["c"]
        assert count == 1
    finally:
        with driver.session() as session:
            session.run("MATCH (d:Document {id: $id}) DETACH DELETE d", id=node_id)
        driver.close()


def test_execute_deletion_deletes_only_true_ghosts_and_writes_manifest(tmp_path, monkeypatch):
    """confirm=True일 때 실제로 유령만 지우고, 정상 노드(관계 보유)는
    남겨야 한다. 삭제 manifest 파일도 tmp_path 아래에 남아야 한다."""
    mod = _load_ghost_audit_module()
    monkeypatch.chdir(tmp_path)
    driver = _driver()
    ghost_id = f"ghost-{uuid.uuid4()}"
    linked_id = f"linked-{uuid.uuid4()}"
    other_id = f"other-{uuid.uuid4()}"
    try:
        with driver.session() as session:
            session.run("CREATE (:Document {id: $id})", id=ghost_id)
            session.run(
                "CREATE (a:Document {id: $linked})-[:HAS_COMPONENT]->(:Component {canonical_name: $c})",
                linked=linked_id, c=f"comp-{linked_id}",
            )
            session.run("CREATE (:Document {id: $id, source_type: 'confluence_docs'})", id=other_id)

        result = mod._execute_deletion(driver, confirm=lambda _prompt: True)
        assert result["requested"] == 1
        assert result["deleted"] == 1
        assert "manifest" in result
        assert (tmp_path / result["manifest"]).exists()

        with driver.session() as session:
            remaining = {
                r["id"]
                for r in session.run(
                    "MATCH (d:Document) WHERE d.id IN [$g, $l, $o] RETURN d.id AS id",
                    g=ghost_id, l=linked_id, o=other_id,
                )
            }
        assert remaining == {linked_id, other_id}
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (d:Document) WHERE d.id IN [$g, $l, $o] "
                "OPTIONAL MATCH (d)-[r]-() DETACH DELETE d",
                g=ghost_id, l=linked_id, o=other_id,
            )
            session.run(
                f"MATCH (c:Component {{canonical_name: 'comp-{linked_id}'}}) DETACH DELETE c"
            )
        driver.close()


def test_execute_deletion_batch_failure_is_retry_safe(monkeypatch):
    """배치 중간 실패 시: 이미 처리된 배치는 커밋된 채 남고, 재시도하면
    idempotent하게 나머지만 지워져야 한다."""
    mod = _load_ghost_audit_module()
    monkeypatch.setattr(mod, "_DELETE_BATCH_SIZE", 2)
    driver = _driver()
    ids = [f"batch-{uuid.uuid4()}" for _ in range(4)]
    try:
        with driver.session() as session:
            for i in ids:
                session.run("CREATE (:Document {id: $id})", id=i)

        orig_delete_batch = mod._delete_batch
        calls = {"n": 0}

        def flaky(session, batch_ids):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated batch failure")
            return orig_delete_batch(session, batch_ids)

        monkeypatch.setattr(mod, "_delete_batch", flaky)
        with pytest.raises(RuntimeError):
            mod._execute_deletion(driver, confirm=lambda _prompt: True)

        with driver.session() as session:
            remaining = {
                r["id"]
                for r in session.run(
                    "MATCH (d:Document) WHERE d.id IN $ids RETURN d.id AS id", ids=ids
                )
            }
        assert remaining == set(ids[2:])  # first batch (ids[0:2]) already deleted

        monkeypatch.setattr(mod, "_delete_batch", orig_delete_batch)
        result = mod._execute_deletion(driver, confirm=lambda _prompt: True)
        assert result["deleted"] == 2  # only the remaining batch
    finally:
        with driver.session() as session:
            session.run("MATCH (d:Document) WHERE d.id IN $ids DETACH DELETE d", ids=ids)
        driver.close()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

```bash
$PY -m pytest tests/test_ghost_node_audit_db.py -q -k execute_deletion
```
Expected: FAIL — `AttributeError: module 'ghost_node_audit' has no attribute '_execute_deletion'`

- [ ] **Step 3: `scripts/verify/ghost_node_audit.py`에 삭제 실행 로직 추가**

파일 상단 import 블록에 `json`/`os`는 이미 있으므로 추가 import 없음. `_audit()` 헬퍼 바로 아래에 삽입:

```python
_DELETE_BATCH_SIZE = 5000


def _delete_batch(session, ids: list[str]) -> int:
    result = session.run(
        "UNWIND $ids AS id MATCH (d:Document {id: id}) "
        "WHERE d.source_type IS NULL AND labels(d) = ['Document'] "
        "DETACH DELETE d RETURN count(d) AS deleted",
        ids=ids,
    )
    return result.single()["deleted"]


def _execute_deletion(driver, *, confirm) -> dict:
    """락은 호출자(main())가 미리 잡아둔 상태에서 불린다. 과거 dry-run
    manifest는 신뢰하지 않고, 이 호출 시점에 판정 쿼리를 다시 실행해
    삭제 대상을 확정한다(TOCTOU 방지)."""
    result = _audit(driver)
    ids = [g["id"] for g in result["would_delete"]]
    if not ids:
        return {"requested": 0, "deleted": 0}

    prompt = f"{len(ids)}건을 삭제하시겠습니까? 되돌릴 수 없습니다. 'delete' 입력: "
    if not confirm(prompt):
        return {"requested": len(ids), "deleted": 0, "cancelled": True}

    manifest_path = (
        f"artifacts/ghost_node_deletion_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {"deleted_at": datetime.now(timezone.utc).isoformat(), "ids": ids},
            f, ensure_ascii=False, indent=2,
        )

    deleted_total = 0
    with driver.session() as session:
        for i in range(0, len(ids), _DELETE_BATCH_SIZE):
            batch = ids[i : i + _DELETE_BATCH_SIZE]
            deleted_total += _delete_batch(session, batch)

    return {"requested": len(ids), "deleted": deleted_total, "manifest": manifest_path}
```

`main()`의 argparse 블록에 플래그 추가:

```python
    p.add_argument(
        "--execute", action="store_true",
        help="dry-run이 아니라 실제로 삭제한다 — sync_cli와 같은 advisory lock을 "
             "먼저 잡고, 그 시점에 판정 쿼리를 다시 실행해 'delete' 확인을 받은 뒤 삭제한다.",
    )
```

`main()`에서 `driver = GraphDatabase.driver(...)` 직후, Task 1에서 만든 `result = _audit(driver)` 호출 블록보다 **앞에** `--execute` 분기를 넣는다 — dry-run 경로와 execute 경로를 완전히 분리한다:

```python
    driver = GraphDatabase.driver(uri, auth=(user, password))

    if args.execute:
        from app.db.session import get_engine
        from app.graph.sync_cli import _ADVISORY_LOCK_KEY

        lock_conn = get_engine().connect()
        got_lock = lock_conn.exec_driver_sql(
            "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)
        ).scalar()
        if not got_lock:
            lock_conn.close()
            driver.close()
            print("✗ sync_cli가 이미 같은 락을 쥐고 있습니다 — 삭제를 시도하지 않고 중단합니다", file=sys.stderr)
            print(json.dumps({"error": "lock_held_by_another_run"}), file=sys.stderr)
            return 1
        try:
            outcome = _execute_deletion(
                driver, confirm=lambda prompt: input(prompt).strip() == "delete"
            )
        finally:
            lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
            lock_conn.close()
            driver.close()
        print(json.dumps(outcome, ensure_ascii=False))
        if outcome.get("cancelled"):
            print("삭제를 취소했습니다.")
            return 0
        print(f"요청 {outcome['requested']}건 중 {outcome['deleted']}건 삭제 완료.")
        return 0

    try:
        result = _audit(driver)
    finally:
        driver.close()
```

(이 블록은 Task 1에서 만든 `try: result = _audit(driver) finally: driver.close()` 바로 앞에 삽입되는 것이고, 그 아래 `manifest = {...}` 이후 코드는 손대지 않는다.)

- [ ] **Step 4: 테스트 실행 → 통과 확인**

```bash
$PY -m pytest tests/test_ghost_node_audit_db.py -q
```
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add scripts/verify/ghost_node_audit.py apps/api/tests/test_ghost_node_audit_db.py
git commit -m "feat(graph): ghost_node_audit --execute — advisory lock + live re-check + batch delete

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: advisory lock 충돌(sync_cli와 동시 실행) CLI 레벨 테스트

**Files:**
- Test: `apps/api/tests/test_ghost_node_audit_db.py`

- [ ] **Step 1: 실패하는 테스트 작성**

맨 아래에 추가:

```python
def test_main_execute_blocked_when_sync_cli_lock_is_held(capsys):
    """sync_cli가 이미 advisory lock을 쥐고 있으면, --execute는 Neo4j
    판정 쿼리를 재실행하기 전에 즉시 중단해야 한다."""
    from app.db.session import get_engine
    from app.graph.sync_cli import _ADVISORY_LOCK_KEY

    mod = _load_ghost_audit_module()
    lock_conn = get_engine().connect()
    got = lock_conn.exec_driver_sql(
        "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)
    ).scalar()
    assert got is True
    try:
        rc = mod.main(["--execute"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "lock_held_by_another_run" in captured.err
    finally:
        lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
        lock_conn.close()
```

- [ ] **Step 2: 테스트 실행**

```bash
$PY -m pytest tests/test_ghost_node_audit_db.py -q -k lock_is_held
```
Expected: PASS 즉시 (Task 2에서 이미 구현된 로직을 검증하는 테스트이므로 바로 통과해야 함 — 만약 FAIL이면 Task 2의 `--execute` 분기 순서/임포트를 다시 확인).

- [ ] **Step 3: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add apps/api/tests/test_ghost_node_audit_db.py
git commit -m "test(graph): ghost_node_audit --execute concurrency-with-sync_cli coverage

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: 통합 테스트 러너에 등록 + 문서 갱신

**Files:**
- Modify: `scripts/verify/run_integration_tests.sh:16-23`
- Modify: `docs/features/graph-evidence.md`

- [ ] **Step 1: `run_integration_tests.sh`의 `INTEGRATION_FILES` 배열에 추가**

`scripts/verify/run_integration_tests.sh`의 기존 배열:

```bash
INTEGRATION_FILES=(
  tests/test_graph_pipeline_db.py
  tests/test_graph_neo4j_client.py
  tests/test_graph_hub_flags.py
  tests/test_graph_sync_state_db.py
  tests/test_graph_router.py
  tests/test_lexicon_seed_db.py
  tests/test_graph_sync_cli_db.py
)
```

아래로 교체(마지막에 한 줄 추가):

```bash
INTEGRATION_FILES=(
  tests/test_graph_pipeline_db.py
  tests/test_graph_neo4j_client.py
  tests/test_graph_hub_flags.py
  tests/test_graph_sync_state_db.py
  tests/test_graph_router.py
  tests/test_lexicon_seed_db.py
  tests/test_graph_sync_cli_db.py
  tests/test_ghost_node_audit_db.py
)
```

- [ ] **Step 2: 러너 전체 실행해서 신규 파일 포함 전체 통과 확인**

```bash
cd /home/citec/dev/citec-kb
GRAPH_VERIFY_PYTHON=/tmp/graph-ci-venv/bin/python \
GRAPH_TEST_DATABASE_URL="$GRAPH_TEST_DATABASE_URL" \
GRAPH_NEO4J_TEST_URI="$GRAPH_NEO4J_TEST_URI" \
GRAPH_NEO4J_TEST_USER="$GRAPH_NEO4J_TEST_USER" \
GRAPH_NEO4J_TEST_PASSWORD="$GRAPH_NEO4J_TEST_PASSWORD" \
  ./scripts/verify/run_integration_tests.sh
```
Expected: `✓ 모든 그래프 통합 테스트 파일 통과` (각 파일 블록에 `test_ghost_node_audit_db.py`도 나오고 전부 PASS)

- [ ] **Step 3: `docs/features/graph-evidence.md`에 구현 완료 기록 추가**

파일 맨 끝("## 최종 전체 재백필 결과 — 완료" 섹션 뒤)에 새 섹션을 추가:

```markdown

## 2026-10-13 유령 노드 정리 — 구현 완료(실행은 별도 승인)

운영 재백필 후 확인된 고립 유령 `Document` 노드 78,493건(`source_type IS
NULL`, 관계 0개 — G01 이전 raw-id 스텁 잔존, 샘플 검증 결과 `{id: ...}`
단 하나의 속성뿐인 빈 노드임을 확인)에 대해, 설계
(`docs/superpowers/specs/2026-10-13-ghost-node-cleanup-design.md`)와 구현
계획(`docs/superpowers/plans/2026-10-13-ghost-node-cleanup.md`)에 따라
`scripts/verify/ghost_node_audit.py --execute`를 추가했다:

- 판정 쿼리에 `labels(d) = ['Document']` 가드 추가(멀티 라벨 노드 과잉
  삭제 방지).
- `--execute`는 sync_cli와 동일한 Postgres advisory lock을 먼저 잡고,
  실패하면(sync_cli 실행 중) 즉시 중단.
- 락 획득 후 **그 시점에 판정 쿼리를 재실행**해 대상을 확정(과거 dry-run
  manifest는 신뢰하지 않음), `"delete"` 입력 확인, 삭제 직전 manifest를
  `artifacts/ghost_node_deletion_<timestamp>.json`에 저장, 5,000건 배치로
  `DETACH DELETE`(배치별 독립 트랜잭션 — 중단 시 재실행이 idempotent).
- dev scratch Neo4j+Postgres에서 `apps/api/tests/test_ghost_node_audit_db.py`
  (멀티 라벨 제외, 확인 취소, 정상 노드 보존, 배치 실패 후 재시도
  idempotency, sync_cli와의 동시 실행 차단)로 검증, 전부 통과.

**운영 실행은 이 세션에서 하지 않음** — 실행 전 전체 Neo4j 백업(기존
`prod_deploy_and_backfill.sh`의 볼륨 mountpoint tar 방식)을 사람이 별도로
뜬 뒤, `docker compose cp`로 최신 스크립트를 컨테이너에 올리고 사람이
직접 `--execute`를 실행·확인하는 것이 설계상 의도된 절차다.
```

- [ ] **Step 4: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add scripts/verify/run_integration_tests.sh docs/features/graph-evidence.md
git commit -m "docs(graph): register ghost_node_audit_db tests, record --execute implementation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: 스크래치 환경 정리

**Files:** 없음(인프라 정리만)

- [ ] **Step 1: 스크래치 컨테이너/DB 제거**

```bash
docker rm -f citec-graph-test-neo4j
docker compose exec -T postgres psql -U citec -d postgres -c "DROP DATABASE citec_graph_test;"
```

- [ ] **Step 2: 작업 디렉터리의 임시 artifacts 정리(커밋 안 된 파일만)**

```bash
cd /home/citec/dev/citec-kb
git status --short artifacts/ | grep '^??' || true
rm -f /tmp/ghost_audit_task1.json
```

이 Task는 커밋이 없다(정리 작업뿐).
