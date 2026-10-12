# 지식그래프 정합성 — G01~G11 추적 (2026-10-12 핸드오프 대응)

`/tmp/CLAUDE_CODE_PROMPT.md`(분석 커밋 `39fd123`)의 G01~G11을 추적한다. 각
항목: 재현 여부 → 원인 → 수정 커밋/파일 → 검증 증거 → 잔여.

## P0

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G01 (식별자 resolver) | **거의 완료 — 범위 한정 있음** | `apps/api/app/graph/identity.py`(신규), `extract_hierarchy`/`routers/graph._resolve_document_anchor_ids`가 결정적 tie-break(active>grade>source tier>id) 사용. 미해결 부모는 `UnresolvedPage`(Document 아님). document 앵커가 대표가 아닌 쪽 복제본으로 걸려도 같은 external_id의 모든 수집본에 대해 explore()를 수행하고 기존 다중 앵커 병합 로직으로 합친다(복제본 자신은 fan-out 부작용으로 "연관 문서"에서 제외, 2차 리뷰에서 발견해 수정). **범위 한정**(2차 리뷰에서 발견, 아직 안 고침): `extract_references`/`extract_evidence`는 여전히 축약된 `build_external_id_index()`(document_id+evidence_grade만)를 받아, tie-break가 사실상 grade→id뿐이고 active/source-tier는 적용되지 않는다 — 실무 영향은 적지만(이 두 extractor의 대상인 CITECTS/evidence_ref 참조는 외부 중복 소스가 드묾), 다음 extractor version bump 때 `page_identity_index`를 같이 넘기도록 고쳐야 완전해진다. | `tests/test_graph_identity.py`(7), `tests/test_graph_extract.py`의 hierarchy 테스트, `tests/test_graph_neo4j_client.py::test_merge_document_parent_of_to_unresolved_page_does_not_create_document`, `tests/test_graph_pipeline_db.py::test_sync_document_resolves_direct_parent_via_page_identity_index`(TGW 재현 입력 그대로), `tests/test_graph_router.py`의 fan-out/자기제외/null-external_id 3종. **실제 dev 코퍼스 조회**: external_id가 confluence_map/confluence_docs/tech_repo 세 소스 밖으로 번지는 교차 소스 충돌 0건(`artifacts/citec-kb-graph-correctness-20261012/evidence/focused_check_before_after.md`) — identity resolver가 incident_reports 등 다른 tier를 잘못 대표로 고를 실제 데이터 시나리오는 이번 코퍼스에 없음을 확인. |
| G02 (PARENT_OF 방향) | **완료** | `extract_hierarchy`가 직계 부모 1개만 emit, `Neo4jClient._merge_edges`가 PARENT_OF만 source/target을 뒤집어 parent→child로 씀. | `test_merge_document_parent_of_is_written_parent_to_child`, `test_sync_document_resolves_direct_parent_via_page_identity_index`(grandparent edge가 없음을 확인) |
| G03 (재동기화 교체) | **완료** | `_replace_owned_edges_tx`가 머지 전 소유 edge를 삭제(PARENT_OF는 incoming, 나머지는 outgoing). `_dedup_edges_prefer_extracted`로 EXTRACTED/INFERRED 충돌을 쓰기 전에 해소. **추가 수정**(1차 리뷰에서 발견 — 실제 재백필 전에 잡음): 이 incoming-삭제만으로는 "수정 전 코드가 쓴 (child)-[:PARENT_OF]->(raw-id ghost)" 유령(=이 문서 기준 outgoing)이 전혀 지워지지 않았다. 단순히 outgoing PARENT_OF를 전부 지우면 "이 문서가 다른 문서의 정상 부모인" 경우(지금 모델에서 유효한 outgoing)까지 같이 부순다 — 그래서 모든 managed edge에 `extractor_version`/`generation`을 쓰기 시점에 찍고, outgoing PARENT_OF 중 **이 스탬프가 없는 것만** 유령으로 간주해 지운다. | `test_merge_document_resync_replaces_owned_parent_of_edge`, `test_merge_document_resync_removes_vanished_component_mention`, `test_merge_document_resync_removes_legacy_unstamped_parent_of_ghost`(유령은 지우고 정상 outgoing PARENT_OF는 보존됨을 함께 확인) |
| G04 (제외 필터, 최소 구현) | **완료(최소 범위) — 실제 데이터 재현은 BLOCKED** | `app.graph.explore.filter_excluded_documents` + `/v1/graph/explore`의 `exclude_page_ids`/`exclude_subtree_ids`/`exclude_source_types` + `kb_graph_explore`의 동명 쉼표구분 인자. dedup/정렬/cap 이전에 적용. | `tests/test_graph_explore.py`(4종), `tests/test_graph_router.py::test_explore_excludes_document_by_page_id`(합성 fixture). **BLOCKED**: dev Postgres에 평가보고서 page_id `2586676618`가 아예 존재하지 않음(`SELECT count(*) FROM documents WHERE external_id='2586676618'` → 0) — dev에서는 "제외 전/후 동일 응답"을 재현할 수 없었고, 필터 자체가 켜지면 동작한다는 것만 합성 fixture로 확인했다. 실제 평가보고서 누출 재현은 운영 DB 접근이 있어야 가능. |
| | 잔여 | 중간 경로 bridge 차단(제외된 페이지를 거쳐야만 나오는 후보 차단), anchor 자신이 제외 대상일 때 `filtered` 상태 응답, exclude_subtree_ids의 PG 조회 비용 최적화 | 미구현 — 다음 라운드 |
| G05 (동기화 세대/신선도) | **대부분 완료, REST 상태 노출은 미구현이라 partial 유지** | `graph_sync_state.graph_generation`(마이그레이션 `20261012_0011`), `graph_runs` 테이블, hub 재집계 실패가 `completed`/exit 0으로 숨겨지던 것을 `completed_warn`/exit 1로, 루프 중 예상 못 한 예외가 나면 `graph_runs`가 `running`으로 영원히 남던 것을 `failed`로 전환. **2차 수정**: `--generation`은 사람이 깜빡하면 그대로 재발하는 구조였다(코드 상수 `DEFAULT_GENERATION`에 fallback) — 이제 `sync_cli`가 대상 Neo4j 자신에 `(:GraphMeta {key:'generation'})` 마커를 읽고, 없으면 `f"{EXTRACTOR_VERSION}.{random}"`로 새로 발급해 쓴다. `--generation`을 명시하면 그 값이 마커를 덮어쓴다. | `test_mark_synced_records_generation`, `test_sync_document_resyncs_when_generation_changes`, `scripts/verify/generation_mint_and_skip.sh` 실제 실행 결과(scratch, `artifacts/citec-kb-graph-correctness-20261012/evidence/generation_mint_and_skip.log`) — run1 마커 없음→발급→synced=84/skipped=0, run2 같은 마커→synced=0/skipped=84, 마커 삭제 후 run3 새 마커→synced=84/skipped=0. `scripts/verify/hub_recompute_failure_injection.py`·`crash_mid_run_injection.py` 실행 결과도 같은 디렉터리에 저장(`completed_warn`/exit 1, `failed` 전환 확인). |
| | 잔여 | `mark_synced` 자체 실패는 여전히 "synced" 반환(그래프 쓰기는 성공했으므로 의도적 — 단 `graph_runs`에 이 세부 실패가 안 보임), `unresolved_references` 카운트 미연동(항상 0), REST `/v1/graph/status` 미구현(as_of만 유지) | 미구현/의도적 설계 |

