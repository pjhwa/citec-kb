# 유령 Document 노드 정리 설계

## 배경

2026-10-13 2차 핸드오프 대응의 일부로 만든 `scripts/verify/ghost_node_audit.py`(읍기 전용 dry-run)를 운영 Neo4j에 돌려본 결과:

- 전체 `Document` 노드: 193,802건
- 정상 동기화된 문서: 115,309건 (`/v1/graph/status`의 `latest_full_completed_run.documents_synced`와 일치)
- 완전히 고립된 유령 노드: **78,493건**

유령 노드는 `source_type IS NULL`이고 관계(degree)가 0개인 `Document` 노드다. 운영 샘플 검증(`MATCH (d:Document {id: '1000703495'}) RETURN d, labels(d), properties(d)`) 결과 `properties(d) == {id: "1000703495"}` 단 하나뿐으로 확인됨 — label도 `Document` 하나뿐, 속성도 `id` 하나뿐, 관계 0개. 숨어있는 다른 데이터가 전혀 없는 "id만 있는 빈 깡통 노드"다.

id 값들은 Confluence raw page id 형식(`1000703495` 등)과 일치하며, G01 수정(식별자 통합 로직 수정, 2026-10-12 이전 라운드) 이전에 relationship MERGE가 참조 대상 `Document` 노드를 먼저 빈 상태로 만들어두고 실제 데이터로 채워지지 못한 채 남은 잔존물로 판단된다. G01이 이미 수정·배포되어 있으므로, 이 스펙이 다루는 78,493건은 **과거에 누적된 것**이며 이후 재백필(2026-10-13 전체 재백필 포함)로도 자동 정리되지 않는다 — `sync_document`는 PG에 있는 유효 문서만 upsert하고, 이렇게 예전에 생긴 고립 스텁은 삭제 로직이 없기 때문이다.

이 문서는 해당 78,493건을 안전하게 삭제하기 위한 설계다.

## 범위

- **포함**: 유령 노드 판정 쿼리 보강, 삭제 실행 경로(`--execute`) 추가, 복구 전략, 배치/에러 처리, 테스트 계획.
- **제외**: 이 스펙은 **일회성 정리 작업**이다. 재발 방지용 주기적 모니터링/알림/크론 추가는 범위 밖(사용자가 명시적으로 이번 스쿼프에서 제외하기로 결정). G01이 이미 원인을 차단했으므로 새 유령이 쌓이지 않을 것으로 예상하지만, 혹시 다시 쌓이는지 확인하려면 이후 재백필 시점마다 사람이 `ghost_node_audit.py`를 수동으로 다시 돌려보는 것으로 충분하다.

## 복구 전략

유령 노드에는 지울 "데이터"가 없다(id 하나뿐). 따라서 복구가 필요한 시나리오는 둘로 나뉜다:

1. **판정이 잘못되어 실제로 쓰이는 노드를 지운 경우**: 복구는 같은 id로 빈 노드를 재생성하는 것으로 충분하다(`MERGE (d:Document {id: $id}) RETURN d`). 원래도 빈 노드였으므로 데이터 손실이 없다. 이를 위해 `--execute`는 **삭제 직전에 확정된 id 목록을 별도 "삭제 manifest" 파일로 저장**한다.
2. **삭제 스크립트 자체의 버그로 범위를 벗어난 노드까지 지운 경우**: 노드 단위 복구로는 부족하므로, 실행 전 **전체 Neo4j 백업**(기존 `prod_deploy_and_backfill.sh`가 쓰는 Docker 볼륨 mountpoint tar 백업 방식 재사용)으로 복원한다.

두 가지를 모두 준비한다 — 전체 백업은 사람이 실행 직전에 수동으로 한 번 더 뜨고(스크립트 책임 밖, 런북에 명시), 삭제 manifest는 스크립트가 자동으로 남긴다.

## 아키텍처

`scripts/verify/ghost_node_audit.py`를 확장한다(별도 스크립트를 새로 만들지 않음 — 판정 쿼리를 dry-run/execute 두 경로가 공유해야 하기 때문).

### 판정 쿼리 보강

기존 쿼리에 `labels(d) = ['Document']` 조건을 추가한다. 유령 노드가 혹시 `Document` 외 다른 label도 같이 갖고 있다면(멀티 라벨 노드) 삭제 대상에서 제외해야 하는데, 기존 쿼리는 이를 확인하지 않았다.

```cypher
MATCH (d:Document)
WHERE d.source_type IS NULL AND labels(d) = ['Document']
OPTIONAL MATCH (d)-[r]-()
WITH d, count(r) AS degree
WHERE degree = 0
RETURN d.id AS id, d.external_id AS external_id, d.title AS title
ORDER BY d.id
```

dry-run(`_GHOST_QUERY`/`_COUNT_QUERY`)과 execute 경로 모두 이 조건을 쓴다. dry-run 쪽도 함께 수정해서, dry-run이 보여주는 숫자가 곧 실제 삭제될 숫자와 일치하도록 맞춘다.

### `--execute` 플래그

플래그 없이 실행하면 기존과 동일한 읍기 전용 dry-run(변경 없음). `--execute`를 주면 아래 순서로 진행한다.

