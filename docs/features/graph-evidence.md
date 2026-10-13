# 지식그래프 정합성 — G01~G11 추적 (2026-10-12 핸드오프 대응)

`/tmp/CLAUDE_CODE_PROMPT.md`(분석 커밋 `39fd123`)의 G01~G11을 추적한다. 각
항목: 재현 여부 → 원인 → 수정 커밋/파일 → 검증 증거 → 잔여.

## P0

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G01 (식별자 resolver) | **거의 완료 — 범위 한정 있음** | `apps/api/app/graph/identity.py`(신규), `extract_hierarchy`/`routers/graph._resolve_document_anchor_ids`가 결정적 tie-break(active>grade>source tier>id) 사용. 미해결 부모는 `UnresolvedPage`(Document 아님). document 앵커가 대표가 아닌 쪽 복제본으로 걸려도 같은 external_id의 모든 수집본에 대해 explore()를 수행하고 기존 다중 앵커 병합 로직으로 합친다(복제본 자신은 fan-out 부작용으로 "연관 문서"에서 제외, 2차 리뷰에서 발견해 수정). **범위 한정 — 완료(2026-10-13)**: `extract_evidence`는 이미 이전 라운드에 `page_identity_index`를 받도록 고쳐져 있었다(`sync_failure_bucket`의 `richer_index`). `extract_references`만 `sync_document`가 넘기는 축약 인덱스(`external_id_index`, document_id+evidence_grade만)에 묶여 있었던 게 실제 잔여였다 — 이제 `sync_document`가 `page_identity_index`를 우선으로 넘긴다(아래 "P1 잔여 처리" 절), EXTRACTOR_VERSION `2026-10-13.1`로 다음 전체 재백필에 반영됨. | `tests/test_graph_identity.py`(7), `tests/test_graph_extract.py`의 hierarchy 테스트, `tests/test_graph_neo4j_client.py::test_merge_document_parent_of_to_unresolved_page_does_not_create_document`, `tests/test_graph_pipeline_db.py::test_sync_document_resolves_direct_parent_via_page_identity_index`(TGW 재현 입력 그대로), `tests/test_graph_router.py`의 fan-out/자기제외/null-external_id 3종. **실제 dev 코퍼스 조회**: external_id가 confluence_map/confluence_docs/tech_repo 세 소스 밖으로 번지는 교차 소스 충돌 0건(`artifacts/citec-kb-graph-correctness-20261012/evidence/focused_check_before_after.md`) — identity resolver가 incident_reports 등 다른 tier를 잘못 대표로 고를 실제 데이터 시나리오는 이번 코퍼스에 없음을 확인. |
| G02 (PARENT_OF 방향) | **완료** | `extract_hierarchy`가 직계 부모 1개만 emit, `Neo4jClient._merge_edges`가 PARENT_OF만 source/target을 뒤집어 parent→child로 씀. | `test_merge_document_parent_of_is_written_parent_to_child`, `test_sync_document_resolves_direct_parent_via_page_identity_index`(grandparent edge가 없음을 확인) |
| G03 (재동기화 교체) | **완료** | `_replace_owned_edges_tx`가 머지 전 소유 edge를 삭제(PARENT_OF는 incoming, 나머지는 outgoing). `_dedup_edges_prefer_extracted`로 EXTRACTED/INFERRED 충돌을 쓰기 전에 해소. **추가 수정**(1차 리뷰에서 발견 — 실제 재백필 전에 잡음): 이 incoming-삭제만으로는 "수정 전 코드가 쓴 (child)-[:PARENT_OF]->(raw-id ghost)" 유령(=이 문서 기준 outgoing)이 전혀 지워지지 않았다. 단순히 outgoing PARENT_OF를 전부 지우면 "이 문서가 다른 문서의 정상 부모인" 경우(지금 모델에서 유효한 outgoing)까지 같이 부순다 — 그래서 모든 managed edge에 `extractor_version`/`generation`을 쓰기 시점에 찍고, outgoing PARENT_OF 중 **이 스탬프가 없는 것만** 유령으로 간주해 지운다. | `test_merge_document_resync_replaces_owned_parent_of_edge`, `test_merge_document_resync_removes_vanished_component_mention`, `test_merge_document_resync_removes_legacy_unstamped_parent_of_ghost`(유령은 지우고 정상 outgoing PARENT_OF는 보존됨을 함께 확인) |
| G04 (제외 필터, 최소 구현) | **완료(최소 범위) — 실제 데이터 재현은 BLOCKED** | `app.graph.explore.filter_excluded_documents` + `/v1/graph/explore`의 `exclude_page_ids`/`exclude_subtree_ids`/`exclude_source_types` + `kb_graph_explore`의 동명 쉼표구분 인자. dedup/정렬/cap 이전에 적용. | `tests/test_graph_explore.py`(4종), `tests/test_graph_router.py::test_explore_excludes_document_by_page_id`(합성 fixture). **BLOCKED**: dev Postgres에 평가보고서 page_id `2586676618`가 아예 존재하지 않음(`SELECT count(*) FROM documents WHERE external_id='2586676618'` → 0) — dev에서는 "제외 전/후 동일 응답"을 재현할 수 없었고, 필터 자체가 켜지면 동작한다는 것만 합성 fixture로 확인했다. 실제 평가보고서 누출 재현은 운영 DB 접근이 있어야 가능. |
| | 잔여 | 중간 경로 bridge 차단(제외된 페이지를 거쳐야만 나오는 후보 차단), anchor 자신이 제외 대상일 때 `filtered` 상태 응답, exclude_subtree_ids의 PG 조회 비용 최적화 | **bridge 차단(page_ids/source_types)·filtered 응답 완료(2026-10-13, 아래 "P1 잔여 처리" 절)** — exclude_subtree_ids bridge 차단·PG 비용 최적화는 여전히 미구현 |
| G05 (동기화 세대/신선도) | **대부분 완료, REST 상태 노출은 미구현이라 partial 유지** | `graph_sync_state.graph_generation`(마이그레이션 `20261012_0011`), `graph_runs` 테이블, hub 재집계 실패가 `completed`/exit 0으로 숨겨지던 것을 `completed_warn`/exit 1로, 루프 중 예상 못 한 예외가 나면 `graph_runs`가 `running`으로 영원히 남던 것을 `failed`로 전환. **2차 수정**: `--generation`은 사람이 깜빡하면 그대로 재발하는 구조였다(코드 상수 `DEFAULT_GENERATION`에 fallback) — 이제 `sync_cli`가 대상 Neo4j 자신에 `(:GraphMeta {key:'generation'})` 마커를 읽고, 없으면 `f"{EXTRACTOR_VERSION}.{random}"`로 새로 발급해 쓴다. `--generation`을 명시하면 그 값이 마커를 덮어쓴다. | `test_mark_synced_records_generation`, `test_sync_document_resyncs_when_generation_changes`, `scripts/verify/generation_mint_and_skip.sh` 실제 실행 결과(scratch, `artifacts/citec-kb-graph-correctness-20261012/evidence/generation_mint_and_skip.log`) — run1 마커 없음→발급→synced=84/skipped=0, run2 같은 마커→synced=0/skipped=84, 마커 삭제 후 run3 새 마커→synced=84/skipped=0. `scripts/verify/hub_recompute_failure_injection.py`·`crash_mid_run_injection.py` 실행 결과도 같은 디렉터리에 저장(`completed_warn`/exit 1, `failed` 전환 확인). |
| | 잔여 | `mark_synced` 자체 실패는 여전히 "synced" 반환(그래프 쓰기는 성공했으므로 의도적 — 단 `graph_runs`에 이 세부 실패가 안 보임), `unresolved_references` 카운트 미연동(항상 0), REST `/v1/graph/status` 미구현(as_of만 유지) | `unresolved_references` 집계·`GET /v1/graph/status` **완료(2026-10-13)** — `mark_synced` 실패 시 "synced" 반환은 의도적 설계로 유지 |

