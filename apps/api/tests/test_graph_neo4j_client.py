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
