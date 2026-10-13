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


def test_sync_document_resyncs_when_generation_changes():
    """G05: 같은 hash라도 generation이 바뀌면(=다른 Neo4j로 옮김) skip하지 않는다."""
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
        first = pipeline.sync_document(doc_id, client=client, generation="gen-A")
        assert first == "synced"
        same_gen = pipeline.sync_document(doc_id, client=client, generation="gen-A")
        assert same_gen == "skipped"
        new_gen = pipeline.sync_document(doc_id, client=client, generation="gen-B")
        assert new_gen == "synced"
    finally:
        client.close()


def test_sync_document_counts_state_write_failed_but_still_reports_synced(monkeypatch):
    """R02(2026-10-13 2차 핸드오프) §1 — mark_synced 실패는 그래프 쓰기 자체는
    성공했으므로 반환값은 여전히 "synced"지만, counters로 state_write_failed가
    집계돼야 한다(과거엔 조용히 삼켜져 어디에도 안 보였다)."""
    import app.graph.pipeline as pipeline
    import app.graph.sync_state as sync_state
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

    def boom_mark_synced(*args, **kwargs):
        raise RuntimeError("simulated state-write failure (test)")

    monkeypatch.setattr(sync_state, "mark_synced", boom_mark_synced)

    client = pipeline.build_neo4j_client(
        uri=_NEO4J_URI,
        user=os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"),
    )
    try:
        counters: dict[str, int] = {}
        result = pipeline.sync_document(doc_id, client=client, counters=counters)
        assert result == "synced"
        assert counters.get("state_write_failed") == 1
    finally:
        client.close()


def test_sync_document_retries_despite_matching_hash_when_last_error_set():
    """R04(2026-10-13 2차 핸드오프) §9 실제 재현 — mark_failed()는 input_hash/
    extractor_version/generation을 안 건드리므로, 성공 상태에서 input_hash가
    stale 그대로인 채 last_error만 남는 경우 재시도를 허용해야 한다."""
    import app.graph.pipeline as pipeline
    import app.graph.sync_state as sync_state
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
        first = pipeline.sync_document(doc_id, client=client, generation="gen-A")
        assert first == "synced"
        # 문서 내용은 그대로지만, 뭔가 실패했다는 last_error만 수동으로 남긴다
        # (mark_failed()가 실제로 하는 것과 동일 — input_hash/version/generation은
        # 그대로 두고 last_error만 설정).
        sync_state.mark_failed(doc_id, error="simulated unrelated failure (test)")
        retried = pipeline.sync_document(doc_id, client=client, generation="gen-A")
        assert retried == "synced"  # "skipped"면 버그 — last_error가 재시도를 막았다는 뜻
    finally:
        client.close()


def test_sync_document_resolves_direct_parent_via_page_identity_index():
    """G01/G02 end-to-end: TGW 재현 입력 — ancestor_ids가 raw page id 체인이고
    문서 id는 source-prefixed일 때, 직계 부모 1개만 올바르게 해석돼야 한다."""
    import app.graph.pipeline as pipeline
    from app.db.models import Document
    from app.db.session import session_scope

    parent_doc_id = f"confluence_map:{uuid.uuid4()}"
    parent_external_id = parent_doc_id.split(":", 1)[1]
    child_doc_id = f"confluence_map:{uuid.uuid4()}"
    child_external_id = child_doc_id.split(":", 1)[1]
    grandparent_raw_id = str(uuid.uuid4())  # 코퍼스에 없음 — unresolved

    with session_scope() as session:
        session.add(Document(
            id=parent_doc_id, source_type="confluence_map", external_id=parent_external_id,
            title="부모", body_md="x", content_hash="hp", status="active", evidence_grade="C",
        ))
        session.add(Document(
            id=child_doc_id, source_type="confluence_map", external_id=child_external_id,
            title="자식", body_md="x", content_hash="hc", status="active", evidence_grade="C",
            metadata_={"ancestor_ids": [grandparent_raw_id, parent_external_id, child_external_id]},
        ))

    client = pipeline.build_neo4j_client(
        uri=_NEO4J_URI,
        user=os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"),
    )
    try:
        page_identity_index = pipeline.build_page_identity_index()
        result = pipeline.sync_document(
            child_doc_id, client=client, page_identity_index=page_identity_index,
        )
        assert result == "synced"
        with client._driver.session() as session:
            direct_parent = session.run(
                "MATCH (p:Document {id: $p})-[r:PARENT_OF]->(c:Document {id: $c}) RETURN count(r) AS n",
                p=parent_doc_id, c=child_doc_id,
            ).single()["n"]
            no_grandparent_edge = session.run(
                "MATCH ()-[r:PARENT_OF]->(c:Document {id: $c}) RETURN count(r) AS n",
                c=child_doc_id,
            ).single()["n"]
            fake_document_for_raw_id = session.run(
                "MATCH (n:Document {id: $v}) RETURN count(n) AS n", v=grandparent_raw_id,
            ).single()["n"]
        assert direct_parent == 1
        assert no_grandparent_edge == 1  # 직계 부모 하나만 — 조상 전체 아님
        assert fake_document_for_raw_id == 0  # grandparent는 unresolved라 Document로 안 생김
    finally:
        client.close()


def test_sync_document_handles_deleted_document_without_raising():
    import app.graph.pipeline as pipeline
    from app.db.models import Document
    from app.db.session import session_scope

    doc_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(
            Document(
                id=doc_id, source_type="tech_repo", external_id=str(uuid.uuid4()),
                title="will be deleted", body_md="x", content_hash="h1",
            )
        )
    # 문서가 아예 없는 경우를 시뮬레이션 — sync_document가 예외 없이 "failed"를 반환해야 함
    with session_scope() as session:
        session.query(Document).filter(Document.id == doc_id).delete()

    client = pipeline.build_neo4j_client(
        uri=_NEO4J_URI,
        user=os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"),
    )
    try:
        result = pipeline.sync_document(doc_id, client=client)
        assert result == "failed"
    finally:
        client.close()