## P1(이번 라운드에서 함께 처리된 항목)

| ID | 상태 | 수정 | 검증 |
|---|---|---|---|
| G06 (별칭) | **부분 완료** | `data/seeds/lexicon/core.json`의 NetApp에 `netapp`/`넷앱` 추가. `seed_lexicon`/`POST /v1/lexicon/seed` 기본값을 `replace=False`(비파괴 upsert)로 변경 — 이번 세션 중 운영에서 `?replace=true`를 호출해 10건이 **교체**(삭제 후 75건 재삽입)된 적이 있다. 그 10건 중 seed 파일에 없는 수동 추가 항목이 있었는지는 **확인하지 않았다** — 데이터 손실이 실제로 있었다고 주장할 근거는 없다. `extract_lexicon_components`가 토큰 집합 대신 변형어 문자열(긴 것 먼저) 검사로 바뀌어 다중 단어 구문과 조사 붙은 한글을 매칭. **한글 한 글자 오탐 수정**: 단순 포함 검사가 "행"(hang)을 "진행"/"수행"/"행정"/"행사"/"1행"(표의 행) 안에서도 오탐시켜, 한 글자짜리 한글 변형어는 그래프 매칭에서 완전히 제외(seed/검색 FTS에는 유지). **ASCII 경계 재설계**(2차 리뷰 — 실제 코퍼스 2000건 샘플 diff로 재현): `\b`는 `/`,`-`,`.`을 전부 경계로 봐서, 과거 `_TOKEN_RE`(이 문자들을 토큰에 포함)로는 안 걸리던 "/sys/fs/cgroup"의 fs, "ceph-mds=enabled"의 mds, "scp-pbupa02"의 scp가 새로 오탐됐다 — 좌우 커스텀 경계(인접 `[a-z0-9_/-]` 금지, 점(.) 뒤 식별자 금지)로 과거 정밀도를 되살리면서 다중 단어/문장부호/조사(`Redis가`,`NetApp에서`) 이득은 유지. 재현 diff와 수정 후 재검증 결과는 `artifacts/citec-kb-graph-correctness-20261012/evidence/lexicon_matcher_diff_2000docs.log`. | `tests/test_graph_extract.py`의 lexicon 10여 종(멀티워드, 조사, ASCII 경계, 한글 오탐 방지, 경로/호스트명/점구분 키 오탐 방지, 문장부호/조사 유지) |
| | 잔여/미해결 결정 | `load_lexicon_map()`의 프로세스별 `lru_cache`가 여러 API 워커 간 version 기반으로 동기화 안 됨. **긴 구문 우선 매칭이 실제로는 상호배타가 아니다** — 변형어별로 독립 검사하므로 실제 lexicon에서 "NetApp Cluster"가 본문에 있으면 NetApp_Cluster/NetApp/Cluster 세 canonical이 전부 집계된다(스펙 §2.5의 "가장 긴 등록 구문을 구분해 매칭" 요구를 글자 그대로는 아직 안 채움 — Cluster는 hub라 실질 영향은 작지만, "NetApp Cluster 언급이 NetApp에도 자동 연결돼야 하는가"는 판단이 필요해 이번 라운드에서 결정하지 않음) | **둘 다 완료(2026-10-13)** — 사용자가 "긴 구문 우선"으로 결정, 소비된 구간은 재매칭 안 함(아래 "P1 잔여 처리" 절). lru_cache는 DB 지문 기반 캐시로 교체(워커 간 자동 동기화) |
| G09 (등급·결정적 정렬) | **완료** | `app.graph.identity.GRADE_RANK`로 통합(A/A-/B/C/machine/draft) — `explore.py`/`failure_bucket.py` 둘 다 여기서 가져옴. **2차 수정**: `_sort_key`가 `(hops, grade)`뿐이라 동률이면 Neo4j `collect()`의 비결정적 순서가 그대로 샜다 — id(컴포넌트는 canonical_name)를 최종 tie-break로 추가해 같은 요청을 반복해도 순서가 같아지게 함. **3차 수정**: `dedup_by_key`(복수 앵커 병합 시 중복 제거)도 동률이면 "먼저 본 쪽"이 이겨 입력 순서(앵커 순회 순서)에 의존했다 — (hops, relation, tag) 내용 기반 키로 교체. `_resolve_document_anchor_ids`의 fan-out id 목록도 정렬 추가. | `test_grade_rank_includes_a_minus`, `test_shape_explore_result_sorts_a_minus_before_b_at_same_hop`, `test_shape_explore_result_breaks_ties_deterministically_by_id`, `test_dedup_by_key_tie_break_is_deterministic_regardless_of_input_order` |
| G11 (실제 seed lexicon 테스트) | **완료** | `app.lexicon.seed.build_lexicon_map(terms)`를 순수 함수로 분리(과거 `load_lexicon_map()` 안에 세 군데 중복 — DB 행/파일 fallback/예외 fallback). `@lru_cache`+DB 의존인 `load_lexicon_map()`과 달리 CI에서 DB 없이도 실제 `core.json`으로 바로 테스트 가능해짐. | `test_real_seed_netapp_aliases_share_canonical`, `test_real_seed_netapp_cluster_multiword_variant_matches_in_body`, `test_real_seed_single_syllable_hangul_variant_excluded_from_graph_matching` — 전부 가짜 fixture가 아니라 `load_lexicon_seed_file()`로 읽은 실제 seed 파일로 검증(핸드오프가 지적한 간극 해소) |
| G07 (cap 뒤 접근) | **완료** | `shape_explore_result`가 카테고리별 `*_total`/`*_truncated`를 돌려주고(전역 `truncated` 하나뿐이던 것 대체), `/v1/graph/explore`에 `documents_offset`/`components_offset`/`failure_buckets_offset` 추가. `kb_graph_explore`도 동명 인자로 다음 페이지를 요청할 수 있다. **MCP 렌더링 구멍 발견 즉시 수정**: 처음엔 50개짜리 페이지를 20줄만 찍고 다음 offset을 +50으로 안내해 21~50번째가 영영 안 보였다 — 이제 페이지 전체(최대 50줄)를 그대로 찍는다. | `tests/test_graph_explore.py`의 페이지네이션 2종, `tests/test_graph_router.py::test_explore_documents_offset_pages_beyond_cap`, `mcp-server/test_graph_explore_format.py`(신규, 0/20/21/50/51건 경계 + offset을 따라가며 전부 도달하는지 end-to-end 확인 — **mcp-server/ 테스트는 CI에 안 물려 있음**, 로컬 venv로만 실행) |
| G08 (동일 페이지 수집본 묶기 + PG 보강 통합) | **완료** | `collapse_duplicate_copies()` — confluence_map/confluence_docs/tech_repo가 같은 페이지를 따로 수집한 "사본"을 하나의 논리 후보로 묶고(대표는 기존 identity resolver로 결정) 나머지는 `copies` 배열에 보존. `_fetch_document_enrichment()`로 grade/status/source_type/external_id/space_key/ancestor_ids를 PG 왕복 한 번에 통합(JSONB 전체가 아니라 필요한 두 키만 선택, id 목록도 dedupe) — 과거 `_fetch_evidence_grades`+`_fetch_ancestor_ids` 두 왕복을 대체. `attach_document_access()`(kb_search와 같은 빌더)를 최종 페이지(≤50건)에만 적용해 실제 `mcp_args.path`/`web_url`/`body_kind`를 부여. archived 문서는 `archived` bool로 표시(응답 최상위 `status`와 이름 안 겹치게). **배포 전 리뷰에서 치명적 버그 발견·수정**: 2hop 경로 정보를 `"path"` 키로 저장했는데, 이게 `attach_document_access`의 실제 "원문 접근 path" 의미와 충돌해 `mcp_args.path`/`web_url`의 `path=`/`body_api_file`이 전부 경로 리스트로 덮어써질 뻔했다 — `"route"`로 이름 변경, 회귀 테스트 추가(실제 접근 경로와 route 둘 다 정확한지 함께 확인). | `tests/test_graph_explore.py`의 collapse/enrichment 6종, `tests/test_graph_router.py::test_explore_collapses_duplicate_page_copies_in_results`(실제 PG+Neo4j로 두 수집본이 하나로 묶이고 `access.mcp_args.path`/`web_url`이 정확함을 확인), `mcp-server/test_graph_explore_format.py`에 copies/archived/snapshot 렌더링 2종 추가. **실제 dev NetApp anchor에서 실측**: `documents_total` 7,745 → **7,058**(687건 접힘), `documents_offset=100`부터 다수의 실제 confluence_docs/confluence_map 쌍이 즉시 발견됨, `_EN` 접미사 checkitem은 전혀 안 섞임. 재시간측정: NetApp 1.9~2.26초, symptom_text 1.8~2.13초, document(595824602) 0.09~0.12초 — collapse/enrichment 추가 후에도 유의한 회귀 없음(`artifacts/citec-kb-graph-correctness-20261012/EVIDENCE.md`). |
| G09 잔여 | — | `Neo4jClient.explore()`의 `best_by_id`가 같은 hop에서 동률일 때 보여줄 `relation` 라벨은 여전히 Neo4j `collect()` 순서에 의존(어떤 경로로 도달했다고 "표시"하는지만 비결정적 — 포함 여부·정렬 순서 자체는 결정적). | **완료(2026-10-13)** — explore() Cypher의 ORDER BY에 elementId(r)를 최종 tie-break로 추가(아래 "P1 잔여 처리" 절) |
| G10 (failure_bucket 명시 URL 해석) | **완료** | 핸드오프의 "resolution" 필드는 이 스키마의 `recommended_action`(`\d failure_buckets`로 확인 — `resolution` 컬럼 자체가 없음). `app.graph.failure_bucket.decode_confluence_tiny_link()`가 Confluence tiny link(`/x/<code>`)를 로컬 디코딩(fetch 없음)하고, `extract_mentioned_references()`가 `root_cause`+`recommended_action`에서 그 URL과 `pageId=` 쿼리를 찾아 `page_identity_index`로 해석되는 것만 `REFERENCES`(HAS_EVIDENCE 아님, §2.7)로 낸다. `classify_evidence()`/`aggregate_evidence_reasons()`로 resolved/external_artifact/legacy_missing/target_not_ingested/unresolved_reference 사유별 집계를 `sync_cli` JSON 출력에 추가(§2.7 요구). | `tests/test_graph_failure_bucket.py` 13종. **전체 재백필 완료 후 dev에 실제 적용 확인됨**: `--source-ids`로 버킷 재동기화 → `evidence_reasons={"resolved":2,"external_artifact":6,"legacy_missing":4,"target_not_ingested":0,"unresolved_reference":0}`(기대 분포와 정확히 일치), Pacemaker 버킷(`6ea28d59-...`) REFERENCES → `confluence_map:2542097469` 직접 조회로 확인(`artifacts/citec-kb-graph-correctness-20261012/EVIDENCE.md`). |
| G08 잔여 | — | `filtered`/`backend_error` status 미연동(anchor 자체 제외 감지, Neo4j 쿼리 타임아웃 매핑 둘 다 미구현), `found`/`not_found`/`BusinessEntity` 경유 경로 비율 집계 미실시, collapse 시 대표의 hops/route만 보여줘 비대표 수집본이 더 적은 hop으로 도달했어도 그 값이 안 보임(사소함, 비차단) | **filtered/backend_error·min-hop 완료(2026-10-13)**(아래 절) — 경로 비율 집계는 측정 항목이라 여전히 미실시 |
| G11 잔여 | — | skip을 실패로 바꾸는 `scripts/verify/` 러너(`GRAPH_REQUIRE_INTEGRATION=1` 등) 미구현. mcp-server를 CI(`.github/workflows/ci.yml`)에 추가하는 건 CI 파이프라인 변경이라 사용자 승인 필요 — 아직 묻지 않음 | **러너 완료(2026-10-13)**(`scripts/verify/run_integration_tests.sh`, 아래 절) — mcp-server CI 추가는 이번에도 범위 밖(사용자 확인) |

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

