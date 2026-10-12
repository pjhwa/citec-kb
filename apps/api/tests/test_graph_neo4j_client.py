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


def _doc(doc_id: str) -> dict:
    return {"id": doc_id, "source_type": "confluence_map", "external_id": "x", "title": "t",
            "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1}


def test_merge_document_parent_of_is_written_parent_to_child():
    """G02: Edge.target_value는 "내 부모"를 뜻하지만, Neo4j에는 실제로
    parent->child로 저장돼야 한다."""
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    parent_id = f"test-parent-{uuid.uuid4()}"
    child_id = f"test-child-{uuid.uuid4()}"
    client.merge_document(_doc(parent_id), [])
    client.merge_document(
        _doc(child_id),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=parent_id, tag="EXTRACTED")],
    )
    with client._driver.session() as session:
        forward = session.run(
            "MATCH (p:Document {id: $p})-[r:PARENT_OF]->(c:Document {id: $c}) RETURN count(r) AS n",
            p=parent_id, c=child_id,
        ).single()["n"]
        backward = session.run(
            "MATCH (c:Document {id: $c})-[r:PARENT_OF]->(p:Document {id: $p}) RETURN count(r) AS n",
            p=parent_id, c=child_id,
        ).single()["n"]
    assert forward == 1
    assert backward == 0
    client.close()


def test_merge_document_resync_replaces_owned_parent_of_edge():
    """G03: 자식이 다른 부모로 재동기화되면 옛 PARENT_OF가 남지 않아야 한다."""
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    old_parent = f"test-oldparent-{uuid.uuid4()}"
    new_parent = f"test-newparent-{uuid.uuid4()}"
    child_id = f"test-child-{uuid.uuid4()}"
    client.merge_document(_doc(old_parent), [])
    client.merge_document(_doc(new_parent), [])
    client.merge_document(
        _doc(child_id),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=old_parent, tag="EXTRACTED")],
    )
    client.merge_document(
        _doc(child_id),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=new_parent, tag="EXTRACTED")],
    )
    with client._driver.session() as session:
        old_count = session.run(
            "MATCH (:Document {id: $p})-[r:PARENT_OF]->(:Document {id: $c}) RETURN count(r) AS n",
            p=old_parent, c=child_id,
        ).single()["n"]
        new_count = session.run(
            "MATCH (:Document {id: $p})-[r:PARENT_OF]->(:Document {id: $c}) RETURN count(r) AS n",
            p=new_parent, c=child_id,
        ).single()["n"]
    assert old_count == 0
    assert new_count == 1
    client.close()


def test_merge_document_resync_removes_vanished_component_mention():
    """G03: 본문에서 사라진 컴포넌트 멘션의 edge가 재동기화 후 남지 않아야 한다."""
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    doc_id = f"test-doc-{uuid.uuid4()}"
    comp_name = f"TestComp-{uuid.uuid4()}"
    client.merge_document(
        _doc(doc_id),
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="INFERRED")],
    )
    client.merge_document(_doc(doc_id), [])  # 재추출 결과 더 이상 언급 없음
    with client._driver.session() as session:
        count = session.run(
            "MATCH (:Document {id: $id})-[r:HAS_COMPONENT]->(:Component {canonical_name: $c}) "
            "RETURN count(r) AS n",
            id=doc_id, c=comp_name,
        ).single()["n"]
    assert count == 0
    client.close()


def test_merge_document_parent_of_to_unresolved_page_does_not_create_document():
    """G01: 코퍼스에 없는 부모는 Document가 아니라 UnresolvedPage로 남아야 한다."""
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    child_id = f"test-child-{uuid.uuid4()}"
    raw_parent_id = f"raw-{uuid.uuid4()}"
    client.merge_document(
        _doc(child_id),
        [Edge(rel_type="PARENT_OF", target_label="UnresolvedPage", target_key="external_id",
              target_value=raw_parent_id, tag="EXTRACTED")],
    )
    with client._driver.session() as session:
        as_document = session.run(
            "MATCH (n:Document {id: $v}) RETURN count(n) AS n", v=raw_parent_id,
        ).single()["n"]
        as_unresolved = session.run(
            "MATCH (n:UnresolvedPage {external_id: $v})-[:PARENT_OF]->(:Document {id: $c}) "
            "RETURN count(n) AS n", v=raw_parent_id, c=child_id,
        ).single()["n"]
    assert as_document == 0
    assert as_unresolved == 1
    client.close()


