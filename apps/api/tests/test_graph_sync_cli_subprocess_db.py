"""Real-subprocess DB-touching tests for app.graph.sync_cli's advisory-lock
liveness story (F04, 2026-10-13 final handoff §2.4/§3.2 "V04 ... hard
kill/동시 writer/hub 실패는 미실행" -- this file is that missing piece).

Unlike test_graph_sync_cli_db.py (which calls sync_cli.main() in-process,
simulating "another writer" by holding the lock from a second connection),
these tests spawn `python -m app.graph.sync_cli` as a REAL OS subprocess
and actually SIGKILL it or race two real processes against each other --
the handoff explicitly asks for this ("실제 동시 실행으로 입증"), and an
in-process test can't demonstrate that a hard-killed process's connection
(and therefore its session-level advisory lock) is really released by
Postgres itself, independent of any cooperating Python code.

Skipped unless both GRAPH_TEST_DATABASE_URL and GRAPH_NEO4J_TEST_URI are
set, same convention as the rest of this test suite.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
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

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # apps/api


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


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _APP_DIR
    return env


def _seed_documents(n: int) -> list[str]:
    from app.db.models import Document
    from app.db.session import session_scope

    ids = [f"test-subproc-{uuid.uuid4()}" for _ in range(n)]
    with session_scope() as session:
        for doc_id in ids:
            session.add(Document(
                id=doc_id, source_type="tech_repo", external_id=str(uuid.uuid4()),
                title="subprocess test doc", body_md="x", content_hash=str(uuid.uuid4()),
            ))
    return ids


def _spawn_sync_cli(*extra_args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "app.graph.sync_cli", *extra_args],
        cwd=_APP_DIR, env=_subprocess_env(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _existing_run_ids() -> set[str]:
    """공유 스크래치 DB에는 다른 테스트 파일(예: test_graph_router.py의
    liveness 테스트들)이 정리하지 않고 남긴 status='running' 행이 있을 수
    있다 — 그런 leftover를 "우리 서브프로세스가 막 만든 행"으로 착각하지
    않도록, 스폰 직전 존재하는 run_id 전체를 스냅샷으로 떠둔다."""
    from sqlalchemy import select
    from app.db.models import GraphRun
    from app.db.session import session_scope

    with session_scope() as session:
        return set(session.scalars(select(GraphRun.run_id)).all())


def _poll_for_running_row(before_run_ids: set[str], timeout_s: float = 30.0):
    """graph_runs에 status='running'이면서 before_run_ids에 없던(= 지금
    막 생긴) 행이 나타날 때까지 기다렸다가 반환한다."""
    from sqlalchemy import select
    from app.db.models import GraphRun
    from app.db.session import session_scope

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with session_scope() as session:
            rows = session.scalars(
                select(GraphRun).where(GraphRun.status == "running").order_by(GraphRun.seq.desc())
            ).all()
            for row in rows:
                if row.run_id not in before_run_ids:
                    return {"run_id": row.run_id, "lock_holder_pid": row.lock_holder_pid}
        time.sleep(0.05)
    raise TimeoutError("no NEW 'running' graph_runs row appeared in time")


def test_hard_killed_subprocess_leaves_running_row_that_next_run_marks_abandoned():
    """실제 프로세스를 SIGKILL하고(정상 종료 경로를 전혀 안 거침), 그래도
    Postgres가 커넥션 종료를 감지해 advisory lock을 스스로 풀어주는지,
    그리고 다음 sync_cli가 락을 다시 잡으며 죽은 행을 'abandoned'로
    표시하는지 끝까지 실제로 확인한다."""
    from sqlalchemy import select, text
    from app.db.models import GraphRun
    from app.db.session import session_scope, get_engine

    doc_ids = _seed_documents(400)
    before_run_ids = _existing_run_ids()
    proc = _spawn_sync_cli()
    try:
        running = _poll_for_running_row(before_run_ids)
        killed_run_id = running["run_id"]
        pid = running["lock_holder_pid"]
        assert pid is not None

        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

        # Postgres가 죽은 백엔드의 커넥션 정리를 끝낼 시간을 약간 준다 —
        # 세션 수준 advisory lock은 커넥션 종료 즉시 풀리지만, pg_locks
        # 반영까지 아주 짧은 지연이 있을 수 있다.
        engine = get_engine()
        deadline = time.monotonic() + 10.0
        lock_released = False
        while time.monotonic() < deadline:
            with engine.connect() as conn:
                still_held = conn.execute(
                    text("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid = :pid"),
                    {"pid": pid},
                ).first()
            if still_held is None:
                lock_released = True
                break
            time.sleep(0.1)
        assert lock_released, "하드킬된 프로세스의 advisory lock이 Postgres에 의해 풀리지 않음"

        with session_scope() as session:
            killed_row = session.get(GraphRun, killed_run_id)
            assert killed_row.status == "running"  # 하드킬 직후엔 여전히 'running'으로 남아있어야 정상(이게 바로 문제 상황)

        # 다음 sync_cli가 락을 다시 잡고, 죽은 행을 abandoned로 표시하는지 확인.
        proc2 = _spawn_sync_cli("--source-ids", f"nonexistent-{uuid.uuid4()}", "--skip-hub-recompute")
        proc2.wait(timeout=30)

        with session_scope() as session:
            killed_row = session.get(GraphRun, killed_run_id)
            assert killed_row.status == "abandoned"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        with session_scope() as session:
            session.execute(
                text("DELETE FROM documents WHERE id = ANY(:ids)"), {"ids": doc_ids}
            )


def test_two_real_subprocesses_second_one_is_rejected_by_advisory_lock():
    """진짜 두 프로세스를 거의 동시에 띄워서, 하나만 락을 잡고 다른 하나는
    lock_held_by_another_run으로 즉시 거부되는지 확인한다(mock이나 같은
    프로세스 안의 커넥션이 아니라 실제 OS 프로세스 2개)."""
    doc_ids = _seed_documents(400)
    before_run_ids = _existing_run_ids()
    proc_a = _spawn_sync_cli()
    try:
        _poll_for_running_row(before_run_ids)  # proc_a가 확실히 락을 잡고 돌기 시작한 뒤에
        proc_b = _spawn_sync_cli("--source-ids", f"nonexistent-{uuid.uuid4()}", "--skip-hub-recompute")
        _, stderr_b = proc_b.communicate(timeout=30)
        assert proc_b.returncode == 1
        assert b"lock_held_by_another_run" in stderr_b
    finally:
        if proc_a.poll() is None:
            proc_a.wait(timeout=60)
        from app.db.session import session_scope
        from sqlalchemy import text
        with session_scope() as session:
            session.execute(
                text("DELETE FROM documents WHERE id = ANY(:ids)"), {"ids": doc_ids}
            )