## 2026-10-13 — P1 잔여 처리 (branch `graph-correctness-g01-g11-remainder`)

운영 배포·재백필(2026-10-12) 완료 후, 위 표의 "잔여" 행 대부분을 처리했다.
배포 비용을 줄이려고 두 묶음으로 나눴다: extraction 결과가 바뀌는 것(전체
재백필 1회로 모아서 받아야 함)과, API/MCP 재시작만으로 끝나는 것.

### 결정(브레인스토밍 생략 — 스펙이 이미 있던 항목들, 세 가지만 사용자에게 확인)

- **G06 다중 단어 매칭 — 긴 구문 우선으로 확정**: 스펙 §2.5 "가장 긴 등록
  구문을 구분해 매칭"을 글자 그대로 적용하기로 사용자가 확인. 구현은
  "소비된 구간(span)" 추적 — 긴 변형어가 매칭된 구간에서는 짧은 변형어가
  재매칭되지 않는다(다른 구간에선 그대로 매칭됨). extraction 결과가 바뀌므로
  EXTRACTOR_VERSION을 올렸다(`2026-10-13.1`).
- **고립 유령 노드(827개, source_type IS NULL)**: 이번에도 보류 — 삭제하지
  않음(별도 승인 대상으로 유지).
- **mcp-server를 CI에 추가**: 이번 라운드에서 손대지 않음(범위 밖으로 유지).

