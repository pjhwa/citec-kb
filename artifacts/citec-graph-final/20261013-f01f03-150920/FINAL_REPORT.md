# 2026-10-13 최종 핸드오프 — F01~F03 처리 결과

**범위: F01/F02/F03(확정된 세 결함)만.** F04~F10은 이번 실행에서 다루지 않았다 — 핸드오프 §3.1의 실행 순서와, 세 결함부터 끝낸 뒤 체크포인트하라는 지시를 따랐다. 아래는 결론과 상태표만 담고, 상세 재현/원시값은 `responses/`, `requirement-results.json`, `environment.json`에 있다.

## 결론

세 확정 결함(F01/F02/F03) 모두 수정·테스트·실제 dev MCP/CLI/REST로 재검증 완료. 이 과정에서 **F04(R02) 범위의 새 결함**(`GraphRun.started_at` 동시 삽입 시 동점 — `latest_run` 선택이 비결정적)을 하나 더 발견해 기록해뒀다(이번엔 수정하지 않음).

| 과제 | 상태 | 커밋 |
|---|---|---|
| F01 — 422를 연결 장애로 오분류 | ✅ 수정+검증 | `cf1882f` |
| F02 — 고립 bucket 자신의 사유 미전달 | ✅ 수정+검증 | `5d3ebcc` |
| F03 — 수동 metadata가 매 seed마다 지워짐 | ✅ 수정+검증 | `a134e78` |
| F04~F10 | NOT_RUN(이번 범위 밖) | — |

## F01 — HTTP 오류 분류

- 원인: `mcp-server/server.py`의 `_err()`가 모든 `httpx.HTTPError`(4xx 포함)를 "연결할 수 없습니다"로 뭉갰고, `kb_graph_explore`는 구조화 계약이 전혀 없었다.
- 수정: `_classify_http_error()`로 invalid_request/not_found/unauthorized/server_error/timeout/connection_error를 구분. `_err()`(기존 ~30개 도구 공유)는 텍스트에 올바른 `status:` 줄만 더함. `kb_graph_explore`는 `mcp.types.CallToolResult`를 직접 반환해 `isError=True` + `structuredContent`(status/error_code/retryable/http_status/field_errors)를 제공 — 기존 클라이언트가 캐시해둔 outputSchema와의 하위호환을 위해 `result` 키도 유지.
- **실환경 검증**: 이 세션의 실제 `mcp__wiki-mcp__kb_graph_explore` 호출로 `documents_offset=-1` 재현 입력을 다시 호출 — 수정 전엔 "연결할 수 없습니다"였던 게, 수정 후 `status: invalid_request`와 실제 필드 오류로 바뀜을 직접 확인(`responses/f01_after_fix.json`). 이 과정에서 FastMCP 클라이언트가 최초 연결 시 캐시한 outputSchema 때문에 `result` 키 없이 구조화 content만 주면 클라이언트 쪽 pydantic validation으로 실패한다는 걸 실측으로 발견해 반영함.
- 테스트: `mcp-server/test_graph_explore_error_envelope.py` 신규 5건, 기존 18건 포함 전체 23건(F02까지 반영 후 25건) 통과.

## F02 — 고립 failure_bucket anchor의 evidence_reasons

- 원인: `Neo4jClient.explore()`의 Cypher가 anchor 노드(`a`) 자신을 이미 가져오고 있었는데, "anchor가 None인지" 체크에만 쓰고 버렸다. MCP formatter도 이웃 bucket 줄에만 evidence_reasons를 렌더링했다.
- 수정: `explore()`가 `anchor_node`(bucket_name + evidence_reasons)를 반환하도록 확장. `routers/graph.py`가 failure_bucket anchor일 때 이를 `anchor_info`에 담음. MCP가 앵커 자신의 evidence_reasons(또는 명시적 `unknown`)를 렌더링.
- **실환경 검증**: 실제 재현 입력(`failure_bucket=89d78df7-...`)을 dev MCP로 재호출 — 이제 실제 bucket_name과 `evidence_reasons=unknown (아직 이 라운드 코드로 재백필되지 않음)`이 나온다(`responses/f02_after_fix.json`). dev Neo4j는 이 기능이 생긴 뒤로 재백필을 한 적이 없어(사용자 확인) 모든 버킷이 NULL — "unknown"이 나오는 게 맞는 동작이고, 실제 값이 있을 때의 동작은 격리 DB 테스트로 커버함.
- 테스트: Neo4jClient 3건, 라우터 3건, mcp-server 2건 신규 — 전부 통과.

