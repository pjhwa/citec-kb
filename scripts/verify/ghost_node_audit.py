#!/usr/bin/env python3
"""R04.10(2026-10-13 2차 핸드오프) — 고립 유령 Document 노드(source_type IS
NULL, edge 0개 — G01 수정 전 raw-id 유령의 잔존, 전체 재백필 후 다수 확인됨
— 환경마다 다름, dry-run으로 먼저 실제 건수를 확인하라) 삭제는 "diff·백업·
복구안을 만든 뒤 별도 승인 대상으로 남겨라"는 요구에 따라 설계했다.

기본 동작(플래그 없음)은 **읽기 전용 dry-run**이다 — 쓰기 없음, 운영/dev
어느 Neo4j를 봐도 안전하다. 결과를 커밋하거나 외부에 공유하기 전에
민감정보가 없는지 확인한다(이 노드들은 어차피 source_type/title이 NULL이라
민감정보가 거의 없음).

`--execute`를 주면 실제로 삭제한다. 안전장치: (1) app.graph.sync_cli와
같은 Postgres advisory lock을 먼저 잡아 전체 재백필(sync_cli)과 동시에
돌지 못하게 막고, (2) 과거 dry-run manifest를 신뢰하지 않고 이 호출
시점에 판정 쿼리를 다시 실행하며, (3) 터미널에 "delete"를 정확히
입력해야 진행되는 확인 게이트가 있고, (4) 삭제 전에 대상 id 목록을
manifest 파일로 먼저 남긴 뒤 배치 단위로 지운다(배치 중간 실패 후
재시도해도 안전 — 이미 지워진 id는 재매치되지 않음).

`--execute`는 app.db.session / app.graph.sync_cli를 import하므로
apps/api 패키지가 보여야 한다 — repo 루트에서 실행한다면 먼저
`cd apps/api && PYTHONPATH=. python ../../scripts/verify/ghost_node_audit.py --execute`
처럼 PYTHONPATH를 apps/api로 잡아주거나 apps/api 안에서 실행해야 한다
(아니면 `ModuleNotFoundError: No module named 'app'`). 플래그 없는
dry-run 모드는 이 import를 하지 않으므로 이 제약이 없다.

사용법:
    GRAPH_NEO4J_TEST_URI=... GRAPH_NEO4J_TEST_PASSWORD=... \\
        python scripts/verify/ghost_node_audit.py [--out backup.json]
    # 실제 삭제(사람이 'delete' 확인 입력 필요):
    cd apps/api && PYTHONPATH=. GRAPH_NEO4J_TEST_URI=... GRAPH_NEO4J_TEST_PASSWORD=... \\
        DATABASE_URL=... python ../../scripts/verify/ghost_node_audit.py --execute
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

# labels() 가드 때문에 total_null_source_type은 "source_type이 NULL인 모든
# Document"가 아니라 "label이 Document 하나뿐이면서 source_type이 NULL인
# Document"다 — 멀티 라벨 노드는 삭제 후보에서도, 이 총계에서도 빠진다
# (의도적 — 삭제 후보 집합과 항상 숫자가 일치하게 유지하기 위함).
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


_DELETE_BATCH_SIZE = 5000


def _delete_batch(session, ids: list[str]) -> int:
    # 삭제 대상 정의 전체(source_type NULL + 단일 Document label + 관계
    # 0개)를 삭제 쿼리 자체에도 다시 걸어둔다 — live re-audit과 이 배치
    # 실행 사이에 다른 writer가 이 노드에 관계를 붙였을 가능성까지
    # 막는다(advisory lock은 sync_cli만 막지, 다른 writer는 못 막음).
    result = session.run(
        "UNWIND $ids AS id MATCH (d:Document {id: id}) "
        "WHERE d.source_type IS NULL AND labels(d) = ['Document'] AND NOT (d)--() "
        "DETACH DELETE d RETURN count(d) AS deleted",
        ids=ids,
    )
    return result.single()["deleted"]


def _execute_deletion(driver, *, uri, confirm) -> dict:
    """락은 호출자(main())가 미리 잡아둔 상태에서 불린다. 과거 dry-run
    manifest는 신뢰하지 않고, 이 호출 시점에 판정 쿼리를 다시 실행해
    삭제 대상을 확정한다(TOCTOU 방지)."""
    result = _audit(driver)
    ids = [g["id"] for g in result["would_delete"]]
    if not ids:
        return {"requested": 0, "deleted": 0}

    prompt = f"{uri}에서 {len(ids)}건을 삭제하시겠습니까? 되돌릴 수 없습니다. 'delete' 입력: "
    if not confirm(prompt):
        return {"requested": len(ids), "deleted": 0, "cancelled": True}

    manifest_path = (
        f"artifacts/ghost_node_deletion_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    abs_manifest_path = os.path.abspath(manifest_path)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {"deleted_at": datetime.now(timezone.utc).isoformat(), "uri": uri, "ids": ids},
            f, ensure_ascii=False, indent=2,
        )

    deleted_total = 0
    with driver.session() as session:
        for i in range(0, len(ids), _DELETE_BATCH_SIZE):
            batch = ids[i : i + _DELETE_BATCH_SIZE]
            try:
                deleted_total += _delete_batch(session, batch)
            except Exception:
                remaining = ids[i:]
                print(
                    f"✗ 배치 삭제 중 실패 — 지금까지 {deleted_total}건 삭제됨, "
                    f"{len(remaining)}건 남음(재시도 시 이미 지워진 건 자동으로 건너뜀, idempotent). "
                    f"manifest: {manifest_path}",
                    file=sys.stderr,
                )
                raise

    return {
        "requested": len(ids), "deleted": deleted_total,
        "failed": len(ids) - deleted_total,
        "uri": uri, "manifest": manifest_path, "manifest_abs": abs_manifest_path,
    }


def _confirm_delete(prompt: str) -> bool:
    try:
        return input(prompt).strip() == "delete"
    except EOFError:
        # 비대화형 환경(cron, 파이프 등)에서 stdin이 닫혀 있으면 input()이
        # EOFError를 던진다 — raw traceback 대신 깔끔하게 취소로 처리한다.
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=None, help="백업 manifest(JSON) 저장 경로 — 기본: artifacts/ghost_node_audit_<timestamp>.json")
    p.add_argument(
        "--execute", action="store_true",
        help="dry-run이 아니라 실제로 삭제한다 — sync_cli와 같은 advisory lock을 "
             "먼저 잡고, 그 시점에 판정 쿼리를 다시 실행해 'delete' 확인을 받은 뒤 삭제한다.",
    )
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

    if args.execute:
        from app.db.session import get_engine
        from app.graph.sync_cli import _ADVISORY_LOCK_KEY, _release_lock

        lock_conn = get_engine().connect()
        got_lock = lock_conn.exec_driver_sql(
            "SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)
        ).scalar()
        if not got_lock:
            lock_conn.close()
            driver.close()
            print("✗ sync_cli가 이미 같은 락을 쥐고 있습니다 — 삭제를 시도하지 않고 중단합니다", file=sys.stderr)
            print(json.dumps({"error": "lock_held_by_another_run"}), file=sys.stderr)
            return 1
        try:
            outcome = _execute_deletion(driver, uri=uri, confirm=_confirm_delete)
        finally:
            # _release_lock 내부가 unlock 실행 자체에서 예외를 던져도
            # lock_conn은 그 안의 try/finally로 반드시 close되고, driver는
            # 이 바깥 finally로 반드시 close된다 — 어느 한쪽이 실패해도
            # 두 커넥션 다 누수되지 않게 한다.
            try:
                _release_lock(lock_conn)
            finally:
                driver.close()
        print(json.dumps(outcome, ensure_ascii=False))
        if outcome.get("cancelled"):
            print("삭제를 취소했습니다.")
            return 0
        print(f"요청 {outcome['requested']}건 중 {outcome['deleted']}건 삭제 완료"
              + (f", {outcome['failed']}건 실패" if outcome.get('failed') else "") + ".")
        if outcome.get("manifest_abs") or outcome.get("manifest"):
            print(f"manifest 절대경로: {outcome.get('manifest_abs', outcome.get('manifest'))} — 컨테이너를 재생성하기 전에 반드시 복사해두세요.")
        return 0

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
            "비어 있었음) 복구는 빈 노드 재생성뿐이다. 이 dry-run manifest "
            "자체는 삭제를 일으키지 않는다 — 실제 삭제는 별도 경로인 "
            "--execute(자체 안전 manifest를 따로 남김)로 수행하며, 사람이 "
            "'delete'를 정확히 입력해 승인해야 진행된다."
        ),
    }

    out_path = args.out or f"artifacts/ghost_node_audit_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Document(source_type IS NULL) 총 {result['total_null_source_type']}건")
    print(f"그중 완전히 고립(관계 0개)된 유령: {result['isolated_count']}건")
    print(f"백업 manifest 저장: {out_path}")
    print("플래그 없이 실행하면(dry-run) 삭제하지 않습니다 — 실제 삭제는 --execute로 수행합니다(사람이 manifest를 검토한 뒤 'delete' 입력으로 승인).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