### extraction-affecting (다음 전체 재백필에 반영됨 — EXTRACTOR_VERSION `2026-10-13.1`)

- **G01 잔여 해소**: `sync_document`가 `extract_references`에 넘기는
  reference_index를 이제 `page_identity_index`(active/grade/source-tier/id
  전체 tie-break)로 만든다 — 과거엔 grade/id만 보는 축약 인덱스를 썼다
  (`extract_evidence`는 이전 라운드에서 이미 고쳐져 있었음, 코드 재확인).
- **G06 긴 구문 우선 매칭**: 위 결정대로 구현(`extract_lexicon_components`).
- **G05 `unresolved_references` 집계**: `sync_document`가 UnresolvedPage로
  떨어진 PARENT_OF edge 수를 세어 `graph_runs.unresolved_references`에
  기록(과거엔 컬럼은 있었지만 항상 0).

### restart-only (API/MCP 재시작만으로 반영 — Neo4j 데이터 변경 없음)

- **G04 anchor 자체 제외 → `status="filtered"`**: document 앵커 자신이
  exclude_page_ids/exclude_subtree_ids/exclude_source_types에 걸리면 그래프를
  돌지 않고 즉시 filtered로 답한다.
- **G04 중간 경로 bridge 차단(exclude_subtree_ids 포함)**: `Neo4jClient.explore()`
  가 2hop 중간 노드(p) 단계에서 `exclude_external_ids`/`exclude_source_types`/
  `exclude_subtree_ids`를 Cypher `WHERE`로 직접 거른다(post-filter로는 못
  잡음 — 이미 후보로 모인 뒤에는 "어느 경로로 왔는지"가 사라지기 때문).
  `exclude_subtree_ids` 지원을 위해 `merge_document`가 `Document.ancestor_ids`
  를 PG metadata에서 복제해 Neo4j 노드 속성에도 써둔다(`_DOCUMENT_SET_CLAUSE`)
  — Cypher가 `any(x IN p.ancestor_ids WHERE x IN $exclude_subtree_ids)`로
  직접 판정한다. **부수 효과(G04 PG 조회 비용 잔여도 같이 해소)**: 최종
  문서 후보 목록(수천 건 규모)의 subtree 필터가 이제 `explore()`가 이미
  돌려주는 `Document.ancestor_ids`를 쓰므로, `_fetch_document_enrichment`가
  더 이상 이 컬럼을 PG에서 가져오지 않는다 — anchor 자신의 제외 판정(소수
  건)만 별도의 작은 `_fetch_ancestor_ids()`로 분리.
