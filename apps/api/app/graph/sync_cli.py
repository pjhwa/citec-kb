"""CLI: python -m app.graph.sync_cli — scripts/graph_sync.sh가 컨테이너 안에서 호출."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
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


def _start_run(*, generation: str) -> str:
    run_id = str(uuid4())
    with session_scope() as session:
        session.add(GraphRun(
            run_id=run_id, extractor_version=EXTRACTOR_VERSION, generation=generation,
        ))
    return run_id


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

    run_id = _start_run(generation=args.generation)
    stats = {"synced": 0, "skipped": 0, "failed": 0}
    fb_stats = {"synced": 0, "failed": 0}
    hub_recompute_ok: bool | None = None
    evidence_reasons: dict[str, int] = {}
    counters: dict[str, int] = {"unresolved_references": 0}
    try:
        try:
            external_id_index = build_external_id_index()
            page_identity_index = build_page_identity_index()
            for doc_id in doc_ids:
                result = sync_document(
                    doc_id, client=client, external_id_index=external_id_index,
                    page_identity_index=page_identity_index, generation=args.generation,
                    counters=counters,
                )
                stats[result] += 1

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
        )
        raise

    ok = stats["failed"] == 0 and fb_stats["failed"] == 0 and hub_recompute_ok is not False
    status = "completed" if ok else "completed_warn"  # status 컬럼 String(16) 한도
    _finish_run(
        run_id, status=status,
        documents_synced=stats["synced"], documents_skipped=stats["skipped"],
        documents_failed=stats["failed"], buckets_synced=fb_stats["synced"],
        buckets_failed=fb_stats["failed"], hub_recompute_ok=hub_recompute_ok,
        unresolved_references=counters.get("unresolved_references", 0),
    )
    print(json.dumps(
        {"run_id": run_id, "generation": args.generation, "documents": stats, "failure_buckets": fb_stats,
         "hub_recompute_ok": hub_recompute_ok, "evidence_reasons": evidence_reasons,
         "unresolved_references": counters.get("unresolved_references", 0)},
        ensure_ascii=False,
    ))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
