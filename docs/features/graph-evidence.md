# 지식그래프 정합성 — G01~G11 추적 (2026-10-12 핸드오프 대응)

`/tmp/CLAUDE_CODE_PROMPT.md`(분석 커밋 `39fd123`)의 G01~G11을 추적한다. 각
항목: 재현 여부 → 원인 → 수정 커밋/파일 → 검증 증거 → 잔여.

## P0

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G01 (식별자 resolver) | **완료** | `apps/api/app/graph/identity.py`(신규), `extract_hierarchy`/`extract_references`/`extract_evidence`/`routers/graph._resolve_document_anchor_ids`가 결정적 tie-break(active>grade>source tier>id) 사용. 미해결 부모는 `UnresolvedPage`(Document 아님). **추가 수정**(1차 리뷰에서 발견): document 앵커가 대표가 아닌 쪽 복제본으로 걸려도 같은 계층을 찾도록, 같은 external_id를 가진 모든 수집본에 대해 explore()를 수행하고 기존 다중 앵커 병합 로직으로 합친다 — "어느 수집본을 앵커로 써도 같은 계층을 찾아야 한다" 요구를 이제 충족한다. | `tests/test_graph_identity.py`(7), `tests/test_graph_extract.py`의 hierarchy 테스트, `tests/test_graph_neo4j_client.py::test_merge_document_parent_of_to_unresolved_page_does_not_create_document`, `tests/test_graph_pipeline_db.py::test_sync_document_resolves_direct_parent_via_page_identity_index`(TGW 재현 입력 그대로), `tests/test_graph_router.py::test_explore_document_anchor_fans_out_across_duplicate_external_id_copies`(복제본 fan-out) |
| G02 (PARENT_OF 방향) | **완료** | `extract_hierarchy`가 직계 부모 1개만 emit, `Neo4jClient._merge_edges`가 PARENT_OF만 source/target을 뒤집어 parent→child로 씀. | `test_merge_document_parent_of_is_written_parent_to_child`, `test_sync_document_resolves_direct_parent_via_page_identity_index`(grandparent edge가 없음을 확인) |
| G03 (재동기화 교체) | **완료** | `_replace_owned_edges_tx`가 머지 전 소유 edge를 삭제(PARENT_OF는 incoming, 나머지는 outgoing). `_dedup_edges_prefer_extracted`로 EXTRACTED/INFERRED 충돌을 쓰기 전에 해소. **추가 수정**(1차 리뷰에서 발견 — 실제 재백필 전에 잡음): 이 incoming-삭제만으로는 "수정 전 코드가 쓴 (child)-[:PARENT_OF]->(raw-id ghost)" 유령(=이 문서 기준 outgoing)이 전혀 지워지지 않았다. 단순히 outgoing PARENT_OF를 전부 지우면 "이 문서가 다른 문서의 정상 부모인" 경우(지금 모델에서 유효한 outgoing)까지 같이 부순다 — 그래서 모든 managed edge에 `extractor_version`/`generation`을 쓰기 시점에 찍고, outgoing PARENT_OF 중 **이 스탬프가 없는 것만** 유령으로 간주해 지운다. | `test_merge_document_resync_replaces_owned_parent_of_edge`, `test_merge_document_resync_removes_vanished_component_mention`, `test_merge_document_resync_removes_legacy_unstamped_parent_of_ghost`(유령은 지우고 정상 outgoing PARENT_OF는 보존됨을 함께 확인) |
| G04 (제외 필터, 최소 구현) | **완료(최소 범위) — 실제 데이터 재현은 BLOCKED** | `app.graph.explore.filter_excluded_documents` + `/v1/graph/explore`의 `exclude_page_ids`/`exclude_subtree_ids`/`exclude_source_types` + `kb_graph_explore`의 동명 쉼표구분 인자. dedup/정렬/cap 이전에 적용. | `tests/test_graph_explore.py`(4종), `tests/test_graph_router.py::test_explore_excludes_document_by_page_id`(합성 fixture). **BLOCKED**: dev Postgres에 평가보고서 page_id `2586676618`가 아예 존재하지 않음(`SELECT count(*) FROM documents WHERE external_id='2586676618'` → 0) — dev에서는 "제외 전/후 동일 응답"을 재현할 수 없었고, 필터 자체가 켜지면 동작한다는 것만 합성 fixture로 확인했다. 실제 평가보고서 누출 재현은 운영 DB 접근이 있어야 가능. |
| | 잔여 | 중간 경로 bridge 차단(제외된 페이지를 거쳐야만 나오는 후보 차단), anchor 자신이 제외 대상일 때 `filtered` 상태 응답, exclude_subtree_ids의 PG 조회 비용 최적화 | 미구현 — 다음 라운드 |
| G05 (동기화 세대/신선도) | **대부분 완료** | `graph_sync_state.graph_generation`(마이그레이션 `20261012_0011`), `graph_runs` 테이블, `sync_cli.py --generation`(env `GRAPH_GENERATION`). **추가 수정**(1차 리뷰에서 발견): hub 재집계 실패가 `completed`/exit 0으로 숨겨지던 것을 `completed_warn`/exit 1로; 루프 중 예상 못 한 예외가 나면 `graph_runs`가 `running`으로 영원히 남던 것을 바깥 try/except로 감싸 `failed`로 전환. | `test_mark_synced_records_generation`, `test_sync_document_resyncs_when_generation_changes`, 실제 `sync_cli` 2회 실행(scratch DB, 1회차 synced=16/skipped=0, 2회차 synced=0/skipped=16, `graph_runs` 2행 확인), hub 실패·중간 예외 주입을 스크립트로 직접 실행해 `completed_warn`/`failed` 전환 확인(pytest화는 다음 라운드) |
| | 잔여 | `mark_synced` 자체 실패는 여전히 "synced" 반환(그래프 쓰기는 성공했으므로 의도적 — 단 `graph_runs`에 이 세부 실패가 안 보임), `unresolved_references` 카운트 미연동(항상 0), REST `/v1/graph/status` 미구현(as_of만 유지) | 미구현/의도적 설계 |

