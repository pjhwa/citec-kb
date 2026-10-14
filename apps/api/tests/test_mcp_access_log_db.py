"""DB-touching tests for app.audit.mcp_access (2026-10-14 admin dashboard --
MCP-origin query visibility).

Same opt-in-DB convention as test_confluence_sync_db.py.
"""

from __future__ import annotations

import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


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


def _cleanup():
    from sqlalchemy import delete

    from app.db.models import McpAccessLog
    from app.db.session import session_scope

    with session_scope() as session:
        session.execute(delete(McpAccessLog))


def test_mcp_header_request_is_logged_non_mcp_request_is_not():
    """X-Citec-Client: mcp-server가 있는 요청만 mcp_access_log에 남아야 하고,
    평범한 요청(헤더 없음)은 안 남아야 한다."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.models import McpAccessLog
    from app.db.session import session_scope

    _cleanup()
    try:
        client = TestClient(app)

        # 헤더 없는 보통 요청 — mcp_access_log에 안 남아야 함
        r1 = client.get("/v1/health")
        assert r1.status_code == 200

        # MCP 유래 요청(흉내) — 실제 kb_health가 호출하는 경로를 그대로 씀
        r2 = client.get(
            "/v1/health",
            headers={"X-Citec-Client": "mcp-server", "X-Citec-Tool": "kb_health"},
        )
        assert r2.status_code == 200

        with session_scope() as session:
            rows = session.query(McpAccessLog).all()
            assert len(rows) == 1
            assert rows[0].tool == "kb_health"
            assert rows[0].method == "GET"
            assert rows[0].path == "/v1/health"
            assert rows[0].status_code == 200
            assert rows[0].latency_ms is not None
    finally:
        _cleanup()


def test_mcp_header_request_summarizes_query_body():
    """POST 바디의 흔한 쿼리성 키("q" 등)가 query_summary에 요약돼야 한다 —
    바디 전체를 그대로 저장하지 않는다."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.models import McpAccessLog
    from app.db.session import session_scope

    _cleanup()
    try:
        client = TestClient(app)
        client.post(
            "/v1/search",
            json={"q": "GRO 패킷 드랍", "top_k": 5},
            headers={"X-Citec-Client": "mcp-server", "X-Citec-Tool": "kb_search"},
        )

        with session_scope() as session:
            rows = session.query(McpAccessLog).all()
            assert len(rows) == 1
            assert rows[0].tool == "kb_search"
            assert rows[0].path == "/v1/search"
            assert "GRO 패킷 드랍" in (rows[0].query_summary or "")
    finally:
        _cleanup()


def test_mcp_query_stats_aggregates_by_tool_and_lists_recent():
    from app.audit.mcp_access import mcp_query_stats
    from app.db.models import McpAccessLog
    from app.db.session import session_scope

    _cleanup()
    try:
        with session_scope() as session:
            session.add(McpAccessLog(tool="kb_search", method="POST", path="/v1/search",
                                      query_summary="q=x", status_code=200, latency_ms=50))
            session.add(McpAccessLog(tool="kb_search", method="POST", path="/v1/search",
                                      query_summary="q=y", status_code=200, latency_ms=60))
            session.add(McpAccessLog(tool="kb_ask", method="POST", path="/api/query",
                                      query_summary="q=z", status_code=200, latency_ms=900))

        with session_scope() as session:
            stats = mcp_query_stats(session)

        assert stats["count_24h"] == 3
        assert stats["by_tool_24h"]["kb_search"] == 2
        assert stats["by_tool_24h"]["kb_ask"] == 1
        assert len(stats["recent"]) == 3
    finally:
        _cleanup()
