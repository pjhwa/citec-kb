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


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """app.settings.get_settings()는 @lru_cache — 프로세스 안에서 먼저 실행된 다른
    테스트 모듈이 이미 캐시를 데워놨으면 위에서 os.environ에 설정한 DATABASE_URL/
    NEO4J_URI를 Settings()가 영영 못 본다. test_graph_pipeline_db.py의
    _clear_engine_cache와 동일한 이유로 필요."""
    from app.db import session as db_session
    from app.settings import get_settings
    from app.graph.neo4j_client import close_shared_client

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    close_shared_client()  # _build_client()의 공유 Neo4jClient(Fix 2)도 같은 이유로 리셋
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    close_shared_client()


def test_graph_status_reports_latest_run_and_generation_match():
    """G05 — GET /v1/graph/status가 가장 최근 graph_runs 행과 이 Neo4j의 실제
    generation 마커를 함께 보여주고, 둘이 같으면 generation_matches=True."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.models import GraphRun
    from app.db.session import session_scope

    generation = f"test-gen-{uuid.uuid4()}"
    run_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(GraphRun(
            run_id=run_id, extractor_version="test-version", generation=generation,
            documents_synced=5, documents_skipped=1, documents_failed=0,
            buckets_synced=1, buckets_failed=0, unresolved_references=2,
            hub_recompute_ok=True, status="completed",
        ))
    nclient = _neo4j_test_client()
    nclient.write_generation_marker(generation)
    nclient.close()

    client = TestClient(app)
    resp = client.get("/v1/graph/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_run"]["run_id"] == run_id
    assert body["latest_run"]["generation"] == generation
    assert body["latest_run"]["unresolved_references"] == 2
    assert body["neo4j_generation"] == generation
    assert body["generation_matches"] is True


def test_explore_unknown_failure_bucket_returns_404():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "failure_bucket", "anchor_value": f"missing-{uuid.uuid4()}"},
    )
    assert resp.status_code == 404


def test_shared_neo4j_client_survives_across_requests():
    """Fix 2 검증 — 요청 1회가 끝나도 공유 Neo4jClient가 닫히지 않아야 한다.
    driver 구성 횟수를 세어서 정확히 1회만 생성되었는지 확인한다
    (per-request close() 회귀 방지)."""
    import app.graph.neo4j_client as neo4j_client_module
    from fastapi.testclient import TestClient
    from app.main import app

    neo4j_client_module.close_shared_client()  # start from a clean slate
    construction_count = {"n": 0}
    real_driver_factory = neo4j_client_module.GraphDatabase.driver

    def counting_driver(*args, **kwargs):
        construction_count["n"] += 1
        return real_driver_factory(*args, **kwargs)

    neo4j_client_module.GraphDatabase.driver = counting_driver
    try:
        client = TestClient(app)
        r1 = client.post("/v1/graph/explore", json={"anchor_type": "failure_bucket", "anchor_value": f"nonexistent-{uuid.uuid4()}"})
        r2 = client.post("/v1/graph/explore", json={"anchor_type": "failure_bucket", "anchor_value": f"nonexistent-{uuid.uuid4()}"})
        assert r1.status_code == 404
        assert r2.status_code == 404
        assert construction_count["n"] == 1  # one driver built, reused across both requests
    finally:
        neo4j_client_module.GraphDatabase.driver = real_driver_factory
        neo4j_client_module.close_shared_client()


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


def _neo4j_test_client():
    from app.graph.neo4j_client import Neo4jClient

    return Neo4jClient(
        uri=_NEO4J_URI,
        user=os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"),
    )


def test_explore_component_anchor_returns_resolved_canonical_name():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    doc_id = f"test-doc-{uuid.uuid4()}"
    comp_name = f"TestComp-{uuid.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    nclient.merge_document(
        {"id": doc_id, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    nclient.close()

    client = TestClient(app)
    resp = client.post("/v1/graph/explore", json={"anchor_type": "component", "anchor_value": comp_name})
    assert resp.status_code == 200
    body = resp.json()
    assert body["anchor"]["resolved_id"] == comp_name
    doc_ids = {d["id"] for d in body["documents"]}
    assert doc_id in doc_ids


def test_explore_component_anchor_case_insensitive_fallback():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    doc_id = f"test-doc-{uuid.uuid4()}"
    comp_name = f"MixedCase-{uuid.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    nclient.merge_document(
        {"id": doc_id, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    nclient.close()

    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore", json={"anchor_type": "component", "anchor_value": comp_name.lower()}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["anchor"]["resolved_id"] == comp_name
    doc_ids = {d["id"] for d in body["documents"]}
    assert doc_id in doc_ids


def test_explore_dedups_documents_across_multiple_symptom_anchors():
    """symptom_text가 같은 문서를 참조하는 2개 이상의 component에 매칭될 때,
    병합된 결과에서 그 문서가 정확히 한 번만 나와야 한다(Fix 1)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    doc_id = f"test-doc-{uuid.uuid4()}"
    comp_a = f"netapp-{uuid.uuid4()}"
    comp_b = f"filer-{uuid.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    nclient.merge_document(
        {"id": doc_id, "source_type": "tech_repo", "external_id": "x", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [
            Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                 target_value=comp_a, tag="EXTRACTED"),
            Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                 target_value=comp_b, tag="EXTRACTED"),
        ],
    )
    nclient.close()

    # symptom_text 경로는 app.graph.extract.extract_lexicon_components를 쓰므로,
    # 두 component 모두 매칭되도록 extractor를 테스트 동안만 스텁으로 바꿔친다.
    import app.routers.graph as graph_router_module

    def fake_extract_lexicon_components(doc, lexicon_map):
        from app.graph.extract import Edge as ExtractEdge

        return [
            ExtractEdge(rel_type="HAS_COMPONENT", target_label="Component",
                        target_key="canonical_name", target_value=comp_a, tag="EXTRACTED"),
            ExtractEdge(rel_type="HAS_COMPONENT", target_label="Component",
                        target_key="canonical_name", target_value=comp_b, tag="EXTRACTED"),
        ]

    original = graph_router_module.extract_lexicon_components
    graph_router_module.extract_lexicon_components = fake_extract_lexicon_components
    try:
        client = TestClient(app)
        resp = client.post(
            "/v1/graph/explore",
            json={"anchor_type": "symptom_text", "anchor_value": "doesn't matter, extractor is stubbed"},
        )
    finally:
        graph_router_module.extract_lexicon_components = original

    assert resp.status_code == 200
    body = resp.json()
    doc_ids = [d["id"] for d in body["documents"] if d["id"] == doc_id]
    assert len(doc_ids) == 1