- **G08 `status="backend_error"`**: `/v1/graph/explore` 중 Neo4j 호출이
  실패하면 503 대신 200 + `status="backend_error"`를 돌려준다(MCP가 body의
  status 필드로 판단하므로 HTTP 에러보다 기계가 구분하기 쉽다).
- **G08 collapse 시 min-hop**: `collapse_duplicate_copies()`가 그룹 중 가장
  가까운(hops 최소) 수집본의 hops/route를 쓴다 — 과거엔 대표 자신의 hops만
  보여줘 더 가까운 비대표 수집본의 정보가 사라졌다.
- **G09 relation 표시 결정성**: `explore()` Cypher의 `ORDER BY`에
  `elementId(r)`를 최종 tie-break로 추가 — 동일 priority의 관계가 둘 있는
  드문 경우에도 표시되는 relation 라벨이 항상 같아진다.
- **G05 `GET /v1/graph/status`**: 최근 `graph_runs` 1행 + 이 Neo4j의
  `GraphMeta` generation 마커 + 둘의 일치 여부를 한 번에 보여준다.
- **G06 lexicon 캐시 멀티워커 동기화**: `load_lexicon_map()`을
  `@lru_cache(maxsize=1)`에서 `(count, max(lexicon_terms.updated_at))` 지문
  기반 캐시로 바꿈 — 다른 워커 프로세스가 `seed_lexicon()`을 돌려도 재시작
  없이 다음 호출에서 반영된다. 마이그레이션 `20261013_0012`로
  `lexicon_terms.updated_at` 추가.
- **G11 통합 테스트 러너**: `scripts/verify/run_integration_tests.sh` —
  그래프 DB/Neo4j 통합 테스트 파일(`tests/test_graph_*_db.py` 등)을 파일별
  별도 pytest 프로세스로 돌려 기존 환경변수 오염 패턴(이 문서 "검증 방법"
  절)을 피하고, `GRAPH_REQUIRE_INTEGRATION=1`이면 scratch env var 미설정
  자체를 실패로 처리한다(과거엔 조용히 skip).

### 안 고친 것(의도적 — 사용자가 이번 라운드에서 직접 확인한 범위 제외)

- **고립 유령 노드 827개 삭제**: 이번에도 보류(사용자가 AskUserQuestion으로
  직접 확인 — "이번에도 보류"). 백업 후 dry-run 스크립트는 여전히 미작성.
- **mcp-server를 CI에 추가**: 이번 라운드에서 손대지 않음(사용자가 직접
  확인 — "아니오, 이번엔 손대지 않음").
- G05 `mark_synced` 자체 실패 시 "synced" 반환 — 의도적 설계(그래프 쓰기는
  이미 성공했으므로), 그대로 유지. 이건 버그가 아니라 설계 결정.
- G08 found/not_found/BusinessEntity 경유 경로 비율 집계 — 이 저장소에
  원본 핸드오프 프롬프트(`/tmp/CLAUDE_CODE_PROMPT.md`)가 더 이상 없어
  정확한 측정 정의를 재확인할 수 없었다. G08 자체(코드)는 완료 상태이고,
  이 항목은 그 코드에 대한 1회성 통계 측정으로 보인다 — 코드 갭이 아니라서
  이번 라운드에서 추측해 만들지 않았다. 사람이 정의를 다시 주면 바로 측정
  가능.
