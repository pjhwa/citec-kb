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
