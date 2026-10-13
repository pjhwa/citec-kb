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


def test_sync_cli_main_records_lock_holder_pid_and_heartbeat():
    """F04(2026-10-13 최종 핸드오프) §3/4 — 새 run 행은 lock_holder_pid와
    초기 heartbeat_at을 가져야 한다(둘 다 나중에 '이 run이 살아있는지'를
    판정하는 근거가 된다)."""
    from sqlalchemy import select
    from app.db.models import GraphRun
    from app.db.session import session_scope
    from app.graph.sync_cli import main

    rc = main(["--source-ids", f"nonexistent-{uuid.uuid4()}", "--skip-hub-recompute"])
    assert rc == 1  # 존재하지 않는 source-id라 documents.failed=1 — 이 테스트가 보는 건 아래 필드들

    captured_run_id = None
    with session_scope() as session:
        row = session.scalar(select(GraphRun).order_by(GraphRun.seq.desc()))
        assert row.lock_holder_pid is not None
        assert row.heartbeat_at is not None
        captured_run_id = row.run_id
    assert captured_run_id is not None


def test_sync_cli_marks_prior_running_rows_abandoned_on_next_run():
    """F04 §3/4 — 하드킬 등으로 'running'에 영원히 남은 이전 행은, 락을
    새로 쥔 다음 sync_cli 실행이 시작되는 순간 'abandoned'로 바뀌어야
    한다(그 시점에 락을 쥔 프로세스는 자신뿐이므로, 아직 running인 다른
    행은 전부 죽은 게 확실하다)."""
    import uuid as uuid_module
    from sqlalchemy import select
    from app.db.models import GraphRun
    from app.db.session import session_scope
    from app.graph.sync_cli import main

    stale_run_id = str(uuid_module.uuid4())
    with session_scope() as session:
        session.add(GraphRun(
            run_id=stale_run_id, extractor_version="v", generation="stale-gen",
            scope="full", status="running",
        ))

    main(["--source-ids", f"nonexistent-{uuid.uuid4()}", "--skip-hub-recompute"])

    with session_scope() as session:
        stale_row = session.get(GraphRun, stale_run_id)
        assert stale_row.status == "abandoned"


def test_heartbeat_raises_when_lock_connection_is_terminated():
    """F04(2026-10-13 최종 핸드오프) §5 — fencing 직접 확인: lock_conn의
    백엔드가 중간에 죽으면(DB 재시작, 관리자의 pg_terminate_backend 등),
    _heartbeat()가 조용히 넘어가지 않고 그대로 예외를 던져야 한다 — 이게
    실제로 main()의 예외 처리 경로를 타서 run을 중단시키는 근거다."""
    import uuid as uuid_module
    import pytest as pytest_module
    from app.db.session import session_scope, get_engine
    from app.db.models import GraphRun
    from app.graph.sync_cli import _ADVISORY_LOCK_KEY, _heartbeat

    run_id = str(uuid_module.uuid4())
    with session_scope() as session:
        session.add(GraphRun(
            run_id=run_id, extractor_version="v", generation="g", scope="full", status="running",
        ))

    lock_conn = get_engine().connect()
    got = lock_conn.exec_driver_sql(
        "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)
    ).scalar()
    assert got is True
    victim_pid = lock_conn.exec_driver_sql("SELECT pg_backend_pid()").scalar()

    # 다른 커넥션에서 그 백엔드를 강제 종료 — "프로세스는 살아있는데 DB
    # 커넥션만 끊긴" 상황을 정확히 재현한다(하드킬 서브프로세스 테스트와는
    # 다른 각도: 거긴 프로세스 전체가 죽는 경우).
    killer = get_engine().connect()
    try:
        killer.exec_driver_sql("SELECT pg_terminate_backend(%s)", (victim_pid,))
        killer.commit()
    finally:
        killer.close()

    try:
        with pytest_module.raises(Exception):
            _heartbeat(lock_conn, run_id)
    finally:
        # status='running'인 채로 남겨두면 다른 테스트(서브프로세스
        # 하드킬 테스트의 "지금 막 생긴 running 행 기다리기")가 이
        # leftover를 자기 것으로 착각할 수 있다.
        with session_scope() as session:
            session.query(GraphRun).filter_by(run_id=run_id).delete()
