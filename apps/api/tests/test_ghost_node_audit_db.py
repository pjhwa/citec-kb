"""scripts/verify/ghost_node_audit.py --execute 경로의 DB-touching 테스트
(2026-10-13 유령 노드 정리 설계).

Skipped unless both GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI are set
-- 스크래치 Neo4j + Postgres에만 쓴다, 운영/dev 공유 인스턴스는 절대 안 됨.
"""

from __future__ import annotations

import importlib.util
import json
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
        auth=(os.environ.get("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
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


def test_execute_deletion_cancelled_without_exact_confirmation():
    """"delete" 외의 입력(또는 거부)이면 아무것도 지우지 않아야 한다."""
    mod = _load_ghost_audit_module()
    driver = _driver()
    node_id = f"cancel-{uuid.uuid4()}"
    try:
        with driver.session() as session:
            session.run("CREATE (:Document {id: $id})", id=node_id)

        result = mod._execute_deletion(driver, confirm=lambda _prompt: False)
        # requested는 공유 스크래치 Neo4j에 다른 테스트 파일이 남긴 고립
        # 노드가 섞여 있으면 1보다 커질 수 있다(이 테스트가 심은 노드 외에
        # 다른 진짜 유령도 같이 집계되므로) — 그래도 >=1이어야 하고, 취소
        # 경로이므로 deleted/cancelled는 항상 고정값이어야 한다.
        assert result["requested"] >= 1
        assert result["deleted"] == 0
        assert result["cancelled"] is True

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
        # requested/deleted는 공유 스크래치 Neo4j에 다른 테스트 파일이 남긴
        # 고립 노드가 섞여 있으면 1보다 커질 수 있다 — 핵심 보장(유령만
        # 지우고 정상 노드는 남긴다)은 아래 manifest 내용과 remaining 조회로
        # 이 테스트가 심은 id 단위로 직접 확인한다.
        assert result["requested"] >= 1
        assert result["deleted"] >= 1
        assert "manifest" in result
        manifest_file = tmp_path / result["manifest"]
        assert manifest_file.exists()
        deleted_ids = set(json.loads(manifest_file.read_text())["ids"])
        assert ghost_id in deleted_ids
        assert linked_id not in deleted_ids
        assert other_id not in deleted_ids

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


def test_execute_deletion_batch_failure_is_retry_safe(tmp_path, monkeypatch):
    """배치 중간 실패 시: 이미 처리된 배치는 커밋된 채 남고, 재시도하면
    idempotent하게 나머지만 지워져야 한다.

    _audit()의 _GHOST_QUERY는 d.id로 ORDER BY 하므로(Task 1), 배치는 생성
    순서가 아니라 id 문자열의 사전순으로 나뉜다. 따라서 이 ids 리스트도
    사전순으로 미리 정렬해, "앞의 2개가 첫 배치"라는 테스트의 전제가 실제
    배치 순서와 항상 일치하도록 한다(그렇지 않으면 1/6 확률로만 통과하는
    flaky 테스트가 된다)."""
    mod = _load_ghost_audit_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "_DELETE_BATCH_SIZE", 2)
    driver = _driver()
    ids = sorted(f"batch-{uuid.uuid4()}" for _ in range(4))
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


def test_main_execute_blocked_when_sync_cli_lock_is_held(capsys, monkeypatch):
    """sync_cli가 이미 advisory lock을 쥐고 있으면, --execute는 Neo4j
    판정 쿼리를 재실행하기 전에 즉시 중단해야 한다."""
    import neo4j
    from app.db.session import get_engine
    from app.graph.sync_cli import _ADVISORY_LOCK_KEY

    class _FakeDriver:
        def __init__(self):
            self.sessions = 0
            self.closed = False
        def session(self, *a, **k):
            self.sessions += 1
            raise AssertionError("Neo4j session opened before lock-check abort")
        def close(self):
            self.closed = True

    fake = _FakeDriver()
    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: fake)

    mod = _load_ghost_audit_module()
    lock_conn = get_engine().connect()
    try:
        got = lock_conn.exec_driver_sql(
            "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)
        ).scalar()
        assert got is True
        rc = mod.main(["--execute"])
        assert rc == 1
        assert "lock_held_by_another_run" in capsys.readouterr().err
        assert fake.sessions == 0   # proves _audit's Neo4j query never ran
        assert fake.closed is True  # proves driver cleanup on the abort path ran
    finally:
        try:
            lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
        finally:
            lock_conn.close()
