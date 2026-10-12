# 그래프 정합성 수정(G01~G06/G09) 운영 반영 계획 — 승인 대기, 미실행

이 문서는 계획이다. **이 세션은 운영에 아무 것도 반영하지 않았다.** dev에서
전체 검증(`docs/features/graph-evidence.md`)이 끝난 뒤, 사람 승인을 받고
수행한다.

## 사전 확인(실행 전 사람이 직접)

1. 운영 백업 — Postgres(`documents`, `graph_sync_state`, `graph_runs` 포함
   전체 또는 최소 이 세 테이블) + Neo4j(`neo4j-admin database dump` 또는
   볼륨 스냅샷). 백업 완료·복구 가능 여부 확인 전까지 2단계로 진행하지 않는다.
2. `api-entrypoint`가 컨테이너 시작 시 `alembic upgrade head`를 이미 실행하는지
   확인(`docker compose exec api cat /app/api-entrypoint*` 또는 동등 확인) —
   실행한다면 3단계는 자동, 아니면 수동으로 돌려야 한다.
3. cron(또는 동등 스케줄러)이 `scripts/graph_sync.sh`를 주기적으로 돌리고
   있다면, **4~6단계가 끝나기 전까지 반드시 일시 중지**한다 — 동시에 두
   `sync_cli` 프로세스가 같은 문서의 relationship을 MERGE/DELETE하면
   레이스가 난다(relationship MERGE에는 유니크 제약이 없음).

## 반영 순서

1. **백업**(위 1번, 완료·확인 후에만 진행).
2. **코드 배포**: `scripts/git_pull_deploy.sh`(이전 Phase 1/2 배포 때 쓴 것과
   동일한 가벼운 경로 — 이번 변경은 requirements/Dockerfile 변경 없음, 이미지
   재빌드 불필요. `--force-image-diff` 필요 여부는 배포 시점의 실제 git diff로
   재확인).
3. **마이그레이션**: `alembic upgrade head` (엔트리포인트가 자동 실행하지
   않는다면 `docker compose exec api alembic upgrade head`) — `graph_sync_state.
   graph_generation`, `graph_runs`, 변경 없음(purely additive, 기존 테이블 안
   건드림).
4. **lexicon 재시딩을 첫 전체 재백필 "전에"**: `POST /v1/lexicon/seed`(기본값
   `replace=false`로 바뀌었으므로 쿼리 파라미터 없이 호출해도 안전 — upsert만
   함). lexicon 내용은 `graph_sync_state.input_hash`에 안 들어가므로, 이
   순서를 지키지 않으면 재시딩 전에 동기화된 문서들은 EXTRACTOR_VERSION이
   바뀌기 전까지 넷앱/넷앱 별칭을 못 잡는다.
5. **전체 재백필**: `scripts/graph_sync.sh`(= `sync_cli`) `--generation` 없이
   실행 — 운영 Neo4j에 마커가 없으므로 새로 발급되고, `EXTRACTOR_VERSION`이
   `2026-10-12.3`으로 올라 있어 기존 `graph_sync_state`의 입력 해시 일치
   여부와 무관하게 전체가 재동기화된다(의도된 동작 — G01/G02/G03/G06 모두
   추출 로직이 바뀌었으므로 전체 재추출이 필요). 운영 코퍼스는 115,235건 —
   dev 측정(~19문서/초) 기준 전체 재백필은 약 100분 추정, 운영 쓰기 부하가
   다르면 달라질 수 있다.
6. **cron 재개**(3단계에서 멈췄다면).

## 완료 판정

- `graph_runs`의 해당 run 행이 `status=completed`(또는 hub 실패만 있으면
  `completed_warn` — 그래도 문서/버킷 실패는 0이어야 함).
- `MATCH (:Document)-[]->(g:Document) WHERE g.source_type IS NULL RETURN
  count(DISTINCT g)` ≈ 0(소수의 실패 건 수와 근사). 운영 데이터 규모가
  dev보다 커서 완전히 0이 아닐 수 있다 — 그 경우 남은 건을 별도 조사 대상으로
  기록한다.
- Component `Network`/`SCP`의 `is_hub = true`(운영 전체 코퍼스 기준 — dev
  샘플 규모에서는 안 걸릴 수 있다).
- 실제 Claude Code 세션에서 `kb_search` → `kb_graph_explore` → `kb_get_document`
  체인을 한국어 질문으로 1회 수행해 trace를 남긴다(이 보고서의 python 스크립트
  호출이 아니라 실제 MCP 도구 호출).

## 유령 노드 정리(별도 승인 필요, 이번 반영에 포함 안 함)

전체 재백필이 끝나면 고립된(=아무 edge도 안 남은) raw-id/None 유령 Document가
운영에 남을 수 있다(수정 전 데이터). **삭제하지 않는다** — 영향 범위·백업·
복구 절차를 별도로 작성하고 사람 승인을 받은 뒤에만 수행한다. 이번 반영은
"더 이상 유령을 새로 안 만든다"와 "재동기화되는 문서의 유령은 자동 정리된다"
까지만 보장하며, 전체 코퍼스가 한 번 재동기화를 거쳐야 그 보장이 완성된다.

## 금지 사항(이번 반영에서 하지 않음)

- Neo4j 전체 삭제, 수동 relationship 대량 DELETE.
- `?replace=true`로의 lexicon 재시딩(비파괴 upsert만).
- `--force-image-diff` 없이 이미지 재빌드가 필요한 척 판단하지 않기(현재
  변경분은 image 영향 없음을 재확인했지만, 배포 시점 diff가 다르면 재판단).
