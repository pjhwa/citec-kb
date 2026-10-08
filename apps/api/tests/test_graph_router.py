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

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def test_explore_unknown_failure_bucket_returns_404():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/v1/graph/explore",
        json={"anchor_type": "failure_bucket", "anchor_value": f"missing-{uuid.uuid4()}"},
    )
    assert resp.status_code == 404


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