1. **동시성 잠금**: sync_cli(`apps/api/app/graph/sync_cli.py`)가 쓰는 동일한 Postgres advisory lock key(`_ADVISORY_LOCK_KEY`)를 `pg_try_advisory_lock`으로 시도한다. 실패하면(= sync_cli가 이미 돌고 있음) 삭제를 시도하지 않고 즉시 중단, 명확한 에러 메시지 출력. sync_cli는 relationship MERGE 도중 일시적으로 새 래치 노드를 만들 수 있으므로, 동시 실행을 코드 차원에서 원천 차단한다.
2. **실시간 재조회**: 락 획득 성공 시, 과거에 저장된 dry-run manifest 파일은 신뢰하지 않고 **그 시점에 위 판정 쿼리를 다시 실행**하여 삭제 대상을 확정한다(TOCTOU 방지).
3. **확인 게이트**: 확정된 삭제 대상 건수를 보여주고, 터미널에 정확히 `"delete"`를 입력해야 진행한다. 다른 입력이면 취소.
4. **삭제 manifest 저장**: 확정된 id 목록을 삭제 실행 직전에 별도 JSON 파일(`artifacts/ghost_node_deletion_<timestamp>.json`)로 저장 — 복구 전략 1번의 참고 자료.
5. **배치 삭제 실행** (아래 "배치 삭제 구현" 참고).
6. **락 해제** 및 최종 결과(요청 건수 / 실제 삭제 건수 / 실패 건수) 출력.

사용자 입장에서는 한 번의 명령 실행으로 전체가 끝난다 — 중간에 사람이 멈추거나 재개할 체크포인트는 없다(요청: "한 번에 전체 삭제").

### 배치 삭제 구현

Neo4j 트랜잭션 메모리 문제를 피하기 위해 내부적으로 5,000건 단위 배치로 나눠 처리한다. 이는 순수 구현 디테일이며 사용자에게 노출되는 체크포인트가 아니다.

```python
_DELETE_BATCH_SIZE = 5000

def _delete_batch(session, ids: list[str]) -> int:
    result = session.run(
        "UNWIND $ids AS id MATCH (d:Document {id: id}) "
        "WHERE d.source_type IS NULL AND labels(d) = ['Document'] "
        "DETACH DELETE d RETURN count(d) AS deleted",
        ids=ids,
    )
    return result.single()["deleted"]
```

- 배치 삭제 Cypher에도 `source_type IS NULL AND labels(d) = ['Document']` 조건을 다시 건다 — 재조회(2단계)와 실제 삭제(5단계) 사이에 다른 프로세스가 그 id로 관계를 만들었다면(원래 관계가 생겼다면 이 조건에 걸려 삭제되지 않음), 어차피 advisory lock으로 sync_cli와의 동시 실행은 막혀 있지만 방어적으로 한 번 더 조건을 건다.
- 각 배치는 독립 트랜잭션(드라이버의 `session.run` 기본 동작) — 한 배치가 실패해도 이전 배치는 이미 커밋돼 있다.
- 배치 실행 중 예외가 발생하면 즉시 중단하고, 지금까지 삭제된 건수와 남은 대상 id 목록을 에러 메시지로 출력한다. 재실행은 안전하다(idempotent) — 이미 삭제된 id는 `MATCH`가 안 돼서 자동으로 스킵된다.

## 사전 조건 (스크립트 바깥, 런북으로 문서화)

1. `--execute` 실행 전, 전체 Neo4j 백업을 한 번 더 뜬다 (기존 운영 배포 스크립트의 Docker 볼륨 mountpoint tar 방식 재사용).
2. cron에 걸린 주기 재백필이 있다면 그 시간과 겹치지 않는 시간대에 실행한다(advisory lock이 2차 방어선, 사람이 타이밍을 피하는 게 1차 방어선).

## 테스트 계획

dev scratch Neo4j(`citec-graph-test-neo4j`)에서:

1. 유령 노드 샘플(및 "관계 있는 정상 노드", "`Document` 외 다른 label도 가진 노드")을 수동으로 만들어둔다.
2. `--execute` 없이 돌려서 dry-run manifest가 유령 노드만 정확히 잡는지 확인(label 조건 추가 후에도 기존 동작과 동일하게).
3. 다른 세션에서 동일 advisory lock을 미리 잡아둔 상태로 `--execute` 실행 → 즉시 lock 획득 실패로 중단되는지 확인.
4. `--execute` 실행 → `"delete"` 확인 프롬프트가 뜨는지, 틀린 입력(예: `"yes"`)에는 취소되는지 확인.
5. 확인 후 실제로 유령만 삭제되고, 관계 있는 정상 노드/다른 label 노드는 삭제되지 않는지 확인.
6. 배치 중간에 의도적으로 예외를 주입해, 재실행 시 이미 삭제된 건 건너뛰고 나머지만 처리되는지(idempotent) 확인.

## 완료 기준

- 운영에서 `--execute` 실행 후 `MATCH (d:Document) WHERE d.source_type IS NULL AND labels(d) = ['Document'] OPTIONAL MATCH (d)-[r]-() WITH d, count(r) AS degree WHERE degree = 0 RETURN count(d)` 결과가 0.
- 전체 `Document` 노드 수가 정상 동기화 건수(약 115,309 ± 이후 자연 증감)와 일치.
- 삭제 manifest 파일이 `artifacts/`에 남아 있음.
- 기존 `/v1/graph/status`, `kb_graph_explore` 등 정상 경로 동작에 영향 없음(유령 노드는 원래부터 고립 상태라 어떤 질의 경로에도 참여하지 않았으므로 회귀 위험 낮음).
