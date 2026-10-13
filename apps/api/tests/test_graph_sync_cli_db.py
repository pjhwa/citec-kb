"""DB-touching tests for app.graph.sync_cli — advisory lock, run scope,
lexicon_version (R02, 2026-10-13 2nd handoff).

Skipped unless both GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI are set.
"""

from __future__ import annotations

import os
import uuid

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


def test_sync_cli_concurrent_run_blocked_by_advisory_lock(capsys):
    """R02 §7 — 동시 writer를 advisory lock으로 막는다. 락을 쥔 채로 main()을
    부르면 lock_held_by_another_run으로 즉시 실패해야 하고, graph_runs 행이
    전혀 생기지 않아야 한다(시작 전에 막혔으므로)."""
    from sqlalchemy import select
    from app.db.models import GraphRun
    from app.db.session import session_scope, get_engine
    from app.graph.sync_cli import _ADVISORY_LOCK_KEY, main

    lock_conn = get_engine().connect()
    try:
        got = lock_conn.exec_driver_sql(
            "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)
        ).scalar()
        assert got is True

        with session_scope() as session:
            before = session.scalar(select(GraphRun.run_id).order_by(GraphRun.started_at.desc()))

        rc = main(["--source-ids", "nonexistent-doc-id-for-lock-test"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "lock_held_by_another_run" in captured.err

        with session_scope() as session:
            after = session.scalar(select(GraphRun.run_id).order_by(GraphRun.started_at.desc()))
        assert after == before  # 새 run 행이 생기지 않음
    finally:
        lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
        lock_conn.close()


def test_sync_cli_main_records_partial_scope_with_source_ids(capsys):
    """R02 §5 — --source-ids 디버그 실행은 scope="partial"로 기록돼야
    한다(latest_run을 "방금 끝난 전체 스냅샷"으로 오인하지 않도록)."""
    from sqlalchemy import select
    from app.db.models import GraphRun
    from app.db.session import session_scope
    from app.graph.sync_cli import main

    main(["--source-ids", f"nonexistent-{uuid.uuid4()}", "--skip-hub-recompute"])
    # rc는 1이 된다(존재하지 않는 source-id라 documents.failed=1) — 이 테스트가
    # 보는 건 그게 아니라 scope/lexicon_version 기록 여부다.
    captured = capsys.readouterr()
    import json
    out = json.loads(captured.out.strip().splitlines()[-1])
    run_id = out["run_id"]

    with session_scope() as session:
        row = session.get(GraphRun, run_id)
        assert row.scope == "partial"
        assert row.lexicon_version is not None
