# 그래프 정합성 수정(G01~G11 중 완료분) 운영 반영 계획 — 승인 대기, 미실행

이 문서는 계획이다. **이 세션은 운영에 아무 것도 반영하지 않았다.** dev에서
전체 검증(`docs/features/graph-evidence.md`)이 끝난 뒤, 사람 승인을 받고
수행한다.

**중요 — 전체 재백필이 필요한 변경과 아닌 변경을 구분한다.** G01~G06은
추출 로직 자체가 바뀌어 `EXTRACTOR_VERSION`이 올랐으므로 전체 재백필이
필요하다(아래 5단계). 이 문서 작성 뒤 추가된 G07(페이지네이션)/G08(경로·방향·
status)/G09 잔여/G10/G11은 **API/MCP 읽기 경로와 lexicon 매퍼 순수 함수
정리만 바꿨고 `EXTRACTOR_VERSION`을 올리지 않았다** — 코드 배포 +
`docker compose restart api mcp` 만으로 반영되며, 재백필이 필요 없다.
운영 반영 시 두 변경을 같은 배포에 묶어서 한 번에 배포해도 되고(권장 —
어차피 전체 재백필을 한 번은 해야 하므로), 나눠서 먼저 G01~G06만 반영 후
재백필을 돌리고 나중에 G07~G11을 추가 배포해도 된다.

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
   바뀌기 전까지 넷앱 별칭을 못 잡는다.
5. **전체 재백필**: `scripts/graph_sync.sh`(= `sync_cli`) `--generation` 없이
   실행 — 운영 Neo4j에 마커가 없으므로 새로 발급되고, `EXTRACTOR_VERSION`이
   `2026-10-12.3`으로 올라 있어 기존 `graph_sync_state`의 입력 해시 일치
   여부와 무관하게 전체가 재동기화된다(의도된 동작 — G01/G02/G03/G06 모두
   추출 로직이 바뀌었으므로 전체 재추출이 필요). 운영 코퍼스는 115,235건 —
   dev에서 이번 전체 재백필 중 **실측한 처리율은 ~16.5문서/초**(5분 구간
   직접 측정, `graph_sync_state` 증가량 기준) — 115,235건 기준 약 116분
   추정. 운영 쓰기 부하·네트워크가 다르면 달라질 수 있으니, 3단계의 cron
   중지는 이 추정보다 여유 있게(예: 3시간) 잡는다.
6. **mcp 컨테이너도 함께 재시작**: `docker compose restart api mcp`(또는
   `worker`까지 포함) — `api`만 재시작하면 REST 응답 모양은 바뀌어도
   `kb_graph_explore`가 그 새 필드(`documents_total` 등)를 쓰는 MCP 프로세스
   자체는 안 재시작돼 그대로일 수 있다(이번 세션에서 실제로 겪음 — api만
   재시작하고 mcp를 안 재시작하면 변경이 반쯤 반영된 것처럼 보인다).
7. **cron 재개**(3단계에서 멈췄다면).

## 완료 판정

- `graph_runs`의 해당 run 행이 `status=completed`(또는 hub 실패만 있으면
  `completed_warn` — 그래도 문서/버킷 실패는 0이어야 함).
- `MATCH (:Document)-[]->(g:Document) WHERE g.source_type IS NULL RETURN
  count(DISTINCT g)` ≈ 0(소수의 실패 건 수와 근사). 운영 데이터 규모가
  dev보다 커서 완전히 0이 아닐 수 있다 — 그 경우 남은 건을 별도 조사 대상으로
  기록한다.
- Component `Network`/`SCP`의 `is_hub = true`(운영 전체 코퍼스 기준 — dev
  샘플 규모에서는 안 걸릴 수 있다).
- Neo4j `Document` 중 PG `documents`에 더 이상 없는 id 수를 센다(`MATCH (d:Document)
  RETURN d.id` 결과를 PG `documents.id` 전체와 대조, 또는 소규모면 `documents.id
  IN (...)` 역질의) — 원문이 삭제/비활성화된 뒤에도 Neo4j에 그 문서가 "정상
  Document"로 남아있으면 G03의 "비활성·누락 문서가 근거 목록에 정상 문서로 나오지
  않아야 한다" 요구를 아직 충족 못 한 것(이번 범위는 재동기화되는 문서의 managed
  edge 정리까지만 보장 — 문서 자체가 사라진 경우의 Document 노드 정리는 별도).
- failure_bucket 8건은 `sync_cli`가 끝에서 매번 전부 재동기화하므로 이번 전체
  재백필에 자동 포함된다 — 끝난 뒤 `6ea28d59-...`(Pacemaker)의 REFERENCES가
  `confluence_map:2542097469`로 잡히는지, `sync_cli` JSON의 `evidence_reasons`
  집계가 기대 분포와 맞는지 확인(G10).
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
