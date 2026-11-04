"""A — retired / test failure buckets disappear from /v1/graph/explore neighbors (real Neo4j + PG).

Needs GRAPH_NEO4J_TEST_URI + GRAPH_TEST_DATABASE_URL (see test_graph_router.py).
"""

from __future__ import annotations

import os
import uuid

import pytest

_NEO4J_URI = os.environ.get("GRAPH_NEO4J_TEST_URI")
_PG = os.environ.get("GRAPH_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not (_NEO4J_URI and _PG), reason="set GRAPH_NEO4J_TEST_URI and GRAPH_TEST_DATABASE_URL to run these"
)
if _PG:
    os.environ["DATABASE_URL"] = _PG
if _NEO4J_URI:
    os.environ["NEO4J_URI"] = _NEO4J_URI


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from app.db import session as db_session
    from app.graph.neo4j_client import close_shared_client
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    close_shared_client()
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kw: {"embedded": 0})
    yield
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("delete from documents where id in (select document_id from failure_buckets where created_by like 'grphpy-%')"))
        s.execute(text("delete from failure_buckets where created_by like 'grphpy-%'"))
    close_shared_client()


def _neo():
    from app.graph.neo4j_client import Neo4jClient

    return Neo4jClient(
        uri=_NEO4J_URI,
        user=os.environ.get("GRAPH_NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("GRAPH_NEO4J_TEST_PASSWORD", "citecgraph"),
    )


def _seed(created_by: str):
    """Register a real PG bucket and mirror it into Neo4j behind a bridge doc of a component."""
    from app.failure_buckets.service import create_bucket
    from app.graph.extract import Edge

    tag = uuid.uuid4().hex[:8]
    b = create_bucket(
        bucket_name=f"graph-retire {tag}", fb_domain="network", symptom=f"zzgr{tag}",
        discriminating_signals=[f"zzgr{tag} sig"], root_cause="rc", recommended_action="ra",
        evidence_ref="confluence:1", created_by=created_by,
    )
    comp = f"GrphRetireComp-{tag}"
    bridge = f"test-grph-bridge-{tag}"
    n = _neo()
    n.ensure_constraints()
    n.merge_document(
        {"id": bridge, "source_type": "confluence_map", "external_id": str(uuid.uuid4()), "title": "bridge",
         "source_uri": None, "environment": None, "space_key": None, "priority_tier": 1},
        [Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
              target_value=comp, tag="EXTRACTED")],
    )
    n.merge_failure_bucket(
        {"id": b["id"], "bucket_name": b["bucket_name"], "fb_domain": "network", "protocol": None,
         "environment": None, "evidence_ref": "confluence:1"},
        [Edge(rel_type="HAS_EVIDENCE", target_label="Document", target_key="id",
              target_value=bridge, tag="EXTRACTED")],
    )
    n.close()
    return b["id"], comp


def _neighbors(comp):
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).post("/v1/graph/explore", json={"anchor_type": "component", "anchor_value": comp})
    assert r.status_code == 200, r.text
    return {b["id"] for b in r.json()["failure_buckets"]}


def test_retired_bucket_is_hidden_from_graph_neighbors_and_restore_shows_it():
    from app.failure_buckets.service import restore_bucket, retire_bucket

    bid, comp = _seed("grphpy-sys@v1")
    assert bid in _neighbors(comp)
    retire_bucket(bid, reason="그래프 이웃 제외 확인", retired_by="pytest")
    assert bid not in _neighbors(comp)
    restore_bucket(bid)
    assert bid in _neighbors(comp)


def test_test_registration_is_hidden_from_graph_neighbors():
    bid, comp = _seed("grphpy-sys@v1-test")
    assert bid not in _neighbors(comp)