- G09(2차) relation 라벨이 Neo4j `collect()` 순서에 의존하는 잔여 —
  `explore()`에 `elementId(r)` tie-break를 추가해 실제로는 해소됨(위 절),
  행 자체는 역사적 기록으로 남김.

### 2026-10-13(추가) — G04 subtree bridge 차단 + PG 비용 잔여 동시 해소

위 "restart-only" 목록 작성 당시엔 `exclude_subtree_ids`의 2hop bridge
차단을 범위 밖으로 남겼었다("ancestor_ids는 PG 메타데이터뿐이라 Neo4j
쿼리 중엔 안 보임"). 사용자가 "잔여 개선사항을 남기지 마라"고 확인해
`Document.ancestor_ids`를 Neo4j 노드 속성으로도 쓰도록 바꿔(마이그레이션
없음 — Neo4j는 스키마리스, `merge_document`만 수정) 이 범위 제한을
제거했다. 이 변경은 **extraction-affecting**이다(merge_document가 쓰는
문서 속성이 바뀜) — 다음 전체 재백필 때 모든 Document 노드에 `ancestor_ids`
가 채워진다(위 EXTRACTOR_VERSION 범프와 같은 재백필에 묶여서 자동 반영,
별도 플래그 불필요).

### 검증

전체 `apps/api/tests/` 스위트를 scratch Neo4j(`citec-graph-test-neo4j`,
`bolt://localhost:17687`) + scratch Postgres(`citec_graph_test`, 이번에
새로 재생성)로 실행 — `test_ops_dashboard_auth.py`를 뺀 나머지 전부 통과
(518 passed, 52 skipped, 3 deselected; 포함 skip은 이 두 env var 없이도
항상 스킵되는 다른 무관한 테스트). `scripts/verify/run_integration_tests.sh`
도 같은 scratch로 end-to-end 통과 확인, skip-vs-require-fail 두 경로 모두
의도대로 동작.

### 배포 메모

다음 운영 배포 시 참고:

- **재시작만으로 충분**(`scripts/git_pull_deploy.sh`, Neo4j 재백필 불필요):
  G04 filtered/bridge-block(page_ids·source_types), G08 backend_error/
  min-hop, G09 elementId tie-break, G05 `/v1/graph/status`, G06 lexicon
  캐시, G11 verify 러너. 다만 `lexicon_terms.updated_at` 마이그레이션
  (`20261013_0012`)이 포함돼 있으니 `alembic upgrade head`가 자동으로
  도는지는 평소처럼 확인.
- **전체 재백필 필요**(EXTRACTOR_VERSION이 `2026-10-13.1`로 바뀜 — 다음
  `sync_cli` 실행이 자동으로 전체 재동기화를 트리거함, 사람이 따로 플래그를
  줄 필요 없음): G01 reference_index 완전 tie-break, G06 긴 구문 우선
  매칭, G05 `unresolved_references` 집계, **G04 `exclude_subtree_ids`
  bridge 차단**(`Document.ancestor_ids`를 Neo4j 노드 속성에도 쓰기 시작 —
  재백필 전까지는 기존 Document 노드에 이 속성이 없어 subtree bridge
  차단이 조용히 무효화됨, 틀린 결과를 주진 않지만 효과가 없음에 주의).

## 2026-10-13 2차 핸드오프 대응 (R01~R08)

`/tmp/CLAUDE_CODE_PROMPT.md` + `ANALYSIS.md`(이번엔 `evidence/`가
`/tmp/citec-graph-handoff-20261013.zip`로 함께 전달됨, 분석 커밋
`2bb1d7f` — 바로 위 2026-10-13(1차) 라운드 머지 **직전** 커밋이다) 대응.
먼저 dev 컨테이너를 재시작해 1차 라운드 코드가 실제로 돌고 있는지
확인하고, R01의 4개 재현 입력을 실제 dev MCP로 다시 쳐본 결과 — **R01의
핵심 버그(중간 경로 bridge 미차단)는 이미 1차 라운드의 G04 작업으로
고쳐져 있었다**(`604365026` 제외 시 78→73건, `604365046`이 더 이상
안 보임 — 핸드오프의 정확한 재현 입력으로 실측 확인). 그래서 이번
라운드는 "R01을 처음부터 구현"이 아니라 advisor 리뷰로 찾은 **그
G04 구현 자체의 결함**(아래)과, 아직 안 건드린 R02/R03/R04/R06/R07/R08
항목에 집중했다.

### R01 — 이번 라운드에서 찾은 자체 결함 교정

- **source_type 제외가 사본 fan-out 전체를 filtered로 끝냄(과도함)**:
  `exclude_source_types`에 걸리는 수집본이 "같은 external_id"의 여러
  수집본 중 하나뿐이어도, 과거 코드는 전체를 filtered로 끝내 허용된
  다른 수집본(예: 같은 페이지의 confluence_map 사본)을 탐색할 기회를
  잃었다. **실제 재현**: `confluence_docs:1000706325` 앵커 +
  `exclude_source_types=confluence_docs` → 같은 external_id의
  `confluence_map:1000706325` 사본이 있었는데도 무조건 filtered였음.
  이제 source_type 제외는 "그 수집본만 fan-out에서 제외"하고 남은
  수집본으로 계속 탐색한다 — 전부 제외됐을 때만 filtered(reason=
  `all_copies_source_excluded`). page_id/subtree 제외는 여전히
  페이지 전체 단위(reason=`page_or_subtree_excluded`) — 같은 물리
  페이지라 수집본을 가려도 의미가 없다.
- **`elementId(r)` tie-break가 과거 결정을 되돌림**: explore() Cypher의
  ORDER BY에 추가했던 `elementId(r1)`/`elementId(r1b)`/`elementId(r2)`는
  "elementId는 DB 인스턴스 안에서만 안정적이라 dev/운영/scratch 간
  재현이 안 된다"는 바로 위 주석과 충돌했다 — `startNode(r)=a`(방향)로
  교체, 도메인 안정적인 tie-break만 남김.
- **backend_error가 모든 예외를 삼킴(로그 없음)**: 과거엔 bare
  `except Exception`이라 이 라우터 자체의 버그(TypeError 등)도 "그래프
  장애"로 둔갑해 로그도 안 남고 조용히 200을 돌려줬다. 예외 타입의
  모듈 이름이 `neo4j`로 시작할 때만 backend_error로 분류(패키지를
  직접 import하지 않고 `type(exc).__module__`만 봐서 CI의 optional-import
  제약을 안 건드림), 나머지는 `logger.exception` 후 그대로 500으로
  드러나게 re-raise. `backend_error_reason` 필드 추가.
- 새 테스트: fan-out source_type 부분 제외(허용된 사본으로 계속 탐색),
  neo4j 기원 예외만 backend_error로 분류되고 그 외(TypeError)는 500으로
  드러남 — `tests/test_graph_router.py`.

### R02 — 동기화 run 상태의 정직성

- `mark_synced` 실패(그래프 쓰기는 성공, PG 상태 기록만 실패)를
  `graph_runs.documents_state_write_failed`로 집계하고 0보다 크면
  `sync_cli` exit code가 non-zero가 된다(과거엔 `completed`/exit 0로
  숨겨짐).
- `graph_runs.scope`(`full`|`partial`) 추가 — `--source-ids` 디버그
  실행을 구분. `GET /v1/graph/status`가 `latest_run`(가장 최근 아무
  행)과 `latest_full_completed_run`(scope=full ∧ completed류)을 분리
  반환, `generation_matches`는 후자 기준으로 판정 — partial 디버그
  실행이 가장 최근 행이어도 "그래프가 최신"으로 오판하지 않음.
- `graph_runs.lexicon_version` 실제로 채움(`lexicon_terms`의
  (count, max(updated_at)) 지문).
- `sync_cli`가 run 전체 기간 동안 전용 Postgres 커넥션으로
  `pg_try_advisory_lock`을 쥐어 동시 writer를 막는다(풀링된 ORM
  세션으로 잡으면 커넥션 반환 뒤에도 lock이 남을 수 있어 전용 커넥션
  사용). 락을 못 잡으면 `graph_runs` 행을 만들지도 않고 즉시 exit 1.
- 새 테스트: `tests/test_graph_sync_cli_db.py`(락 충돌, scope=partial
  기록), `tests/test_graph_pipeline_db.py`(state_write_failed 집계),
  `tests/test_graph_router.py`(latest_full_completed_run 분리).

### R03 — lexicon seed 모든 진입점 비파괴 기본값

- CLI 기본값 버그 실제 확인: `main()`이 `replace=not args.no_replace`라
  `--no-replace`를 안 주면 늘 `replace=True`였다(함수 기본값 False와
  불일치). `--replace`를 명시해야만 파괴적 동작, 기본은 보존.
- `replace=False`도 과거엔 seed에 "있는" canonical의 variants를 그대로
  덮어썼다(수동 추가 별칭 소실 가능) — 이제 기존 variants와 seed
  variants를 union으로 병합. priority/metadata는 seed 값으로 갱신(이
  둘은 수동 편집 대상이 아니라고 간주).
- `seed_lexicon(dry_run=True)` / `POST /v1/lexicon/seed?dry_run=true` —
  추가/변경/삭제 예상 canonical만 보여주고 쓰지 않음.
- `build_lexicon_map()`이 같은 변형어가 두 canonical에 겹치면
  `logger.warning`으로 기록(조용히 DB 반환 순서로 확정하지 않음),
  `load_lexicon_map()`의 쿼리에 `ORDER BY canonical` 추가해 결정성 확보.
- 새 테스트 4종 — `tests/test_lexicon_seed_db.py`.

### R04 — 적용한 one-line 수정

- `sync_document`의 skip 조건에 `not state.get("last_error")` 추가 —
  `mark_failed()`는 input_hash/version/generation을 안 건드리므로, 해시
  계산 전 단계에서 실패가 나고 문서 내용이 그대로면 last_error가 남은
  채 영원히 skip되는 경로가 있었다(실제 코드 경로로 재현·테스트함).
- `enrich_with_document_metadata`에 `pg_missing` 플래그 추가 — PG에서
  못 찾은 문서(삭제됨 등)가 `archived=False`인 정상 문서로 보이던 문제
  분리. MCP 렌더링에도 `[pg_missing]` 태그 추가.
- **잔여(이번에도 미착수)**: namespace 충돌 합성 테스트, UnresolvedPage
  역참조 재해석 — 설계가 더 필요해 범위 밖으로 유지.

### R05 — 착수하지 않음(설계 필요, 의도적 보류)

핸드오프 자체가 "먼저 holdout 질문 집합을 구현 전에 고정하고 랭킹
튜닝에서 제외하라"(§3.3), "알려진 정답 ID를 가중치로 넣지 말라"(§2.5.4)
고 명시한다 — 이건 버그 수정이 아니라 새 관련성 랭킹 기능이고, 잘못
만들면 바로 그 금지사항(ID 특례, 하드코딩)을 어기기 쉬운 영역이다.
이번 라운드에서 섣불리 구현하지 않았다 — **별도 브레인스토밍 세션으로
설계(홀드아웃 질문셋, 랭킹 신호, 합격 기준 고정)부터 시작해야 한다.**

### R06 — 근거 가시성

- MCP `_render_route`가 `tag`(EXTRACTED/INFERRED)를 드롭하던 것을
  `관계명[TAG]` 형식으로 표시.
- `sync_failure_bucket`이 이 bucket 자신의
  resolved/external_artifact/legacy_missing/target_not_ingested/
  unresolved_reference 사유를 계산해 `FailureBucket.evidence_reasons_json`
  으로 Neo4j 노드에 저장(sync 시점 1회 계산 — 매 요청마다
  `page_identity_index`를 다시 만드는 비용을 피함), `kb_graph_explore`의
  failure_bucket 결과 줄에 `evidence_reasons=...`로 노출.
- 새 테스트: `mcp-server/test_graph_explore_format.py`(tag 렌더링),
  `evidence_reasons` MCP 렌더링은 `_render_failure_bucket_line`에 추가.

### R07 — 상태 envelope 일관성 / 범위 밖

- `kb_graph_explore`의 unknown-anchor 404, 잘못된 anchor_type, 빈
  anchor_value 세 분기 모두 이제 `status: ...`로 시작(과거엔 이 셋만
  예외적으로 status 없는 문장 하나였다 — raw evidence로 재현 확인,
  `invalid_request` status 신규).
- `docs/AI_AGENT_GUIDE.md`의 "status가 항상 첫 줄" 설명을 실제 계약에
  맞게 수정(이제 진짜로 모든 분기가 그렇다).
- **BLOCKED(이 호스트에서 불가능)**: Codex 호스트의 캐시된 도구 계약
  (2-인자 구버전)과 Windows 5곳의 스킬 캐시(10월 9일 계약) 동기화 —
  이 dev 서버에는 Codex/Windows 세션 접근이 없고, `citec-mcp-workbench`
  스킬 자체가 이 호스트에 설치돼 있지도 않다(1차 라운드와 동일 확인,
  `find` 재실행으로 재확인). 정본(`docs/CITEC_KB_MCP_SKILL.md`)은 이미
  현재 8-인자 계약을 정확히 반영하고 있어 그 쪽은 고칠 게 없었다 —
  남은 건 그 정본을 실제 Codex/Windows 설치 경로에 동기화하는 것뿐인데,
  그건 이 호스트 밖의 작업이라 패치 대상이 없다(이미 맞음).

### R08 — CI/정리

- **mcp-server를 CI에 추가했다가 되돌림**: R08은 "그래프·MCP 계약
  검증을 실행하는 최소 job"을 명시적으로 허용해 한 번 추가했으나(커밋
  `30d7ecc`), 사용자가 "사내 git 서버(code.sdsdev.co.kr)는 GitHub
  Actions를 지원하지 않는 것 같다"고 지적 — 이 저장소의 `origin`이
  실제로 `github.com:pjhwa/citec-kb`라 핸드오프가 가리키는 진짜 팀
  저장소(code.sdsdev.co.kr)와 다르다는 뜻이고, `.github/workflows/ci.yml`
  자체가(이번 추가분 포함 전부) 그 서버에서는 안 돌아갈 가능성이 높다.
  바로 revert(`2168a58`)했다 — 실제 CI 시스템이 뭔지 확인되면 그에 맞는
  형식(GitLab CI 등)으로 다시 검토.