## P1(이번 라운드에서 함께 처리된 항목)

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G06 (별칭) | **부분 완료** | `data/seeds/lexicon/core.json`의 NetApp에 `netapp`/`넷앱` 추가. `seed_lexicon`/`POST /v1/lexicon/seed` 기본값을 `replace=False`(비파괴 upsert)로 변경 — 이번 세션 중 운영에서 `?replace=true`를 호출해 10건이 교체(삭제 후 75건 재삽입)된 적이 있다(그 10건 중 seed 파일에 없는 수동 추가 항목이 있었는지는 확인하지 않았다 — 데이터 손실을 확정해 말할 근거는 없지만, 기본값이 그 경로를 다시 열어주면 안 된다는 교훈). `extract_lexicon_components`가 토큰 집합 대신 변형어 문자열(긴 것 먼저) 검사로 바뀌어 다중 단어 구문과 조사 붙은 한글을 매칭. **추가 수정**(1차 리뷰에서 발견): 한글 변형어의 단순 포함 검사가 "행"(hang) 같은 한 글자 변형어를 "진행"/"수행" 안에서도 오탐시켰다 — 왼쪽 경계(바로 앞 글자가 한글이면 매칭 안 함)를 추가해 막고, 오른쪽은 열어 둬 조사 붙은 꼴은 그대로 매칭되게 유지. | `tests/test_graph_extract.py`의 lexicon 6종(멀티워드, 조사, ASCII 경계, 한글 오탐 방지, 한글 경계 매칭) |
| | 잔여 | `load_lexicon_map()`의 프로세스별 `lru_cache`가 여러 API 워커 프로세스 간에는 version 기반으로 동기화 안 됨(여전히 `cache_clear()`가 "그 요청을 받은 프로세스만" 적용) | 미구현 |
| G09 (등급 정렬) | **완료** | `app.graph.identity.GRADE_RANK`로 통합(A/A-/B/C/machine/draft) — `explore.py`/`failure_bucket.py` 둘 다 여기서 가져옴. | `test_grade_rank_includes_a_minus`, `test_shape_explore_result_sorts_a_minus_before_b_at_same_hop` |
| G07/G08/G10/G11 | **미시작** | — | 다음 라운드 — MCP 20건 cap 뒤 접근 수단, 전체 경로/provenance, failure_bucket resolution URL 해석(HAS_EVIDENCE 승격), 기존 테스트의 실제 계약 반영은 G01/G02 fixture 교체분(G11 일부)만 끝남 |

## 부수적으로 발견/수정

- **CI 누락 버그**: `app.main`이 항상 `app.routers.graph`를 import하는데, 그게
  `neo4j_client.py`의 모듈 최상단 `import neo4j`를 항상 실행해 `requirements-ci.txt`
  (neo4j 의도적 제외) 환경에서 `app.main`을 import하는 모든 테스트가 깨지고
  있었다(그래프와 무관한 `test_mock_idp_e2e.py` 포함, PR #16 머지 이후부터 존재).
  `neo4j_client.py`의 import를 optional로 바꾸고 실제 `Neo4jClient()` 생성
  시점에만 에러를 내도록 수정.

## 1차 코드 리뷰에서 걸러낸 것(실제 dev 재백필 전)

전체 dev 재백필(새 generation)을 한 번 시작했다가, 리뷰에서 G03/G06의 실제
결함 두 건을 찾아 **그 자리에서 kill하고** 수정 후 재검증했다 — 위 표의
"추가 수정" 항목들이 그 결과다. 죽이기 전 실행된 run(`graph_runs`에
`generation=2026-10-12.g1`, `status=running`으로 남은 행)은 미완성 데이터를
남겼을 수 있어, 두 수정이 끝난 뒤에만 다시 전체 재백필을 실행한다(별도 기록 예정).

## 검증 방법(재현 절차)

```bash
# 격리 Neo4j(절대 dev/운영 공유 인스턴스 아님)
docker run -d --name citec-graph-test-neo4j -e NEO4J_AUTH=neo4j/<SCRATCH_NEO4J_PASSWORD> \
  -p 17687:7687 -p 17474:7474 neo4j:5.26-community

# 격리 Postgres(기존 postgres 컨테이너 안에 scratch DB)
docker compose exec -T postgres psql -U citec -d postgres -c "CREATE DATABASE citec_graph_test;"
docker compose exec -T -e DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/citec_graph_test" \
  api alembic upgrade head

cd apps/api
GRAPH_TEST_DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:8574/citec_graph_test" \
GRAPH_NEO4J_TEST_URI="bolt://localhost:17687" GRAPH_NEO4J_TEST_USER=neo4j \
GRAPH_NEO4J_TEST_PASSWORD=<SCRATCH_NEO4J_PASSWORD> NEO4J_USERNAME=neo4j NEO4J_PASSWORD=<SCRATCH_NEO4J_PASSWORD> \
/tmp/graph-ci-venv/bin/python -m pytest tests/ -q -k "not mock_idp_e2e and not ops_dashboard_auth"
```

## BLOCKED(이번 세션에서 확인 불가)

- confluence-mcp/github-mcp 세션 도구 없음 — 라이브 Confluence 재검증, 10월 9일
  zip 재다운로드·SHA256 확인 미수행. 코드 근거(`evidence/source-excerpts.md`)와
  개발 DB/Neo4j 직접 조회로 대체했다.
