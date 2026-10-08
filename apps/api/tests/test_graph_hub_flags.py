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
