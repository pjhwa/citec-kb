"""scripts/verify/ghost_node_audit.py --execute 경로의 DB-touching 테스트
(2026-10-13 유령 노드 정리 설계).

Skipped unless both GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI are set
-- 스크래치 Neo4j + Postgres에만 쓴다, 운영/dev 공유 인스턴스는 절대 안 됨.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest

_PG_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")
_NEO4J_URI = os.environ.get("GRAPH_NEO4J_TEST_URI")

pytestmark = pytest.mark.skipif(
    not (_PG_DSN and _NEO4J_URI),
    reason="set GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI to run these",
)

if _PG_DSN:
    os.environ["DATABASE_URL"] = _PG_DSN
if _NEO4J_URI:
    os.environ["NEO4J_URI"] = _NEO4J_URI
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"))


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


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "verify" / "ghost_node_audit.py"
)


def _load_ghost_audit_module():
    spec = importlib.util.spec_from_file_location("ghost_node_audit", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _driver():
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        _NEO4J_URI,
        auth=(os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )


def test_audit_excludes_multi_label_node_even_if_source_type_null_and_isolated():
    """labels() 가드 — Document 외 다른 label도 가진 노드는 source_type이
    NULL이고 관계가 0개라도 유령으로 치지 말아야 한다(실제로 다른 용도로
    쓰이는 노드를 잘못 지우는 사고를 막는 핵심 안전장치)."""
    mod = _load_ghost_audit_module()
    driver = _driver()
    true_ghost_id = f"ghost-{uuid.uuid4()}"
    multi_label_id = f"multi-{uuid.uuid4()}"
    try:
        with driver.session() as session:
            session.run("CREATE (:Document {id: $id})", id=true_ghost_id)
            session.run(
                "CREATE (:Document:Component {id: $id})", id=multi_label_id
            )

        result = mod._audit(driver)
        ids = {g["id"] for g in result["would_delete"]}
        assert true_ghost_id in ids
        assert multi_label_id not in ids
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (d:Document) WHERE d.id IN [$a, $b] DETACH DELETE d",
                a=true_ghost_id, b=multi_label_id,
            )
        driver.close()