## F03 — 사전 metadata 보존 + dry-run=apply 일치

- 원인: `seed_lexicon()`의 dry-run은 variants/priority만 비교하고 metadata는 전혀 안 봤다. 실제 apply는 매번 `row.metadata_ = dict(t.get("metadata") or {})`로 무조건 덮어썼는데, 실제 seed 파일 항목엔 `metadata` 키가 아예 없어서 **매 실행마다 모든 canonical의 metadata가 빈 dict로 지워지는** 실제 버그였다.
- 수정: `_plan_term_change()` 하나를 dry-run/apply가 공유. metadata도 variants와 같은 보존 정책(seed 값은 기본값, 기존 값이 있으면 그게 이김, `replace=True`만 예외) 적용. dry-run의 `change_fields`로 어떤 필드가 바뀌는지 canonical별로 노출(metadata-only 변경도 보임).
- **실환경 검증**: 가짜 객체가 아니라 **격리 Postgres + 실제 운영 seed 파일(`data/seeds/lexicon/core.json`, 75개 항목) + 실제 CLI(`python -m app.lexicon.seed`) + 실제 REST(`POST /v1/lexicon/seed`)**로 재현: `GRO`에 수동 metadata(`{"manual": true}`)와 수동 alias를 넣고 dry-run(예측: `GRO` variants만 변경) → apply(수동 metadata/alias 보존 확인 + seed의 새 variant 추가 확인) → 재실행 dry-run(변경 0건, 멱등성 확인) → REST 경로도 CLI와 동일 결과(`responses/f03_isolated_cli_api_run.json`).
- 테스트: `test_lexicon_seed_db.py` 신규 3건, 기존 6건 포함 전체 9건 통과. 전체 apps/api 유닛 스위트 474 passed, 회귀 없음.

## 이번에 새로 발견한, 수정하지 않은 결함 (F04/R02 범위)

`tests/test_graph_router.py::test_graph_status_ignores_partial_run_for_generation_matches`가 **실제 DB에 대해 재현 가능하게(3회 연속) 실패**한다.

- 원인: `GraphRun.started_at`이 `server_default=func.now()`를 쓰는데, Postgres의 `now()`는 **한 트랜잭션 안에서는 항상 같은 값**을 반환한다(`clock_timestamp()`와 다름). 같은 `session_scope()` 안에서 추가된 두 `GraphRun` 행은 `started_at`이 동일해져서, `ORDER BY started_at DESC`로는 "더 최근" 행을 안정적으로 고를 수 없다.
- 이 테스트는 `GRAPH_TEST_DATABASE_URL`/`GRAPH_NEO4J_TEST_URI`가 있어야 실행되는데, CI는 이 둘을 설정한 적이 없다 — 즉 **이 테스트가 실제 DB에 대해 돌아간 적이 이번이 처음**일 가능성이 높다.
- 이번엔 F01~F03 범위 밖이라 고치지 않았다. F04 라운드에서 다룰 것 — 수정 방향은 보통 두 번째 정렬 키(예: 단조 증가 시퀀스 컬럼)를 추가하거나 `clock_timestamp()`로 바꾸는 것.

## 다음 단계 (사용자 확인 필요)

1. F04~F10은 아직 시작 안 함 — 계속 진행할지, 어느 것부터 할지 확인 필요.
2. F06(R05, 관련성 랭킹)은 홀드아웃 질문셋이 먼저 고정돼야 시작할 수 있다(이번 세션의 기존 결정 + 핸드오프 §2.6이 동일하게 요구) — 질문셋을 사용자가 주거나 승인해야 함.
3. F09(스킬 동기화)의 로컬 설치 검증은 이 dev 호스트에 citec-mcp-workbench 스킬이 설치돼 있지 않아 BLOCKED로 남을 가능성이 높음(이전 라운드에 이미 확인됨).
4. F10(CI)은 이전에 사용자가 명시적으로 결정한 대로 — 사내 git 서버가 GitHub Actions를 지원하지 않는다는 전제 유지, 새 workflow 파일 추가 안 함.
5. 운영 반영: 이번 F01~F03 수정은 아직 운영에 배포되지 않았다. 유령 노드 정리(`--execute`)와 별개로 별도 배포 절차가 필요하다.
