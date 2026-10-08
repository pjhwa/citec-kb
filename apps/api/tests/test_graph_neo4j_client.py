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


def test_ensure_constraints_is_idempotent():
    client = _client()
    client.ensure_constraints()
    client.ensure_constraints()  # second call must not raise
    with client._driver.session() as session:
        names = {
            r["name"]
            for r in session.run("SHOW CONSTRAINTS YIELD name RETURN name")
        }
    assert len(names) >= 4  # at least the 4 constraints we create
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
    client.recompute_hub_flags()  # NOTE: this is the Task-1 method on the client, not the pipeline function

    result = client.explore("Document", "id", doc_id, max_hops=2)

    names = {n["canonical_name"] for n in result["components"]}
    assert hub_name not in names
    assert hub_name in result["excluded_hub_components"]
    client.close()


def test_explore_does_not_traverse_through_hub_pivot_at_hop2():
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    anchor_doc = f"test-anchor-{uuid.uuid4()}"
    hub_name = f"HubPivot-{uuid.uuid4()}"
    other_doc = f"test-other-{uuid.uuid4()}"

    # Create 5001 documents all pointing to hub_name to make it a hub
    for i in range(5001):
        client.merge_document(
            {"id": f"test-fan2-{hub_name}-{i}", "source_type": "tech_repo", "external_id": "x",
             "title": "t", "source_uri": None, "environment": None, "space_key": None,
             "priority_tier": 1},
            [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                  target_value=hub_name, tag="EXTRACTED")],
        )
    # Create anchor document pointing to hub_name (hop1)
    client.merge_document(
        {"id": anchor_doc, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=hub_name, tag="EXTRACTED")],
    )
    # Create other_doc also pointing to hub_name (would be hop2 from anchor_doc via hub pivot)
    client.merge_document(
        {"id": other_doc, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=hub_name, tag="EXTRACTED")],
    )
    client.recompute_hub_flags()

    result = client.explore("Document", "id", anchor_doc, max_hops=2)

    doc_ids = {d["id"] for d in result["documents"]}
    assert other_doc not in doc_ids  # hub pivot blocks the hop-2 path to other_doc
    assert hub_name in result["excluded_hub_components"]  # still reachable at hop1, just excluded as a hub
    client.close()


def test_is_component_hub_true_for_hub_component():
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    hub_name = f"HubSelf-{uuid.uuid4()}"
    for i in range(5001):
        client.merge_document(
            {"id": f"test-selfhub-{hub_name}-{i}", "source_type": "tech_repo", "external_id": "x",
             "title": "t", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
            [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                  target_value=hub_name, tag="EXTRACTED")],
        )
    client.recompute_hub_flags()

    assert client.is_component_hub(hub_name) is True
    client.close()


def test_is_component_hub_false_for_non_hub_component():
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    doc_id = f"test-doc-{uuid.uuid4()}"
    comp_name = f"NonHub-{uuid.uuid4()}"
    client.merge_document(
        {"id": doc_id, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    client.recompute_hub_flags()

    assert client.is_component_hub(comp_name) is False
    client.close()


def test_is_component_hub_none_for_nonexistent_component():
    client = _client()
    client.ensure_constraints()

    assert client.is_component_hub(f"DoesNotExist-{uuid.uuid4()}") is None
    client.close()
