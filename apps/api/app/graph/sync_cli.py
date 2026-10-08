"""CLI: python -m app.graph.sync_cli — scripts/graph_sync.sh가 컨테이너 안에서 호출."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy import select

from app.db.models import Document, FailureBucket
from app.db.session import session_scope
from app.graph.pipeline import (
    build_external_id_index,
    build_neo4j_client,
    recompute_hub_flags,
    sync_document,
    sync_failure_bucket,
)

logger = logging.getLogger("citec.graph.sync_cli")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="대상 건수만 세고 종료")
    p.add_argument("--source-ids", help="쉼표구분 document_id만 처리(디버그용)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--skip-hub-recompute", action="store_true",
        help="Component.is_hub 재집계 생략(디버그/부분 실행용)",
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
    external_id_index = build_external_id_index()
    stats = {"synced": 0, "skipped": 0, "failed": 0}
    try:
        for doc_id in doc_ids:
            result = sync_document(doc_id, client=client, external_id_index=external_id_index)
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
            except Exception:  # noqa: BLE001 — §4.4와 같은 이유: 허브 재집계 실패가
                # 이미 끝낸 문서/버킷 동기화 결과 출력을 막으면 안 된다
                logger.exception("recompute_hub_flags failed")
    finally:
        client.close()

    print(json.dumps({"documents": stats, "failure_buckets": fb_stats}, ensure_ascii=False))
    return 0 if stats["failed"] == 0 and fb_stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
