# 지식그래프 정합성 — G01~G11 추적 (2026-10-12 핸드오프 대응)

`/tmp/CLAUDE_CODE_PROMPT.md`(분석 커밋 `39fd123`)의 G01~G11을 추적한다. 각
항목: 재현 여부 → 원인 → 수정 커밋/파일 → 검증 증거 → 잔여.

## P0

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G01 (식별자 resolver) | **완료** | `apps/api/app/graph/identity.py`(신규), `extract_hierarchy`/`_resolve_document_anchor_value`/`extract_references`/`extract_evidence`가 결정적 tie-break(active>grade>source tier>id) 사용. 미해결 부모는 `UnresolvedPage`(Document 아님). | `tests/test_graph_identity.py`(7), `tests/test_graph_extract.py`의 hierarchy 3종, `tests/test_graph_neo4j_client.py::test_merge_document_parent_of_to_unresolved_page_does_not_create_document`, `tests/test_graph_pipeline_db.py::test_sync_document_resolves_direct_parent_via_page_identity_index`(TGW 재현 입력 그대로, scratch Neo4j+PG에서 실제 실행) |
| G02 (PARENT_OF 방향) | **완료** | `extract_hierarchy`가 직계 부모 1개만 emit, `Neo4jClient._merge_edges`가 PARENT_OF만 source/target을 뒤집어 parent→child로 씀. | `test_merge_document_parent_of_is_written_parent_to_child`, `test_sync_document_resolves_direct_parent_via_page_identity_index`(grandparent edge가 없음을 확인) |
| G03 (재동기화 교체) | **완료** | `_replace_owned_edges_tx`가 머지 전 소유 edge를 삭제(PARENT_OF는 incoming, 나머지는 outgoing). `_dedup_edges_prefer_extracted`로 EXTRACTED/INFERRED 충돌을 쓰기 전에 해소. | `test_merge_document_resync_replaces_owned_parent_of_edge`, `test_merge_document_resync_removes_vanished_component_mention` |
| G04 (제외 필터, 최소 구현) | **완료(최소 범위)** | `app.graph.explore.filter_excluded_documents` + `/v1/graph/explore`의 `exclude_page_ids`/`exclude_subtree_ids`/`exclude_source_types` + `kb_graph_explore`의 동명 쉼표구분 인자. dedup/정렬/cap 이전에 적용. | `tests/test_graph_explore.py`(4종), `tests/test_graph_router.py::test_explore_excludes_document_by_page_id` |
| | 잔여 | 중간 경로 bridge 차단(제외된 페이지를 거쳐야만 나오는 후보 차단), anchor 자신이 제외 대상일 때 `filtered` 상태 응답, exclude_subtree_ids의 PG 조회 비용 최적화 | 미구현 — 다음 라운드 |
| G05 (동기화 세대/신선도) | **완료** | `graph_sync_state.graph_generation`(마이그레이션 `20261012_0011`), `graph_runs` 테이블, `sync_cli.py --generation`(env `GRAPH_GENERATION`). | `test_mark_synced_records_generation`, `test_sync_document_resyncs_when_generation_changes`, 실제 `sync_cli` 2회 실행(scratch DB, 1회차 synced=16/skipped=0, 2회차 synced=0/skipped=16, `graph_runs` 2행 확인) |
| | 잔여 | `unresolved_references` 카운트 미연동(항상 0), REST `/v1/graph/status` 미구현(as_of만 유지) | 미구현 |

## P1(이번 라운드에서 함께 처리된 항목)

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G06 (별칭) | **부분 완료** | `data/seeds/lexicon/core.json`의 NetApp에 `netapp`/`넷앱` 추가. `seed_lexicon`/`POST /v1/lexicon/seed` 기본값을 `replace=False`(비파괴 upsert)로 변경 — 운영에서 10월 12일 `replace=true` 호출로 10건이 삭제된 사고 재발 방지. `extract_lexicon_components`가 토큰 집합 대신 변형어 문자열(긴 것 먼저) 검사로 바뀌어 다중 단어 구문과 조사 붙은 한글을 매칭. | `tests/test_graph_extract.py`의 lexicon 4종(멀티워드, 조사, ASCII 경계) |
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

## 검증 방법(재현 절차)

```bash
# 격리 Neo4j(절대 dev/운영 공유 인스턴스 아님)
docker run -d --name citec-graph-test-neo4j -e NEO4J_AUTH=neo4j/testpassword123 \
  -p 17687:7687 -p 17474:7474 neo4j:5.26-community

# 격리 Postgres(기존 postgres 컨테이너 안에 scratch DB)
docker compose exec -T postgres psql -U citec -d postgres -c "CREATE DATABASE citec_graph_test;"
docker compose exec -T -e DATABASE_URL="postgresql+psycopg://citec:citec@postgres:5432/citec_graph_test" \
  api alembic upgrade head

cd apps/api
GRAPH_TEST_DATABASE_URL="postgresql+psycopg://citec:citec@localhost:8574/citec_graph_test" \
GRAPH_NEO4J_TEST_URI="bolt://localhost:17687" GRAPH_NEO4J_TEST_USER=neo4j \
GRAPH_NEO4J_TEST_PASSWORD=testpassword123 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword123 \
/tmp/graph-ci-venv/bin/python -m pytest tests/ -q -k "not mock_idp_e2e and not ops_dashboard_auth"
```

## BLOCKED(이번 세션에서 확인 불가)

- confluence-mcp/github-mcp 세션 도구 없음 — 라이브 Confluence 재검증, 10월 9일
  zip 재다운로드·SHA256 확인 미수행. 코드 근거(`evidence/source-excerpts.md`)와
  개발 DB/Neo4j 직접 조회로 대체했다.
