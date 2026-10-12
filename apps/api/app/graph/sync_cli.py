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
    DEFAULT_GENERATION,
    EXTRACTOR_VERSION,
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
        "--generation", default=os.environ.get("GRAPH_GENERATION", DEFAULT_GENERATION),
        help="그래프 세대 식별자(G05) — 다른 Neo4j로 옮길 때 바꾸면 PG의 과거 "
             "skip 상태를 무시하고 전체 재동기화한다. 기본: $GRAPH_GENERATION",
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

    run_id = _start_run(generation=args.generation)
    client = build_neo4j_client()
    external_id_index = build_external_id_index()
    page_identity_index = build_page_identity_index()
    stats = {"synced": 0, "skipped": 0, "failed": 0}
    hub_recompute_ok: bool | None = None
    try:
        for doc_id in doc_ids:
            result = sync_document(
                doc_id, client=client, external_id_index=external_id_index,
                page_identity_index=page_identity_index, generation=args.generation,
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
                    }
                )
        fb_stats = {"synced": 0, "failed": 0}
        for bucket_id in bucket_ids:
            result = sync_failure_bucket(
                bucket_id, client=client, all_buckets=all_buckets, external_id_index=external_id_index
            )
            fb_stats[result] += 1

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

    status = "completed" if stats["failed"] == 0 and fb_stats["failed"] == 0 else "failed"
    _finish_run(
        run_id, status=status,
        documents_synced=stats["synced"], documents_skipped=stats["skipped"],
        documents_failed=stats["failed"], buckets_synced=fb_stats["synced"],
        buckets_failed=fb_stats["failed"], hub_recompute_ok=hub_recompute_ok,
    )
    print(json.dumps(
        {"run_id": run_id, "generation": args.generation, "documents": stats, "failure_buckets": fb_stats},
        ensure_ascii=False,
    ))
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
