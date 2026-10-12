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

