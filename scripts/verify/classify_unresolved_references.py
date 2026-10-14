#!/usr/bin/env python3
"""F05(2026-10-13 최종 핸드오프) §2.5 — "이전 full run의 unresolved_references
개수는 집계값이다. 이를 모두 오류 또는 누락 문서로 분류하지 말라"는 요구에
따라, graph_runs.unresolved_references 숫자만 보고 판단하지 않고 각
UnresolvedPage의 실제 원인을 분류한다.

**읽기 전용** — Neo4j/Postgres 어디에도 쓰지 않는다. 운영/dev 어느 쪽을
봐도 안전하다.

분류 기준 (각 UnresolvedPage.external_id 하나당):
  - target_exists_active:   같은 raw id의 Document가 Postgres에 하나 이상
                            status=active로 존재한다 — 다음 sync_cli 실행이
                            이 참조를 해석해야 하는데 아직 안 됐다는 뜻(F05
                            §2.5.2가 말하는 "재적재된 참조 대상" 케이스).
  - target_exists_archived: 존재는 하지만 전부 archived.
  - target_not_ingested:    Postgres 어디에도 해당 external_id가 없다 —
                            진짜 누락(외부 페이지, 삭제됨, 또는 아직
                            크롤 안 된 페이지).
  - malformed_id:           external_id가 비어있거나 숫자/일반 페이지ID
                            형태가 아님(참고용 — 거짓양성 소지).

사용법 (ghost_node_audit.py와 같은 호출 관례):
    cd apps/api && PYTHONPATH=. \\
        DATABASE_URL=postgresql+psycopg://... \\
        NEO4J_URI=bolt://... NEO4J_USERNAME=neo4j NEO4J_PASSWORD=... \\
        python ../../scripts/verify/classify_unresolved_references.py [--sample 10] [--out report.json]

DATABASE_URL/NEO4J_* 가 없으면 app.settings의 기본값(컨테이너 안에서 실행
중이면 그 환경의 실제 값)을 그대로 쓴다 — docker compose exec api로 돌리면
별도 env 지정 없이 운영 자신의 DB를 그대로 읽는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

_NUMERIC_ID_RE = re.compile(r"^\d+$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=10, help="분류별로 보여줄 샘플 수")
    parser.add_argument("--out", type=str, default=None, help="전체 결과를 JSON으로 저장할 경로")
    args = parser.parse_args(argv)

    from app.graph.neo4j_client import get_shared_client
    from app.db.session import session_scope
    from sqlalchemy import text

    client = get_shared_client()
    with client._driver.session() as session:  # noqa: SLF001 — 읽기 전용 단발 쿼리, 전용 메서드 없음
        rows = session.run(
            """
            MATCH (u:UnresolvedPage)
            OPTIONAL MATCH (u)-[:PARENT_OF]-(child:Document)
            RETURN u.external_id AS raw_id, collect(DISTINCT child.id) AS referencing_doc_ids
            """
        ).data()

    if not rows:
        print("UnresolvedPage 노드가 없습니다 — 분류할 게 없습니다.")
        return 0

    raw_ids = [r["raw_id"] for r in rows]
    with session_scope() as pg_session:
        found = pg_session.execute(
            text("SELECT external_id, source_type, status FROM documents WHERE external_id = ANY(:ids)"),
            {"ids": raw_ids},
        ).all()
    by_raw_id: dict[str, list[tuple[str, str]]] = {}
    for external_id, source_type, status in found:
        by_raw_id.setdefault(external_id, []).append((source_type, status))

    classified: dict[str, list[dict]] = {
        "target_exists_active": [], "target_exists_archived": [],
        "target_not_ingested": [], "malformed_id": [],
    }
    for row in rows:
        raw_id = row["raw_id"]
        item = {
            "raw_id": raw_id,
            "referencing_doc_ids": row["referencing_doc_ids"],
            "matches_in_postgres": [
                {"source_type": st, "status": status} for st, status in by_raw_id.get(raw_id, [])
            ],
        }
        if not raw_id or not _NUMERIC_ID_RE.match(str(raw_id)):
            classified["malformed_id"].append(item)
        elif any(status == "active" for _, status in by_raw_id.get(raw_id, [])):
            classified["target_exists_active"].append(item)
        elif by_raw_id.get(raw_id):
            classified["target_exists_archived"].append(item)
        else:
            classified["target_not_ingested"].append(item)

    counts = {k: len(v) for k, v in classified.items()}
    print(f"총 UnresolvedPage: {len(rows)}건")
    for k, n in counts.items():
        print(f"  {k}: {n}건")
        for item in classified[k][: args.sample]:
            print(f"    - raw_id={item['raw_id']} referencing={item['referencing_doc_ids'][:3]}"
                  f"{'...' if len(item['referencing_doc_ids']) > 3 else ''}"
                  f" pg_matches={item['matches_in_postgres']}")

    if counts["target_exists_active"] > 0:
        print(
            f"\n주의: target_exists_active가 {counts['target_exists_active']}건 — "
            "다음 sync_cli 실행(cron)이 이 참조들을 자동으로 재해석해야 합니다. "
            "F05의 '후적재 참조 자동 재계산' 기능(invalidation queue)은 아직 미구현이라, "
            "지금은 전체 재백필이 이를 우연히 해결해줄 뿐 보장된 동작이 아닙니다."
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"total": len(rows), "counts": counts, "classified": classified}, f,
                      ensure_ascii=False, indent=2)
        print(f"\n전체 결과 저장: {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
