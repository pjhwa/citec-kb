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
| G09 (등급·결정적 정렬) | **완료** | `app.graph.identity.GRADE_RANK`로 통합(A/A-/B/C/machine/draft) — `explore.py`/`failure_bucket.py` 둘 다 여기서 가져옴. **2차 수정**: `_sort_key`가 `(hops, grade)`뿐이라 동률이면 Neo4j `collect()`의 비결정적 순서가 그대로 샜다 — id(컴포넌트는 canonical_name)를 최종 tie-break로 추가해 같은 요청을 반복해도 순서가 같아지게 함. | `test_grade_rank_includes_a_minus`, `test_shape_explore_result_sorts_a_minus_before_b_at_same_hop`, `test_shape_explore_result_breaks_ties_deterministically_by_id` |
| G07/G08/G10/G11 | **미시작** | — | 다음 라운드 — MCP 20건 cap 뒤 접근 수단, 전체 경로/provenance, failure_bucket resolution URL 해석(HAS_EVIDENCE 승격), 기존 테스트의 실제 계약 반영은 G01/G02 fixture 교체분(G11 일부)만 끝남 |

## 부수적으로 발견/수정

- **CI 누락 버그**: `app.main`이 항상 `app.routers.graph`를 import하는데, 그게
  `neo4j_client.py`의 모듈 최상단 `import neo4j`를 항상 실행해 `requirements-ci.txt`
  (neo4j 의도적 제외) 환경에서 `app.main`을 import하는 모든 테스트가 깨지고
  있었다(그래프와 무관한 `test_mock_idp_e2e.py` 포함, PR #16 머지 이후부터 존재).
  `neo4j_client.py`의 import를 optional로 바꾸고 실제 `Neo4jClient()` 생성
  시점에만 에러를 내도록 수정.

## 코드 리뷰에서 걸러낸 것(실제 dev 전체 재백필 전, 총 3회 kill)

전체 dev 재백필(새 generation)을 세 번 시도했고, 매번 리뷰에서 실제 결함을
찾아 **그 자리에서 kill**한 뒤 수정·재검증했다 — 완성된 뒤에야 네 번째로
진짜 전체 재백필을 실행했다. 세 번 모두 `graph_runs`에 `status=running`으로
남은 행이 있다(`generation` 값: `2026-10-12.g1`, `2026-10-12.g2`, 그리고
`--source-ids` 포커스 체크 이전의 세 번째 시도분) — kill은 SIGKILL이라
`sync_cli`의 바깥 try/except(G05 수정)로도 못 잡는다. dev 전용이라
`running`으로 방치해도 피해는 없다(사람이 봤을 때 "그 시점에 멈췄다"는
정확한 기록이기도 하다).

1. **1차**: G03(PARENT_OF 방향 수정 전 코드가 쓴 유령이 지워지지 않음),
   G06(한글 한 글자 변형어 "행" 오탐) — 둘 다 코드 리뷰만으로 발견, 실행 중
   kill.
2. **2차**: G06 ASCII 경계가 과거보다 오히려 정밀도를 떨어뜨림(path/hostname/
   dotted key 오탐) — 실제 코퍼스 2000건 diff로 재현. G01 fan-out이 또
   다른 두 버그를 만듦(복제본이 서로의 연관 문서로 나옴, external_id=''가
   SQL `IS NULL`/falsy 비교로 fan-out됨). G05의 `--generation` 사람 실수
   의존성도 이 라운드에서 마커 기반으로 구조적으로 제거.
3. **3차(포커스 체크)**: 코드 결함은 못 찾음 — `tech_repo:1153334044`(구
   유령 소유자)와 TGW 3문서, `confluence_docs:1117787168`를
   `--source-ids`로만 재동기화해 before/after를 실측하고서야 전체 실행을
   승인했다. 결과는 `artifacts/citec-kb-graph-correctness-20261012/evidence/`.

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

## 최종 전체 재백필 결과

실행 중 — `docker compose exec -T api python -m app.graph.sync_cli`(`--generation`
미지정, dev Neo4j 마커 자동 발급/사용). 완료 후 아래를 채운다: `graph_runs` 최종
상태/실패 수, `source_type IS NULL`인 연결된 Document 수(≈실패 수와 비교),
고립된 유령 노드 수(백업 후 삭제 승인 대기 목록), PG에서 사라진 문서의 Neo4j
잔존 여부, Network/SCP의 `is_hub` 재확인, 실제 kb_search→kb_graph_explore→
kb_get_document 체인(TGW/NetApp) trace.

## 정리(approved)

- Docker 빌드 캐시(9.5GB) + 미사용 이미지(사용자 승인, "정리 진행" 선택) 정리됨
  — 태그가 있던 이미지 중 `xfetch-backend`/`xfetch-frontend`도 함께 제거됨(이
  리포지토리와 무관한 다른 프로젝트 이미지로 보이며, 미사용으로 판단되어 제거됨).
- 세션에서 만든 scratch Neo4j 컨테이너(`citec-graph-test-neo4j`)와 Postgres
  scratch DB(`citec_graph_test`)는 이번 라운드가 끝나는 시점에 정리한다(아직
  유지 — 재검증에 계속 씀).