## P1(이번 라운드에서 함께 처리된 항목)

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G06 (별칭) | **부분 완료** | `data/seeds/lexicon/core.json`의 NetApp에 `netapp`/`넷앱` 추가. `seed_lexicon`/`POST /v1/lexicon/seed` 기본값을 `replace=False`(비파괴 upsert)로 변경 — 이번 세션 중 운영에서 `?replace=true`를 호출해 10건이 **교체**(삭제 후 75건 재삽입)된 적이 있다. 그 10건 중 seed 파일에 없는 수동 추가 항목이 있었는지는 **확인하지 않았다** — 데이터 손실이 실제로 있었다고 주장할 근거는 없다. `extract_lexicon_components`가 토큰 집합 대신 변형어 문자열(긴 것 먼저) 검사로 바뀌어 다중 단어 구문과 조사 붙은 한글을 매칭. **한글 한 글자 오탐 수정**: 단순 포함 검사가 "행"(hang)을 "진행"/"수행"/"행정"/"행사"/"1행"(표의 행) 안에서도 오탐시켜, 한 글자짜리 한글 변형어는 그래프 매칭에서 완전히 제외(seed/검색 FTS에는 유지). **ASCII 경계 재설계**(2차 리뷰 — 실제 코퍼스 2000건 샘플 diff로 재현): `\b`는 `/`,`-`,`.`을 전부 경계로 봐서, 과거 `_TOKEN_RE`(이 문자들을 토큰에 포함)로는 안 걸리던 "/sys/fs/cgroup"의 fs, "ceph-mds=enabled"의 mds, "scp-pbupa02"의 scp가 새로 오탐됐다 — 좌우 커스텀 경계(인접 `[a-z0-9_/-]` 금지, 점(.) 뒤 식별자 금지)로 과거 정밀도를 되살리면서 다중 단어/문장부호/조사(`Redis가`,`NetApp에서`) 이득은 유지. 재현 diff와 수정 후 재검증 결과는 `artifacts/citec-kb-graph-correctness-20261012/evidence/lexicon_matcher_diff_2000docs.log`. | `tests/test_graph_extract.py`의 lexicon 10여 종(멀티워드, 조사, ASCII 경계, 한글 오탐 방지, 경로/호스트명/점구분 키 오탐 방지, 문장부호/조사 유지) |
| | 잔여/미해결 결정 | `load_lexicon_map()`의 프로세스별 `lru_cache`가 여러 API 워커 간 version 기반으로 동기화 안 됨. **긴 구문 우선 매칭이 실제로는 상호배타가 아니다** — 변형어별로 독립 검사하므로 실제 lexicon에서 "NetApp Cluster"가 본문에 있으면 NetApp_Cluster/NetApp/Cluster 세 canonical이 전부 집계된다(스펙 §2.5의 "가장 긴 등록 구문을 구분해 매칭" 요구를 글자 그대로는 아직 안 채움 — Cluster는 hub라 실질 영향은 작지만, "NetApp Cluster 언급이 NetApp에도 자동 연결돼야 하는가"는 판단이 필요해 이번 라운드에서 결정하지 않음) | 미구현/열린 결정 |
| G09 (등급·결정적 정렬) | **완료** | `app.graph.identity.GRADE_RANK`로 통합(A/A-/B/C/machine/draft) — `explore.py`/`failure_bucket.py` 둘 다 여기서 가져옴. **2차 수정**: `_sort_key`가 `(hops, grade)`뿐이라 동률이면 Neo4j `collect()`의 비결정적 순서가 그대로 샜다 — id(컴포넌트는 canonical_name)를 최종 tie-break로 추가해 같은 요청을 반복해도 순서가 같아지게 함. **3차 수정**: `dedup_by_key`(복수 앵커 병합 시 중복 제거)도 동률이면 "먼저 본 쪽"이 이겨 입력 순서(앵커 순회 순서)에 의존했다 — (hops, relation, tag) 내용 기반 키로 교체. `_resolve_document_anchor_ids`의 fan-out id 목록도 정렬 추가. | `test_grade_rank_includes_a_minus`, `test_shape_explore_result_sorts_a_minus_before_b_at_same_hop`, `test_shape_explore_result_breaks_ties_deterministically_by_id`, `test_dedup_by_key_tie_break_is_deterministic_regardless_of_input_order` |
| G11 (실제 seed lexicon 테스트) | **완료** | `app.lexicon.seed.build_lexicon_map(terms)`를 순수 함수로 분리(과거 `load_lexicon_map()` 안에 세 군데 중복 — DB 행/파일 fallback/예외 fallback). `@lru_cache`+DB 의존인 `load_lexicon_map()`과 달리 CI에서 DB 없이도 실제 `core.json`으로 바로 테스트 가능해짐. | `test_real_seed_netapp_aliases_share_canonical`, `test_real_seed_netapp_cluster_multiword_variant_matches_in_body`, `test_real_seed_single_syllable_hangul_variant_excluded_from_graph_matching` — 전부 가짜 fixture가 아니라 `load_lexicon_seed_file()`로 읽은 실제 seed 파일로 검증(핸드오프가 지적한 간극 해소) |
| G07 (cap 뒤 접근) | **완료** | `shape_explore_result`가 카테고리별 `*_total`/`*_truncated`를 돌려주고(전역 `truncated` 하나뿐이던 것 대체), `/v1/graph/explore`에 `documents_offset`/`components_offset`/`failure_buckets_offset` 추가. `kb_graph_explore`도 동명 인자로 다음 페이지를 요청할 수 있다. **MCP 렌더링 구멍 발견 즉시 수정**: 처음엔 50개짜리 페이지를 20줄만 찍고 다음 offset을 +50으로 안내해 21~50번째가 영영 안 보였다 — 이제 페이지 전체(최대 50줄)를 그대로 찍는다. | `tests/test_graph_explore.py`의 페이지네이션 2종, `tests/test_graph_router.py::test_explore_documents_offset_pages_beyond_cap`, `mcp-server/test_graph_explore_format.py`(신규, 0/20/21/50/51건 경계 + offset을 따라가며 전부 도달하는지 end-to-end 확인 — **mcp-server/ 테스트는 CI에 안 물려 있음**, 로컬 venv로만 실행) |
| G08 (동일 페이지 수집본 묶기 + PG 보강 통합) | **완료** | `collapse_duplicate_copies()` — confluence_map/confluence_docs/tech_repo가 같은 페이지를 따로 수집한 "사본"을 하나의 논리 후보로 묶고(대표는 기존 identity resolver로 결정) 나머지는 `copies` 배열에 보존. `_fetch_document_enrichment()`로 grade/status/source_type/external_id/space_key/ancestor_ids를 PG 왕복 한 번에 통합(JSONB 전체가 아니라 필요한 두 키만 선택, id 목록도 dedupe) — 과거 `_fetch_evidence_grades`+`_fetch_ancestor_ids` 두 왕복을 대체. `attach_document_access()`(kb_search와 같은 빌더)를 최종 페이지(≤50건)에만 적용해 실제 `mcp_args.path`/`web_url`/`body_kind`를 부여. archived 문서는 `archived` bool로 표시(응답 최상위 `status`와 이름 안 겹치게). **배포 전 리뷰에서 치명적 버그 발견·수정**: 2hop 경로 정보를 `"path"` 키로 저장했는데, 이게 `attach_document_access`의 실제 "원문 접근 path" 의미와 충돌해 `mcp_args.path`/`web_url`의 `path=`/`body_api_file`이 전부 경로 리스트로 덮어써질 뻔했다 — `"route"`로 이름 변경, 회귀 테스트 추가(실제 접근 경로와 route 둘 다 정확한지 함께 확인). | `tests/test_graph_explore.py`의 collapse/enrichment 6종, `tests/test_graph_router.py::test_explore_collapses_duplicate_page_copies_in_results`(실제 PG+Neo4j로 두 수집본이 하나로 묶이고 `access.mcp_args.path`/`web_url`이 정확함을 확인), `mcp-server/test_graph_explore_format.py`에 copies/archived/snapshot 렌더링 2종 추가. **실제 dev NetApp anchor에서 실측**: `documents_total` 7,745 → **7,058**(687건 접힘), `documents_offset=100`부터 다수의 실제 confluence_docs/confluence_map 쌍이 즉시 발견됨, `_EN` 접미사 checkitem은 전혀 안 섞임. 재시간측정: NetApp 1.9~2.26초, symptom_text 1.8~2.13초, document(595824602) 0.09~0.12초 — collapse/enrichment 추가 후에도 유의한 회귀 없음(`artifacts/citec-kb-graph-correctness-20261012/EVIDENCE.md`). |
| G09 잔여 | — | `Neo4jClient.explore()`의 `best_by_id`가 같은 hop에서 동률일 때 보여줄 `relation` 라벨은 여전히 Neo4j `collect()` 순서에 의존(어떤 경로로 도달했다고 "표시"하는지만 비결정적 — 포함 여부·정렬 순서 자체는 결정적). | 미해결 — 영향 적음(같은 노드가 여러 관계로 도달 가능한 드문 경우에만) |
| G10 (failure_bucket 명시 URL 해석) | **완료** | 핸드오프의 "resolution" 필드는 이 스키마의 `recommended_action`(`\d failure_buckets`로 확인 — `resolution` 컬럼 자체가 없음). `app.graph.failure_bucket.decode_confluence_tiny_link()`가 Confluence tiny link(`/x/<code>`)를 로컬 디코딩(fetch 없음)하고, `extract_mentioned_references()`가 `root_cause`+`recommended_action`에서 그 URL과 `pageId=` 쿼리를 찾아 `page_identity_index`로 해석되는 것만 `REFERENCES`(HAS_EVIDENCE 아님, §2.7)로 낸다. `classify_evidence()`/`aggregate_evidence_reasons()`로 resolved/external_artifact/legacy_missing/target_not_ingested/unresolved_reference 사유별 집계를 `sync_cli` JSON 출력에 추가(§2.7 요구). | `tests/test_graph_failure_bucket.py` 13종. **전체 재백필 완료 후 dev에 실제 적용 확인됨**: `--source-ids`로 버킷 재동기화 → `evidence_reasons={"resolved":2,"external_artifact":6,"legacy_missing":4,"target_not_ingested":0,"unresolved_reference":0}`(기대 분포와 정확히 일치), Pacemaker 버킷(`6ea28d59-...`) REFERENCES → `confluence_map:2542097469` 직접 조회로 확인(`artifacts/citec-kb-graph-correctness-20261012/EVIDENCE.md`). |
| G08 잔여 | — | `filtered`/`backend_error` status 미연동(anchor 자체 제외 감지, Neo4j 쿼리 타임아웃 매핑 둘 다 미구현), `found`/`not_found`/`BusinessEntity` 경유 경로 비율 집계 미실시, collapse 시 대표의 hops/route만 보여줘 비대표 수집본이 더 적은 hop으로 도달했어도 그 값이 안 보임(사소함, 비차단) | 미구현 — 다음 라운드 |
| G11 잔여 | — | skip을 실패로 바꾸는 `scripts/verify/` 러너(`GRAPH_REQUIRE_INTEGRATION=1` 등) 미구현. mcp-server를 CI(`.github/workflows/ci.yml`)에 추가하는 건 CI 파이프라인 변경이라 사용자 승인 필요 — 아직 묻지 않음 | 미구현 |

