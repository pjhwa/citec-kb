#!/usr/bin/env python3
"""R04.10(2026-10-13 2차 핸드오프) — 고립 유령 Document 노드(source_type IS
NULL, edge 0개 — G01 수정 전 raw-id 유령의 잔존, 전체 재백필 후 827개 확인됨)
삭제는 "diff·백업·복구안을 만든 뒤 별도 승인 대상으로 남겨라"는 요구에 따라
이 스크립트는 **읽기 전용 dry-run만** 한다. 삭제 기능 자체가 없다 — 실제
삭제가 필요하면 이 스크립트의 출력(백업 manifest)을 사람이 검토한 뒤 별도
승인을 받고 손으로 실행한다.

사용법:
    GRAPH_NEO4J_TEST_URI=... GRAPH_NEO4J_TEST_PASSWORD=... \\
        python scripts/verify/ghost_node_audit.py [--out backup.json]

운영/dev 어느 Neo4j를 봐도 안전하다(쓰기 없음) — 단, 결과를 커밋하거나
외부에 공유하기 전에 민감정보가 없는지 확인한다(이 노드들은 어차피
source_type/title이 NULL이라 민감정보가 거의 없음).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# 엄격한 판정 — source_type이 NULL "이고" 양방향 relationship이 정확히
# 0개인 Document만 유령으로 본다. 둘 중 하나라도 걸치면 후보에서 뺀다
# (정상 문서인데 source_type만 일시적으로 비어 있거나, 유령처럼 보여도
# 실제로 다른 노드가 참조하고 있으면 지우면 안 됨).
_GHOST_QUERY = """
MATCH (d:Document)
WHERE d.source_type IS NULL AND labels(d) = ['Document']
OPTIONAL MATCH (d)-[r]-()
WITH d, count(r) AS degree
WHERE degree = 0
RETURN d.id AS id, d.external_id AS external_id, d.title AS title
ORDER BY d.id
"""

_COUNT_QUERY = """
MATCH (d:Document)
WHERE d.source_type IS NULL AND labels(d) = ['Document']
OPTIONAL MATCH (d)-[r]-()
WITH d, count(r) AS degree
RETURN count(d) AS total_null_source_type,
       count(CASE WHEN degree = 0 THEN 1 END) AS isolated_count
"""


def _audit(driver) -> dict:
    with driver.session() as session:
        counts = session.run(_COUNT_QUERY).single()
        rows = list(session.run(_GHOST_QUERY))
    ghosts = [{"id": r["id"], "external_id": r["external_id"], "title": r["title"]} for r in rows]
    return {
        "total_null_source_type": counts["total_null_source_type"],
        "isolated_count": counts["isolated_count"],
        "would_delete_count": len(ghosts),
        "would_delete": ghosts,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=None, help="백업 manifest(JSON) 저장 경로 — 기본: artifacts/ghost_node_audit_<timestamp>.json")
    args = p.parse_args(argv)

    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("✗ neo4j 패키지가 없습니다 — requirements.txt(not -ci)로 설치된 환경에서 실행하세요", file=sys.stderr)
        return 1

    uri = os.environ.get("GRAPH_NEO4J_TEST_URI") or os.environ.get("NEO4J_URI")
    user = os.environ.get("GRAPH_NEO4J_TEST_USER") or os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ.get("GRAPH_NEO4J_TEST_PASSWORD") or os.environ.get("NEO4J_PASSWORD")
    if not uri or not password:
        print("✗ GRAPH_NEO4J_TEST_URI/GRAPH_NEO4J_TEST_PASSWORD(또는 NEO4J_URI/NEO4J_PASSWORD)를 설정하세요", file=sys.stderr)
        return 1

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        result = _audit(driver)
    finally:
        driver.close()

    manifest = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "uri": uri,
        "total_null_source_type": result["total_null_source_type"],
        "isolated_count": result["isolated_count"],
        "would_delete_count": result["would_delete_count"],
        "would_delete": result["would_delete"],
        "restore_plan": (
            "이 목록의 각 id로 MERGE (d:Document {id: $id}) RETURN d — "
            "유령 노드는 source_type/title 등 다른 속성이 전혀 없으므로(원래도 "
            "비어 있었음) 복구는 빈 노드 재생성뿐이다. 실제 삭제는 이 스크립트가 "
            "하지 않으며, 사람이 별도로 승인·실행해야 한다."
        ),
    }

    out_path = args.out or f"artifacts/ghost_node_audit_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Document(source_type IS NULL) 총 {result['total_null_source_type']}건")
    print(f"그중 완전히 고립(관계 0개)된 유령: {result['isolated_count']}건")
    print(f"백업 manifest 저장: {out_path}")
    print("이 스크립트는 삭제하지 않습니다 — 실제 삭제는 사람이 manifest를 검토한 뒤 별도로 승인·실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