def test_merge_document_resync_removes_legacy_unstamped_parent_of_ghost():
    """회귀 테스트 — G02 방향 수정 전 코드가 쓴 (child)-[:PARENT_OF]->(raw-id ghost)
    유령 edge는 extractor_version이 없다. 자식을 새 코드로 재동기화하면 그
    유령은 지워져야 하고, 자식이 다른 문서의 "정상 부모"로서 갖는 outgoing
    PARENT_OF(= 그 문서가 재동기화될 때 새로 찍힌 edge)는 보존돼야 한다."""
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    child_id = f"test-child-{uuid.uuid4()}"
    grandchild_id = f"test-grandchild-{uuid.uuid4()}"
    legacy_ghost_raw_id = f"legacy-raw-{uuid.uuid4()}"

    client.merge_document(_doc(child_id), [])
    # 이번 자식이 "정상 부모"로서 갖는 outgoing PARENT_OF — grandchild 재동기화가
    # 새 코드로 찍은 edge, 지워지면 안 된다.
    client.merge_document(
        _doc(grandchild_id),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=child_id, tag="EXTRACTED")],
        extractor_version="2026-10-12.1", generation="gen-X",
    )
    # 과거(수정 전) 코드가 직접 심은 유령 edge — extractor_version 없음.
    with client._driver.session() as session:
        session.run(
            "MATCH (c:Document {id: $child}) MERGE (g:Document {id: $ghost}) "
            "MERGE (c)-[r:PARENT_OF]->(g)",
            child=child_id, ghost=legacy_ghost_raw_id,
        )

    # child를 새 코드로 재동기화(부모 없음 — 최상위라고 가정)
    client.merge_document(_doc(child_id), [], extractor_version="2026-10-12.1", generation="gen-X")

    with client._driver.session() as session:
        legacy_gone = session.run(
            "MATCH (:Document {id: $child})-[r:PARENT_OF]->(:Document {id: $ghost}) RETURN count(r) AS n",
            child=child_id, ghost=legacy_ghost_raw_id,
        ).single()["n"]
        legit_preserved = session.run(
            "MATCH (:Document {id: $child})-[r:PARENT_OF]->(:Document {id: $grandchild}) RETURN count(r) AS n",
            grandchild=grandchild_id, child=child_id,
        ).single()["n"]
    assert legacy_gone == 0
    assert legit_preserved == 1
    client.close()


def test_explore_path_direction_and_structural_priority_over_competing_component_path():
    """G08(2차 리뷰) — explore()가 hop1/hop2에 방향("up"/"down")과 2hop의 전체
    경로(중간 노드 포함)를 돌려줘야 하고, 계층(PARENT_OF) 경로가 공유 Component
    를 통한 경쟁 경로보다 "표시용" 경로로 우선돼야 한다(알파벳 순이면
    HAS_COMPONENT가 이겨서 설명력이 떨어진다 — 2차 리뷰에서 지적)."""
    from app.graph.extract import Edge

    client = _client()
    client.ensure_constraints()
    suffix = uuid.uuid4().hex[:8]
    grandparent = f"test-gp-{suffix}"
    parent = f"test-parent-{suffix}"
    anchor = f"test-anchor-{suffix}"
    sibling = f"test-sibling-{suffix}"
    child = f"test-child-{suffix}"
    grandchild = f"test-grandchild-{suffix}"
    shared_comp = f"SharedComp-{suffix}"

    client.merge_document(_doc(grandparent), [])
    client.merge_document(
        _doc(parent),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=grandparent, tag="EXTRACTED")],
    )
    client.merge_document(
        _doc(anchor),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=parent, tag="EXTRACTED"),
         Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=shared_comp, tag="EXTRACTED")],
    )
    client.merge_document(
        _doc(sibling),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=parent, tag="EXTRACTED"),
         # 경쟁 경로: sibling도 anchor와 같은 Component를 가져 2hop
         # Document-Component-Document 경로가 동시에 존재한다.
         Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=shared_comp, tag="EXTRACTED")],
    )
    client.merge_document(
        _doc(child),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=anchor, tag="EXTRACTED")],
    )
    client.merge_document(
        _doc(grandchild),
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=child, tag="EXTRACTED")],
    )

    result = client.explore("Document", "id", anchor, max_hops=2)
    by_id = {d["id"]: d for d in result["documents"]}

    assert by_id[parent]["hops"] == 1
    assert by_id[parent]["relation"] == "PARENT_OF"
    assert by_id[parent]["route"] == [{"relation": "PARENT_OF", "tag": "EXTRACTED", "direction": "in"}]

    assert by_id[child]["hops"] == 1
    assert by_id[child]["route"] == [{"relation": "PARENT_OF", "tag": "EXTRACTED", "direction": "out"}]

    assert by_id[grandparent]["hops"] == 2
    assert by_id[grandparent]["relation"] == "PARENT_OF"
    assert [step["direction"] for step in by_id[grandparent]["route"]] == ["in", "in"]
    assert by_id[grandparent]["route"][0]["via"]["id"] == parent

    # 핵심 검증 — 경쟁 경로가 있어도 구조적 관계(PARENT_OF)가 표시용 경로로 이긴다.
    assert by_id[sibling]["hops"] == 2
    assert by_id[sibling]["relation"] == "PARENT_OF"
    assert [step["direction"] for step in by_id[sibling]["route"]] == ["in", "out"]
    assert by_id[sibling]["route"][0]["via"]["id"] == parent

    assert by_id[grandchild]["hops"] == 2
    assert by_id[grandchild]["relation"] == "PARENT_OF"
    assert [step["direction"] for step in by_id[grandchild]["route"]] == ["out", "out"]
    assert by_id[grandchild]["route"][0]["via"]["id"] == child

    client.close()