## 부수적으로 발견/수정

- **CI 누락 버그**: `app.main`이 항상 `app.routers.graph`를 import하는데, 그게
  `neo4j_client.py`의 모듈 최상단 `import neo4j`를 항상 실행해 `requirements-ci.txt`
  (neo4j 의도적 제외) 환경에서 `app.main`을 import하는 모든 테스트가 깨지고
  있었다(그래프와 무관한 `test_mock_idp_e2e.py` 포함, PR #16 머지 이후부터 존재).
  `neo4j_client.py`의 import를 optional로 바꾸고 실제 `Neo4jClient()` 생성
  시점에만 에러를 내도록 수정.

## 코드 리뷰에서 걸러낸 것(실제 dev 전체 재백필 전, 총 2회 kill)

전체 dev 재백필(새 generation)을 두 번 kill했다 — `graph_runs`에
`status=running`으로 남은 두 행(`generation=2026-10-12.g1`,
`2026-10-12.g2`). kill은 SIGKILL이라 `sync_cli`의 바깥 try/except(G05 수정)
로도 못 잡는다. dev 전용이라 `running`으로 방치해도 피해는 없다(정확한
전체 이력은 `SELECT generation, status, started_at, completed_at FROM
graph_runs ORDER BY started_at`로 확인 가능).

1. **g1 kill**: G03(PARENT_OF 방향 수정 전 코드가 쓴 유령이 지워지지 않음),
   G06(한글 한 글자 변형어 "행" 오탐) — 둘 다 코드 리뷰만으로 발견, 실행 중
   kill.
2. **g2 kill**: G06 한 글자 제외 규칙으로 고쳤는데도, ASCII `\b` 경계가
   과거보다 오히려 정밀도를 떨어뜨리는 걸 코드 리뷰로 다시 지적받아(아직
   실행 전) kill.
3. 그 사이 코드 리뷰(아무 것도 실행 중이지 않을 때)에서 추가로 찾은 것:
   ASCII 경계 오탐을 실제 코퍼스 2000건 diff로 재현, G01 fan-out의 두 부작용
   (복제본이 서로의 연관 문서로 나옴, `external_id=''`가 SQL `IS NULL`/
   falsy 비교로 fan-out됨), G05 `--generation`의 사람 실수 의존성(마커
   기반으로 구조적으로 제거).
4. **포커스 체크(`--source-ids`로만 재동기화, 전체 실행 아님)**:
   `tech_repo:1153334044`(구 유령 소유자)와 TGW 3문서,
   `confluence_docs:1117787168`를 재동기화해 before/after를 실측하고서야
   전체 실행을 승인했다 — 정상 완료됨(`2026-10-12.3.d2f3f9ca`,
   05:00:08~05:00:22 UTC). 결과는
   `artifacts/citec-kb-graph-correctness-20261012/evidence/`.

4번째(최종) 실행은 `--generation` 없이(=마커 자동 발급/재사용) 실행했고,
완료 결과는 이 문서 하단 "최종 전체 재백필 결과"에 기록한다(실행 중이면
"진행 중"으로 표시).

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
/tmp/graph-ci-venv/bin/python -m pytest tests/ -q
```

`test_ops_dashboard_auth.py`는 **독립 실행하면 통과한다**(CI venv 단독 확인됨) —
위처럼 graph DB 통합 테스트 파일들과 같은 pytest 세션에서 실행하면 실패하는데,
이는 그 파일들이 모듈 최상단에서 `os.environ["DATABASE_URL"] = _PG_DSN`을 설정해
같은 프로세스의 이후 테스트에도 그 값이 새기 때문이다(pre-existing 테스트
격리 문제 — 이번 라운드 전부터 `test_graph_pipeline_db.py`가 쓰던 패턴). 실제
GitHub Actions CI는 `GRAPH_TEST_DATABASE_URL`을 설정하지 않으므로(그래프 통합
테스트는 skip됨) 이 상호작용이 발생하지 않는다 — "무관한 flake"라는 표현은
부정확했고, 정확히는 "로컬에서 통합검증을 전체 테스트와 같은 프로세스로 돌릴 때만
드러나는, 그래프 테스트 쪽의 기존 환경변수 오염 패턴"이다.

## BLOCKED(이번 세션에서 확인 불가)

- confluence-mcp/github-mcp 세션 도구 없음 — 라이브 Confluence 재검증, 10월 9일
  zip 재다운로드·SHA256 확인 미수행. 코드 근거(`evidence/source-excerpts.md`)와
  개발 DB/Neo4j 직접 조회로 대체했다.
- Codex 쪽 스킬 동기화 — 이 호스트/세션에는 Codex 환경 접근이 없다. §3.4가
  요구하는 "Codex 공유 원본 동기화"는 패치 절차만 남기고 미검증으로 둔다.

## 설치된 스킬 조사 결과(§3.4)

`citec-mcp-workbench`라는 이름의 스킬은 이 호스트 `~/.claude/skills/`
(find-skills/pisa-item-generator/synced/token-diet뿐)와
`~/.claude/plugins/cache/`를 뒤져도 **어디에도 설치돼 있지 않다** — 정본은
이 저장소의 `docs/CITEC_KB_MCP_SKILL.md`(와 그 미러 `apps/web/public/docs/
CITEC_KB_MCP_SKILL.md`)뿐이고, 둘 다 이번에 `kb_graph_explore` 섹션을
추가했다(위 커밋). 비교 대상 "설치된 캐시 사본"이 없으므로 diff할 것이 없다.

단, `citec-kb`를 참조하는 **다른** 설치된 플러그인 스킬이 하나 있다 —
`~/.claude/plugins/cache/packet-capture-rca-local/packet-capture-rca/0.3.3/
skills/packet-capture-rca/references/citec-kb-integration.md`. 이 문서는
`kb_match_failure_bucket`/`kb_search`/`kb_list_failure_buckets`/
`kb_similar_incident`/`kb_get_failure_bucket`/`kb_register_failure_bucket`/
`kb_refine_failure_bucket`만 언급하고 **`kb_graph_explore`는 없다**. 이
스킬의 범위(failure_bucket 자가개선 루프)상 graph 탐색이 필수는 아니라
결함으로 단정하지 않지만, "매칭된 패턴을 더 깊이 보고 싶을 때" 후속 단계로
넣을지는 그 플러그인의 소스(이 저장소 밖, `packet-capture-rca` 저장소)를
고치는 별도 작업 — 캐시 사본은 손대지 않는다(이 세션에서 변경 안 함).

## 최종 전체 재백필 결과 — 완료

`sync_cli`(`--generation` 미지정, dev Neo4j 마커 자동 발급 `2026-10-12.3.d2f3f9ca`)
2026-10-12 05:00:52~07:02:01 UTC(~2h1m). `{"documents": {"synced": 114925,
"skipped": 5, "failed": 0}, "failure_buckets": {"synced": 8, "failed": 0},
"hub_recompute_ok": true}`. `graph_runs.status = completed`. 원시 로그/상세
수치는 `artifacts/citec-kb-graph-correctness-20261012/EVIDENCE.md` "전체
재백필 완료 결과" 섹션.

- **Stamp check**: `extractor_version` 불일치/누락 edge **0건** — 전체 코퍼스가
  한 번 완전히 새 코드로 교체됨.
- **고립 유령**: `source_type IS NULL` Document **827개**, 전부 완전히 고립
  (edge 0개) — 수정 전 raw-id 유령의 잔존. 백업 후 삭제는 별도 승인 대상,
  이번엔 삭제하지 않음.
- **UnresolvedPage**: 50개(새 설계대로 정상 생성).
- **PG-Neo4j 일치**: Neo4j `Document`(source_type NOT NULL) 114930건 ==
  PG `documents` 114930건, 불일치 0건.
- **Hub 재확인**: `Network`/`SCP`/`Storage`/`Linux` 등 13개 실측 hub(Ceph,
  Cluster, Firewall, Kubernetes, Linux, Network, OpenStack, Oracle, SCP,
  Storage, VMware, Windows, timeout) — Phase 1의 하드코딩 13종 목록이
  SCP/Kubernetes를 놓쳤던 문제가 실측으로 해결됨을 전체 코퍼스 기준 재확인.
- **G10 적용**: api/mcp 재시작 후 `--source-ids`로 버킷 재동기화,
  `evidence_reasons={"resolved":2,"external_artifact":6,"legacy_missing":4,
  "target_not_ingested":0,"unresolved_reference":0}`(기대 분포와 정확히 일치),
  Pacemaker 버킷(`6ea28d59-...`) REFERENCES → `confluence_map:2542097469`
  확인됨 — **G10 완료로 전환**.
- **실제 Claude Code MCP 체인(이 세션의 실제 `mcp__wiki-mcp__*` 호출, 스크립트
  아님)**:
  - `kb_search("상암 수원 TGW Transit Gateway RTT 이전 개선")` — 측정표 원문은
    top-10에 안 나옴(핸드오프가 지적한 원래 실패와 일치).
  - `kb_graph_explore(document, confluence_map:595824602)` — 루트에서
    `confluence_map:604365046`("1. Network")이 **offset 없이 첫 페이지에서
    바로 보임**(2hop/PARENT_OF) — G07/G01/G02가 실제로 이 케이스를 고쳤음을
    확인. `confluence_map:604365026`도 1hop에서 보임.
  - `kb_get_document(confluence_map/604365046.md)` — 본문에서 Multi-AZ
    Transit Gateway Ping 1분 측정값 **Server 1→1 2.1ms, 1→2 1.1ms, 2→1
    1.2ms, 2→2 2.0ms**를 그대로 확인(핸드오프 표와 정확히 일치). 이동 전후
    개선량 데이터는 본문에 없음(= 미확인으로 두는 것이 맞음, 핸드오프 경고와
    일치).
  - `kb_graph_explore(symptom_text, "넷앱 스토리지 지연 장애")` — `매칭된
    컴포넌트: NetApp, Storage` 정상 매칭. 전체 코퍼스 기준 연관 문서
    7,745건(샘플 500건 규모였던 이전 테스트보다 훨씬 큼 — 실제 운영 규모를
    반영, G07 페이지네이션이 바로 이런 경우를 위한 것임을 재확인).
- **타이밍**: `/v1/graph/explore` component 앵커(NetApp) 3회 호출 평균
  ~1.3초, document 앵커(TGW 루트) 1회 ~0.08초 — MCP 30초 타임아웃에 전혀
  근접하지 않음.

## 정리(approved)

- Docker 빌드 캐시(9.5GB) + 미사용 이미지(사용자 승인, "정리 진행" 선택) 정리됨
  — 태그가 있던 이미지 중 `xfetch-backend`/`xfetch-frontend`도 함께 제거됨(이
  리포지토리와 무관한 다른 프로젝트 이미지로 보이며, 미사용으로 판단되어 제거됨).
- 세션에서 만든 scratch Neo4j 컨테이너(`citec-graph-test-neo4j`)와 Postgres
  scratch DB(`citec_graph_test`) 모두 전체 재백필·검증 완료 후 제거함.
