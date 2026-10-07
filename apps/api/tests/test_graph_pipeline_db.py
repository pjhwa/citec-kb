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