- **고립 유령 노드 827개**: `scripts/verify/ghost_node_audit.py` 신규
  — 읽기 전용 dry-run만, 삭제 기능 자체가 없음. dev에서 실행 확인:
  `source_type IS NULL`인 Document 827건 전부 고립(관계 0개) — 이전
  라운드의 측정과 정확히 일치. 백업 manifest(id/external_id/title +
  복구안)를 JSON으로 남김. 실제 삭제는 여전히 사람이 manifest를 보고
  별도 승인해야 한다(그대로 보류).

### 검증

전체 `apps/api/tests/` 스위트(532 passed, 52 skipped, 3 deselected)와
`scripts/verify/run_integration_tests.sh`(57 passed, 7개 통합 테스트
파일 — 새로 추가된 `tests/test_graph_sync_cli_db.py` 포함)를 scratch
Neo4j + Postgres로 재확인. `mcp-server/` 테스트(18 passed, 로컬 venv).
R01의 핸드오프 재현 입력 4종을 dev 재시작 후 실제 `mcp__wiki-mcp__*`
호출로 다시 실행해 수정을 직접 확인(위 "R01" 절).

### 배포 메모(이번 라운드 추가분)

- **재시작만으로 충분**: fan-out source_type 부분 제외, elementId→방향
  tie-break, backend_error 로깅/좁히기, R02 상태 정직성 전체(락/scope/
  lexicon_version/상태 엔드포인트), R03 lexicon seed 전체, R04
  last_error 재시도·pg_missing, R06 tag/evidence_reasons 렌더링, R07
  status 일관성. 마이그레이션 `20261013_0013`(`graph_runs.scope`,
  `documents_state_write_failed`) 포함 — `alembic upgrade head` 확인.
- **전체 재백필 필요**: 없음(이번 라운드는 모두 restart-only) — 단
  `FailureBucket.evidence_reasons_json`은 다음 `sync_failure_bucket`
  실행(이미 예정된 재백필에 포함됨) 전까지는 기존 bucket에 비어 있다.
