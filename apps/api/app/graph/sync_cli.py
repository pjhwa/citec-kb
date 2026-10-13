"""CLI: python -m app.graph.sync_cli — scripts/graph_sync.sh가 컨테이너 안에서 호출."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.db.models import Document, FailureBucket, GraphRun
from app.db.session import session_scope
from app.graph.pipeline import (
    EXTRACTOR_VERSION,
    aggregate_evidence_reasons,
    build_external_id_index,
    build_neo4j_client,
    build_page_identity_index,
    recompute_hub_flags,
    sync_document,
    sync_failure_bucket,
)

logger = logging.getLogger("citec.graph.sync_cli")


_ADVISORY_LOCK_KEY = 0x63697465635F6772  # "citec_gr"(앞 8바이트)의 임의 고정값 — pg_advisory_lock은 bigint 1개만 받는다

def _start_run(
    *, generation: str, scope: str, lexicon_version: str | None, lock_holder_pid: int | None = None,
) -> str:
    run_id = str(uuid4())
    with session_scope() as session:
        session.add(GraphRun(
            run_id=run_id, extractor_version=EXTRACTOR_VERSION, generation=generation,
            scope=scope, lexicon_version=lexicon_version,
            lock_holder_pid=lock_holder_pid, heartbeat_at=datetime.now(timezone.utc),
        ))
    return run_id


def _release_lock(lock_conn) -> None:
    try:
        lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
    finally:
        lock_conn.close()


def _heartbeat(lock_conn, run_id: str) -> None:
    """F04(2026-10-13 최종 핸드오프) §3/4 — lock_conn(advisory lock을 쥔 그
    커넥션)으로 직접 쓴다. 이 커넥션이 이미 죽었으면(DB 재시작, 네트워크
    끊김 등) 여기서 예외가 나는데, 그건 "더 이상 락을 보장할 수 없다"는
    뜻이므로 조용히 삼키지 않고 그대로 올려서 run을 중단시킨다(fencing —
    락 없이 계속 Neo4j에 쓰는 사고를 막는다). lock_conn으로 직접 커밋해야
    다른 세션(GET /v1/graph/status)이 이 값을 바로 볼 수 있다."""
    lock_conn.exec_driver_sql(
        "UPDATE graph_runs SET heartbeat_at = now() WHERE run_id = %s", (run_id,)
    )
    lock_conn.commit()


def _lexicon_version() -> str | None:
    """R02(2026-10-13 2차 핸드오프) §10 — graph_runs.lexicon_version이 한 번도
    채워진 적이 없었다. app.lexicon.seed의 캐시 지문과 같은 (count, max(updated_at))
    문자열을 재사용해 "이 run이 어느 lexicon 상태로 돌았는지"를 남긴다."""
    try:
        from sqlalchemy import func as sa_func
        from app.db.models import LexiconTerm
        with session_scope() as session:
            count, max_updated = session.execute(
                select(sa_func.count(), sa_func.max(LexiconTerm.updated_at))
            ).one()
        return f"{count}:{max_updated}"
    except Exception:  # noqa: BLE001 — lexicon_version은 참고 정보, 못 구해도 배치는 계속
        logger.exception("failed to compute lexicon_version")
        return None


def _finish_run(run_id: str, *, status: str, **counts) -> None:
    with session_scope() as session:
        row = session.get(GraphRun, run_id)
        if row is None:
            return
        row.completed_at = datetime.now(timezone.utc)
        row.status = status
        for key, value in counts.items():
            setattr(row, key, value)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="대상 건수만 세고 종료")
    p.add_argument("--source-ids", help="쉼표구분 document_id만 처리(디버그용)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--skip-hub-recompute", action="store_true",
        help="Component.is_hub 재집계 생략(디버그/부분 실행용)",
    )
    p.add_argument(
        "--generation", default=os.environ.get("GRAPH_GENERATION"),
        help="그래프 세대 식별자(G05) — 명시하면 이 Neo4j의 GraphMeta 마커에도 "
             "덮어쓴다. 생략하면(및 $GRAPH_GENERATION 미설정) 이 Neo4j에 이미 "
             "찍힌 마커를 읽어 쓴다 — 마커가 없으면(최초 1회) 새로 만든다. "
             "즉 --generation을 깜빡 안 바꿔도 '다른 Neo4j로 옮겼는데 과거 "
             "generation으로 착각해 전부 skip'되는 사고(1차 리뷰에서 지적)가 "
             "구조적으로 안 난다 — 판단 기준이 사람의 플래그가 아니라 "
             "Neo4j 자신이 들고 있는 값이기 때문이다.",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    with session_scope() as session:
        doc_ids = (
            [s.strip() for s in args.source_ids.split(",") if s.strip()]
            if args.source_ids
            else [row[0] for row in session.execute(select(Document.id)).all()]
        )
        bucket_ids = [row[0] for row in session.execute(select(FailureBucket.id)).all()]

    if args.dry_run:
        print(json.dumps({"documents": len(doc_ids), "failure_buckets": len(bucket_ids)}))
        return 0

    # R02(2026-10-13 2차 핸드오프) §7 — 동시 writer를 막는 최소 장치. 세션
    # 수준 advisory lock은 "어느 커넥션이 쥐고 있는지"가 중요한데, ORM의
    # session_scope()는 풀링된 커넥션을 반환/재사용하므로 거기서 잡으면
    # 커넥션이 풀로 돌아간 뒤에도 잠금이 남을 수 있다 — 그래서 이 run이
    # 끝날 때까지 쥐고 있을 전용 커넥션을 직접 열고, 끝날 때 같은 커넥션에서
    # 명시적으로 푼다. 프로세스가 kill -9로 죽어도 Postgres가 그 커넥션
    # 종료를 감지해 자동으로 풀어준다(stale 상태가 안 남음).
    from app.db.session import get_engine

    lock_conn = get_engine().connect()
    got_lock = lock_conn.exec_driver_sql(
        "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)
    ).scalar()
    if not got_lock:
        lock_conn.close()
        logger.error("another sync_cli run already holds the graph sync lock — aborting")
        print(json.dumps({"error": "lock_held_by_another_run"}), file=sys.stderr)
        return 1

    lock_holder_pid = lock_conn.exec_driver_sql("SELECT pg_backend_pid()").scalar()
    # F04(2026-10-13 최종 핸드오프) §3/4 — 이 락을 쥔 순간부터는 다른 어떤
    # 프로세스도 동시에 돌 수 없다는 게 보장되므로, 그 시점에 아직
    # 'running'으로 남아있는 행은 전부 죽은 게 확실하다(정상 종료라면
    # _finish_run이 completed/failed/completed_warn으로 바꿔놓고 끝났을
    # 것 — hard kill만이 'running'을 그대로 남긴다). 이 판정은 락을 쥔
    # writer(여기)만 내린다 — GET 핸들러는 절대 쓰지 않는다(막 시작하는
    # 다음 sync_cli와 경합할 수 있어서).
    lock_conn.exec_driver_sql("UPDATE graph_runs SET status = 'abandoned' WHERE status = 'running'")
    lock_conn.commit()

    client = build_neo4j_client()
    if args.generation:
        generation = args.generation
        client.write_generation_marker(generation)
    else:
        generation = client.read_generation_marker()
        if generation is None:
            # 마커가 없다(최초 1회거나 새 Neo4j) — 고정 상수로 fallback하면
            # 과거(이번 세션에서 실제로 겪은) "다른 Neo4j인데 PG에 이미 있던
            # 같은 상수값 때문에 전부 skip" 버그가 그대로 재발한다(2차 리뷰에서
            # 지적). 매번 고유한 값을 새로 만든다.
            generation = f"{EXTRACTOR_VERSION}.{uuid4().hex[:8]}"
        client.write_generation_marker(generation)
    args.generation = generation  # 아래에서 그대로 참조되는 모든 곳에 일관되게 반영

    scope = "partial" if args.source_ids else "full"
    run_id = _start_run(
        generation=args.generation, scope=scope, lexicon_version=_lexicon_version(),
        lock_holder_pid=lock_holder_pid,
    )
    stats = {"synced": 0, "skipped": 0, "failed": 0}
    fb_stats = {"synced": 0, "failed": 0}
    hub_recompute_ok: bool | None = None
    evidence_reasons: dict[str, int] = {}
    counters: dict[str, int] = {"unresolved_references": 0}
    try:
        try:
            external_id_index = build_external_id_index()
            page_identity_index = build_page_identity_index()
            last_heartbeat = time.monotonic()
            for doc_id in doc_ids:
                result = sync_document(
                    doc_id, client=client, external_id_index=external_id_index,
                    page_identity_index=page_identity_index, generation=args.generation,
                    counters=counters,
                )
                stats[result] += 1
                # F04 — 약 60초 간격으로 throttle. 전체 문서를 다 돌아도
                # 시간이 몇 초뿐인 디버그/테스트 실행에선 한 번도 안 찍힐
                # 수 있는데, 그건 괜찮다 — heartbeat 신선도는 status가
                # 'running'으로 오래 머무를 때만 의미가 있다.
                if time.monotonic() - last_heartbeat > 60:
                    _heartbeat(lock_conn, run_id)
                    last_heartbeat = time.monotonic()
            _heartbeat(lock_conn, run_id)  # 문서 루프 종료 지점

            all_buckets: list[dict] = []
            with session_scope() as session:
                for b in session.scalars(select(FailureBucket)).all():
                    all_buckets.append(
                        {
                            "id": b.id,
                            "bucket_name": b.bucket_name,
                            "confidence": b.confidence,
                            "discriminating_signals": list(b.discriminating_signals or []),
                            "counter_signals": list(b.counter_signals or []),
                            "evidence_ref": b.evidence_ref,
                            "root_cause": b.root_cause,
                            "recommended_action": b.recommended_action,
                        }
                    )
            for bucket_id in bucket_ids:
                result = sync_failure_bucket(
                    bucket_id, client=client, all_buckets=all_buckets, external_id_index=external_id_index,
                    page_identity_index=page_identity_index, generation=args.generation,
                )
                fb_stats[result] += 1
            _heartbeat(lock_conn, run_id)  # 버킷 루프 종료 지점

            evidence_reasons = aggregate_evidence_reasons(
                all_buckets, external_id_index=external_id_index, page_identity_index=page_identity_index,
            )

            if not args.skip_hub_recompute:
                try:
                    recompute_hub_flags(client)
                    hub_recompute_ok = True
                except Exception:  # noqa: BLE001 — §4.4와 같은 이유: 허브 재집계 실패가
                    # 이미 끝낸 문서/버킷 동기화 결과 출력을 막으면 안 된다
                    logger.exception("recompute_hub_flags failed")
                    hub_recompute_ok = False
        finally:
            client.close()
    except Exception:  # noqa: BLE001 — G05: 여기서 못 잡으면 graph_runs가 영원히
        # "running"으로 남아 다음 배치 모니터링이 "진행 중"과 "죽음"을 구분 못 한다.
        logger.exception("sync_cli run failed unexpectedly")
        _finish_run(
            run_id, status="failed",
            documents_synced=stats["synced"], documents_skipped=stats["skipped"],
            documents_failed=stats["failed"], buckets_synced=fb_stats["synced"],
            buckets_failed=fb_stats["failed"], hub_recompute_ok=hub_recompute_ok,
            unresolved_references=counters.get("unresolved_references", 0),
            documents_state_write_failed=counters.get("state_write_failed", 0),
        )
        _release_lock(lock_conn)
        raise

    state_write_failed = counters.get("state_write_failed", 0)
    # R02(2026-10-13 2차 핸드오프) §1 — mark_synced 실패(그래프 쓰기는 됐지만
    # PG 상태 기록만 실패)를 더 이상 "completed"로 숨기지 않는다. exit code도
    # non-zero가 된다.
    ok = (
        stats["failed"] == 0 and fb_stats["failed"] == 0
        and hub_recompute_ok is not False and state_write_failed == 0
    )
    status = "completed" if ok else "completed_warn"  # status 컬럼 String(16) 한도
    _finish_run(
        run_id, status=status,
        documents_synced=stats["synced"], documents_skipped=stats["skipped"],
        documents_failed=stats["failed"], buckets_synced=fb_stats["synced"],
        buckets_failed=fb_stats["failed"], hub_recompute_ok=hub_recompute_ok,
        unresolved_references=counters.get("unresolved_references", 0),
        documents_state_write_failed=state_write_failed,
    )
    print(json.dumps(
        {"run_id": run_id, "generation": args.generation, "documents": stats, "failure_buckets": fb_stats,
         "hub_recompute_ok": hub_recompute_ok, "evidence_reasons": evidence_reasons,
         "unresolved_references": counters.get("unresolved_references", 0),
         "documents_state_write_failed": state_write_failed},
        ensure_ascii=False,
    ))
    _release_lock(lock_conn)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
