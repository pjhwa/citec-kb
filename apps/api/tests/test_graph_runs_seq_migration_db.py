"""DB-touching test for alembic migration 20261013_0014 (graph_runs.seq).

Skipped unless GRAPH_TEST_DATABASE_URL points at a reachable scratch
Postgres DB -- never the live citec_knowledge DB. This migration runs
alembic downgrade/upgrade directly against that DB, so it is destructive
to the graph_runs table's seq column for the duration of the test.

Advisor-flagged bug this guards against: a naive `ADD COLUMN seq BIGSERIAL`
numbers pre-existing rows in whatever physical heap-scan order Postgres
happens to produce, NOT in started_at/insertion order. On a table with
real history (prod), that can silently make an older run look "latest"
right after the migration runs -- the exact failure mode the seq column
was introduced to fix. This test seeds rows with explicit, intentionally
out-of-insertion-order started_at values, runs the real migration, and
asserts seq ends up matching chronological order regardless of insertion
order.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PG_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _PG_DSN, reason="set GRAPH_TEST_DATABASE_URL to a scratch Postgres DB to run this"
)

if _PG_DSN:
    os.environ["DATABASE_URL"] = _PG_DSN

_ALEMBIC_INI = str(Path(__file__).resolve().parents[1] / "alembic.ini")


def _alembic_config():
    from alembic.config import Config

    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("sqlalchemy.url", _PG_DSN)
    return cfg


def test_seq_backfill_matches_chronological_order_not_insertion_order():
    from alembic import command
    from sqlalchemy import create_engine, text

    cfg = _alembic_config()
    # downgrade 전에 head까지 올려둔다(스크래치 DB는 이미 head일 수도,
    # 이전 테스트 상태에 따라 다를 수도 있다 — 멱등하게 보정).
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "20261013_0013")

    engine = create_engine(_PG_DSN)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 의도적으로 "삽입 순서"와 "시간 순서"를 다르게 둔다: 먼저 넣는 행이
    # 시간상 가장 나중(t3)이고, 두 번째가 가장 먼저(t1)다. heap 스캔 순서
    # (삽입 순서와 보통 일치)로 번호를 매기면 seq가 시간 순서와 어긋난다.
    rows = [
        {"run_id": str(uuid.uuid4()), "started_at": base + timedelta(hours=3), "label": "third"},
        {"run_id": str(uuid.uuid4()), "started_at": base + timedelta(hours=1), "label": "first"},
        {"run_id": str(uuid.uuid4()), "started_at": base + timedelta(hours=2), "label": "second"},
    ]
    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text(
                    "INSERT INTO graph_runs (run_id, started_at, extractor_version, generation, status) "
                    "VALUES (:run_id, :started_at, 'v', :run_id, 'completed')"
                ),
                {"run_id": r["run_id"], "started_at": r["started_at"]},
            )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        seq_by_label = {}
        for r in rows:
            seq = conn.execute(
                text("SELECT seq FROM graph_runs WHERE run_id = :run_id"), {"run_id": r["run_id"]}
            ).scalar_one()
            seq_by_label[r["label"]] = seq

    assert seq_by_label["first"] < seq_by_label["second"] < seq_by_label["third"]

    # 멱등성: 마이그레이션 후 새로 추가되는 행은 백필된 최댓값보다 커야 한다.
    new_run_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO graph_runs (run_id, started_at, extractor_version, generation, status) "
                "VALUES (:run_id, now(), 'v', :run_id, 'completed')"
            ),
            {"run_id": new_run_id},
        )
    with engine.connect() as conn:
        new_seq = conn.execute(
            text("SELECT seq FROM graph_runs WHERE run_id = :run_id"), {"run_id": new_run_id}
        ).scalar_one()
    assert new_seq > max(seq_by_label.values())

    engine.dispose()