def test_explore_component_anchor_that_is_itself_a_hub_short_circuits():
    """스펙 §2 component 행: 앵커 자신이 is_hub=true면 2hop 순회를 생략하고 즉시
    excluded_hub_components만 돌려줘야 한다(Fix 1) — 토큰 낭비 방지."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    hub_name = f"HubAnchor-{uuid.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    for i in range(5001):
        nclient.merge_document(
            {"id": f"test-hubanchor-{hub_name}-{i}", "source_type": "tech_repo", "external_id": "x",
             "title": "t", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
            [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                  target_value=hub_name, tag="EXTRACTED")],
        )
    nclient.recompute_hub_flags()
    nclient.close()

    client = TestClient(app)
    resp = client.post("/v1/graph/explore", json={"anchor_type": "component", "anchor_value": hub_name})
    assert resp.status_code == 200
    body = resp.json()
    assert hub_name in body["excluded_hub_components"]
    assert body["documents"] == []
    assert body["components"] == []
    assert body["failure_buckets"] == []


def test_explore_excludes_document_by_page_id():
    """G04: exclude_page_ids에 걸린 문서는 candidate 단계에서 빠져야 한다."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    comp_name = f"ExclComp-{uuid.uuid4()}"
    excluded_doc_id = f"test-excl-{uuid.uuid4()}"
    excluded_page_id = str(uuid.uuid4())
    kept_doc_id = f"test-keep-{uuid.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    nclient.merge_document(
        {"id": excluded_doc_id, "source_type": "confluence_map", "external_id": excluded_page_id,
         "title": "t", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    nclient.merge_document(
        {"id": kept_doc_id, "source_type": "confluence_map", "external_id": "other",
         "title": "t", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    nclient.close()

    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "component", "anchor_value": comp_name, "exclude_page_ids": [excluded_page_id]},
    )
    assert resp.status_code == 200
    doc_ids = {d["id"] for d in resp.json()["documents"]}
    assert excluded_doc_id not in doc_ids
    assert kept_doc_id in doc_ids


def test_explore_document_anchor_itself_excluded_returns_filtered_status():
    """G04/G08 — anchor 문서 자신이 exclude_page_ids에 걸리면 그래프를 돌지
    않고 status="filtered"로 즉시 답해야 한다(found/not_found와 다른 신호)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.models import Document
    from app.db.session import session_scope

    anchor_external_id = str(uuid.uuid4())
    anchor_doc_id = f"test-filtered-{uuid.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    nclient.merge_document(
        {"id": anchor_doc_id, "source_type": "confluence_map", "external_id": anchor_external_id,
         "title": "t", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [],
    )
    nclient.close()
    with session_scope() as session:
        session.add(Document(
            id=anchor_doc_id, source_type="confluence_map", external_id=anchor_external_id,
            title="t", body_md="x", content_hash="h1", status="active", evidence_grade="A",
        ))

    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "document", "anchor_value": anchor_doc_id,
              "exclude_page_ids": [anchor_external_id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filtered"
    assert body["documents"] == []


def test_explore_bridge_block_hides_path_through_excluded_intermediate_page():
    """G04 — 2hop 경로가 exclude_page_ids에 걸린 중간 페이지를 거쳐야만
    나오는 후보는 결과에서 빠져야 한다(post-filter로는 못 잡는 케이스:
    중간 노드가 Document가 아니라 최종 후보 자체가 아니므로)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    comp_name = f"BridgeComp-{uuid.uuid4()}"
    bridge_page_id = str(uuid.uuid4())
    bridge_doc_id = f"test-bridge-{uuid.uuid4()}"
    anchor_bucket_id = f"test-anchor-fb-{uuid.uuid4()}"
    far_doc_id = f"test-far-{uuid.uuid4()}"

    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    # anchor(FailureBucket) -[:HAS_EVIDENCE]- bridge_doc -[:HAS_COMPONENT]- comp
    # far_doc도 같은 comp를 거쳐 2hop에 나오지만, 이 테스트의 관심사는
    # "bridge_doc을 거친 경로 자체가 제외되는지"다 — far_doc(다른 경로로도
    # 도달 가능한 노드)는 그대로 보여야 한다는 대조군으로 둔다.
    nclient.merge_document(
        {"id": bridge_doc_id, "source_type": "confluence_map", "external_id": bridge_page_id,
         "title": "bridge", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    nclient.merge_document(
        {"id": far_doc_id, "source_type": "confluence_map", "external_id": "far",
         "title": "far", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    nclient.merge_failure_bucket(
        {"id": anchor_bucket_id, "bucket_name": "b", "fb_domain": None, "protocol": None,
         "environment": None, "evidence_ref": None},
        [Edge(rel_type="HAS_EVIDENCE", target_label="Document", target_key="id",
              target_value=bridge_doc_id, tag="EXTRACTED")],
    )
    nclient.close()

    client = TestClient(app)
    # 제외 없이: bridge_doc을 거친 2hop으로 far_doc(Component를 공유)도 보여야 함
    resp_all = client.post("/v1/graph/explore", json={"anchor_type": "failure_bucket", "anchor_value": anchor_bucket_id})
    assert resp_all.status_code == 200
    comps_all = {c["canonical_name"] for c in resp_all.json()["components"]}
    assert comp_name in comps_all

    # bridge_page_id를 제외하면, bridge_doc을 "경로의 중간 노드"로 거쳐야만
    # 나오는 comp(2hop)가 더 이상 나오면 안 된다.
    resp_excl = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "failure_bucket", "anchor_value": anchor_bucket_id,
              "exclude_page_ids": [bridge_page_id]},
    )
    assert resp_excl.status_code == 200
    comps_excl = {c["canonical_name"] for c in resp_excl.json()["components"]}
    assert comp_name not in comps_excl


def test_explore_bridge_block_hides_path_through_excluded_subtree():
    """G04 잔여 — exclude_subtree_ids로의 중간 경로 bridge 차단. merge_document
    가 Document.ancestor_ids를 Neo4j 노드에 직접 써두므로(G04 잔여 해소),
    PG 메타데이터 없이도 Cypher 안에서 조상 트리로 중간 노드를 거를 수 있다."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    comp_name = f"SubtreeBridgeComp-{uuid.uuid4()}"
    excluded_root_id = str(uuid.uuid4())
    bridge_doc_id = f"test-subtree-bridge-{uuid.uuid4()}"
    anchor_bucket_id = f"test-subtree-anchor-fb-{uuid.uuid4()}"

    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    nclient.merge_document(
        {"id": bridge_doc_id, "source_type": "confluence_map", "external_id": str(uuid.uuid4()),
         "title": "bridge", "source_uri": None, "environment": None, "space_key": None,
         "priority_tier": 1, "ancestor_ids": [excluded_root_id]},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp_name, tag="EXTRACTED")],
    )
    nclient.merge_failure_bucket(
        {"id": anchor_bucket_id, "bucket_name": "b", "fb_domain": None, "protocol": None,
         "environment": None, "evidence_ref": None},
        [Edge(rel_type="HAS_EVIDENCE", target_label="Document", target_key="id",
              target_value=bridge_doc_id, tag="EXTRACTED")],
    )
    nclient.close()

    client = TestClient(app)
    resp_all = client.post("/v1/graph/explore", json={"anchor_type": "failure_bucket", "anchor_value": anchor_bucket_id})
    assert resp_all.status_code == 200
    assert comp_name in {c["canonical_name"] for c in resp_all.json()["components"]}

    resp_excl = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "failure_bucket", "anchor_value": anchor_bucket_id,
              "exclude_subtree_ids": [excluded_root_id]},
    )
    assert resp_excl.status_code == 200
    assert comp_name not in {c["canonical_name"] for c in resp_excl.json()["components"]}


def test_explore_neo4j_failure_returns_backend_error_status_not_503(monkeypatch):
    """G08 — Neo4j 조회 실패는 503로 요청 전체를 죽이는 대신, 기계가 구분
    가능한 status="backend_error"로 200 응답을 돌려줘야 한다(MCP가 body의
    status 필드로 판단하므로)."""
    from fastapi.testclient import TestClient
    from app.main import app
    import app.graph.neo4j_client as neo4j_client_module

    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated neo4j outage (test)")

    monkeypatch.setattr(neo4j_client_module.Neo4jClient, "explore", boom)
    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "failure_bucket", "anchor_value": f"whatever-{uuid.uuid4()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "backend_error"


def test_explore_document_anchor_fans_out_across_duplicate_external_id_copies():
    """G01: 같은 external_id를 가진 서로 다른 source_type 수집본 중 "대표"가
    아닌 쪽으로 앵커를 걸어도, 대표 쪽에 붙은 구조(PARENT_OF 등)를 찾아야 한다."""
    import uuid as uuid_mod
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    shared_external_id = str(uuid_mod.uuid4())
    representative_id = f"tech_repo:{shared_external_id}"  # tier 1 소스 — 대표로 뽑힘
    duplicate_id = f"confluence_map:{shared_external_id}"   # tier 3 소스 — 대표 아님
    child_id = f"test-child-{uuid_mod.uuid4()}"

    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    # 대표(tech_repo) 쪽에만 실제 PARENT_OF가 붙어 있다고 가정(=child의 부모로 해석된 쪽)
    nclient.merge_document(
        {"id": representative_id, "source_type": "tech_repo", "external_id": shared_external_id,
         "title": "대표", "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [],
    )
    nclient.merge_document(
        {"id": child_id, "source_type": "tech_repo", "external_id": "x", "title": "자식",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
              target_value=representative_id, tag="EXTRACTED")],
        extractor_version="2026-10-12.1", generation="gen-X",
    )
    nclient.close()

    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        session.add(Document(
            id=representative_id, source_type="tech_repo", external_id=shared_external_id,
            title="대표", body_md="x", content_hash="h1", status="active", evidence_grade="A",
        ))
        session.add(Document(
            id=duplicate_id, source_type="confluence_map", external_id=shared_external_id,
            title="사본", body_md="x", content_hash="h2", status="active", evidence_grade="C",
        ))

    client = TestClient(app)
    resp = client.post("/v1/graph/explore", json={"anchor_type": "document", "anchor_value": duplicate_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["anchor"]["resolved_id"] == representative_id
    doc_ids = {d["id"] for d in body["documents"]}
    assert child_id in doc_ids



def test_explore_document_anchor_excludes_its_own_fanout_copies_from_documents():
    """G01 fan-out 부작용 회귀 테스트 — 같은 external_id의 다른 수집본이
    "연관 문서"에 자기 자신(의 복제본)으로 나오면 안 된다."""
    import uuid as uuid_mod
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge
    from app.db.models import Document
    from app.db.session import session_scope

    shared_external_id = str(uuid_mod.uuid4())
    copy_a = f"tech_repo:{shared_external_id}"
    copy_b = f"confluence_map:{shared_external_id}"
    comp_name = f"SharedComp-{uuid_mod.uuid4()}"

    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    for copy_id, source in [(copy_a, "tech_repo"), (copy_b, "confluence_map")]:
        nclient.merge_document(
            {"id": copy_id, "source_type": source, "external_id": shared_external_id,
             "title": "같은 페이지", "source_uri": None, "environment": None, "space_key": None,
             "priority_tier": 1},
            [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                  target_value=comp_name, tag="EXTRACTED")],
            extractor_version="2026-10-12.2", generation="gen-X",
        )
    nclient.close()

    with session_scope() as session:
        session.add(Document(
            id=copy_a, source_type="tech_repo", external_id=shared_external_id,
            title="같은 페이지", body_md="x", content_hash="h1", status="active", evidence_grade="A",
        ))
        session.add(Document(
            id=copy_b, source_type="confluence_map", external_id=shared_external_id,
            title="같은 페이지", body_md="x", content_hash="h2", status="active", evidence_grade="C",
        ))

    client = TestClient(app)
    resp = client.post("/v1/graph/explore", json={"anchor_type": "document", "anchor_value": copy_a})
    assert resp.status_code == 200
    doc_ids = {d["id"] for d in resp.json()["documents"]}
    assert copy_a not in doc_ids
    assert copy_b not in doc_ids


def test_explore_document_anchor_with_null_external_id_does_not_fanout_to_everything():
    """2차 리뷰에서 지적: external_id가 NULL이면 Document.external_id == None이
    SQL의 IS NULL로 해석돼, external_id가 없는 모든 문서로 fan-out할 뻔했다.
    other_id는 Neo4j에 실제로 존재시켜, fan-out됐다면 200+other_id 관련 결과가
    나왔을 상황을 만든다 — 고쳐졌다면 anchor_id 단독 조회라 404여야 한다."""
    import uuid as uuid_mod
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.models import Document
    from app.db.session import session_scope

    anchor_id = f"test-noext-{uuid_mod.uuid4()}"
    other_id = f"test-noext-other-{uuid_mod.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    nclient.merge_document(
        {"id": other_id, "source_type": "confluence_map", "external_id": "unrelated", "title": "t",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [],
    )
    nclient.close()
    with session_scope() as session:
        # (source_type, external_id) 유니크 제약 때문에 같은 external_id=''를
        # 공유하려면 서로 다른 source_type이어야 한다 — 실제로 가능한 유일한 조합.
        # 이전 실행이 남긴 같은 (source_type, '') 행이 있으면 유니크 제약을
        # 깨므로, 재실행 가능하도록 먼저 지운다(테스트 데이터만, uuid 접두사로
        # 식별 가능한 이 테스트 전용 행이 아니라도 이 조합 자체가 운영 데이터에는
        # 나타나지 않는다 — 위 실제 코퍼스 조회에서 external_id='' 0건 확인됨).
        session.query(Document).filter(
            Document.source_type.in_(["tech_repo", "confluence_map"]), Document.external_id == ""
        ).delete(synchronize_session=False)
        session.add(Document(
            id=anchor_id, source_type="tech_repo", external_id='',
            title="앵커", body_md="x", content_hash="h1", status="active", evidence_grade="A",
        ))
        session.add(Document(
            id=other_id, source_type="confluence_map", external_id='',
            title="다른 문서(무관)", body_md="x", content_hash="h2", status="active", evidence_grade="A",
        ))

    client = TestClient(app)
    resp = client.post("/v1/graph/explore", json={"anchor_type": "document", "anchor_value": anchor_id})
    assert resp.status_code == 404  # 그래프에 없는 단일 앵커 — fan-out으로 other_id까지 끌려오면 안 됨


def test_explore_documents_offset_pages_beyond_cap():
    """G07: documents_offset으로 cap(50) 뒤의 후보에 접근할 수 있어야 한다."""
    import uuid as uuid_mod
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge

    comp_name = f"PageComp-{uuid_mod.uuid4()}"
    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    doc_ids = []
    for i in range(60):
        doc_id = f"test-page-{comp_name}-{i:03d}"
        doc_ids.append(doc_id)
        nclient.merge_document(
            {"id": doc_id, "source_type": "tech_repo", "external_id": "x", "title": "t",
             "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
            [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                  target_value=comp_name, tag="EXTRACTED")],
        )
    nclient.close()

    client = TestClient(app)
    page1 = client.post("/v1/graph/explore", json={"anchor_type": "component", "anchor_value": comp_name})
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["documents"]) == 50
    assert body1["documents_truncated"] is True
    assert body1["documents_total"] == 60

    page2 = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "component", "anchor_value": comp_name, "documents_offset": 50},
    )
    assert page2.status_code == 200
    body2 = page2.json()
    assert len(body2["documents"]) == 10
    assert body2["documents_truncated"] is False
    seen_ids = {d["id"] for d in body1["documents"]} | {d["id"] for d in body2["documents"]}
    assert seen_ids == set(doc_ids)


def test_explore_collapses_duplicate_page_copies_in_results():
    """G08 2차 리뷰 — 같은 Confluence 페이지를 confluence_map/tech_repo가 각각
    수집한 두 문서가 "연관 문서" 결과에 둘 다 나오면, 하나로 묶이고 나머지는
    copies에 담겨야 한다."""
    import uuid as uuid_mod
    from fastapi.testclient import TestClient
    from app.main import app
    from app.graph.extract import Edge
    from app.db.models import Document
    from app.db.session import session_scope

    comp_name = f"CollapseComp-{uuid_mod.uuid4()}"
    shared_external_id = str(uuid_mod.uuid4())
    copy_a = f"tech_repo:{shared_external_id}"
    copy_b = f"confluence_map:{shared_external_id}"

    nclient = _neo4j_test_client()
    nclient.ensure_constraints()
    for copy_id, source in [(copy_a, "tech_repo"), (copy_b, "confluence_map")]:
        nclient.merge_document(
            {"id": copy_id, "source_type": source, "external_id": shared_external_id,
             "title": "같은 페이지", "source_uri": None, "environment": None, "space_key": None,
             "priority_tier": 1},
            [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
                  target_value=comp_name, tag="EXTRACTED")],
        )
    nclient.close()

    with session_scope() as session:
        session.add(Document(
            id=copy_a, source_type="tech_repo", external_id=shared_external_id,
            title="같은 페이지", body_md="x", content_hash="h1", status="active", evidence_grade="A",
        ))
        session.add(Document(
            id=copy_b, source_type="confluence_map", external_id=shared_external_id,
            title="같은 페이지", body_md="x", content_hash="h2", status="active", evidence_grade="C",
        ))

    client = TestClient(app)
    resp = client.post("/v1/graph/explore", json={"anchor_type": "component", "anchor_value": comp_name})
    assert resp.status_code == 200
    docs = resp.json()["documents"]
    matching = [d for d in docs if d["id"] in (copy_a, copy_b)]
    assert len(matching) == 1  # 둘이 하나로 묶임
    assert matching[0]["id"] == copy_a  # tech_repo가 tier 1이라 대표
    assert matching[0]["copies"] == [copy_b]
    # attach_document_access가 실제 문서 경로(route — 그래프 2hop 경로와는
    # 다른 필드)로 올바르게 채워졌는지 확인(2차 리뷰에서 지적된 path 키
    # 충돌 회귀 테스트 — route와 "원문 접근 path"가 같은 이름을 쓰면 후자가
    # 전자로 덮어써진다).
    assert matching[0]["access"]["mcp_args"]["path"] == f"tech_repo/{shared_external_id}.md"
    assert f"path=tech_repo%2F{shared_external_id}" in matching[0]["web_url"]
    assert matching[0]["route"] == [{"relation": "HAS_COMPONENT", "tag": "EXTRACTED", "direction": "in"}]
