# citec-kb 신뢰도·검색 효율·신선도 개선 — 구현 보고 (2026-09-30)

이 문서는 `docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md`(개발
지침)와 `artifacts/citec-kb-improvement-review-20260930/REVIEW.md`(분석)를
받아 이번 세션에서 실제로 구현·검증한 범위와, 의도적으로 미룬 범위를 정리한다.

**이번 세션은 지침의 전체 범위(P0-A~D, P1-A~C, P2, 파일럿)를 전부 구현하지
않았다.** §13이 명시적으로 허용하는 경로("소스 수정 + 오프라인 테스트 +
리뷰 가능한 계획을 완성하고 운영 검증만 BLOCKED_EXTERNAL로 남긴다")를 따라,
**코드로 확정 가능하고 회귀시험으로 증명 가능한 항목만** 구현했다. 1차로
P0 전체(§1), 사용자가 "나머지도 진행"을 요청한 2차 라운드에서 P1-A/P1-C의
코드-확정 가능한 항목(§1-B)을 추가했다. P1-B와 P2는 라이브 코퍼스/트래픽이
있어야 정직하게 검증할 수 있어 두 라운드 모두 손대지 않았고, §3에 왜인지
남겼다. 나머지는 "단순 제안서"로 흩어놓지 않기 위해 각 절 끝에 다음 세션이
바로 집어들 수 있는 지점을 남겨 두었다.

## 0. Baseline

| 항목 | 값 |
|---|---|
| 로컬 HEAD (세션 시작) | `4ef19e7e94f15851ff0b3fc2c9a4ed0422e819b0` (origin/main과 동일, 분석 시점 문서의 878d83d0에서 이미 진전됨) |
| 작업 트리 | clean, 미추적 `artifacts/`, `docs/..._PROMPT_20260930.md`만 존재 — 삭제/덮어쓰기 없이 보존 |
| 운영 배포 SHA | health에서 미노출. 원격 HEAD=배포 SHA로 확정하지 않음 (BLOCKED_EXTERNAL) |
| offline_probes.py 13건 | 전부 `reproduced: true`로 재현 확인 (2026-09-30 세션 시작 시 1회 실행) — 이 스크립트는 `artifacts/.../remote-4ef19e7/`의 **얼린 스냅샷**을 읽으므로, 이후 소스를 고쳐도 재실행 결과는 항상 그대로 "재현됨"을 보고한다. **제품 회귀 판정에 이 스크립트를 다시 쓰지 않았다** — 아래 §2의 신규 pytest가 실제 회귀시험이다. |
| 테스트 실행 환경 | `.venv`에 sqlalchemy 2.0.40/pgvector 0.4.1/pytest 9.1.1 설치 확인 — DB 미통합 예상과 달리 **실제로 unit+contract 전체 실행 가능** |
| DB-integration 환경 | `CONFLUENCE_SYNC_TEST_DATABASE_URL` 최초 미설정 → 기존 관례(`docker exec citec-kb-postgres-1 ... CREATE DATABASE citec_kb_test`)대로 **운영 postgres 컨테이너 안에 별도 scratch DB를 새로 만들어** `alembic upgrade head`까지 적용하고 DB-integration 테스트를 실제로 실행했다 (`citec_knowledge`는 건드리지 않음). |

## 1. 이번에 구현한 것

### P0-D — 날짜·업무유형 보존 (§7, D12)

**현상**: `"2026년 9월 7일부터 2026년 9월 13일까지 기술지원 이력의 총 건수"`가
`query/planner.py`의 절대기간 early-return(`parse_absolute_range` →
`detect_time_scoped_list`)을 타면서 `intent`/`date_field`는 잡지만 Jira
Component 제약("기술지원")을 완전히 버렸다. `tickets/query.list_tickets`에는
애초에 `component` 파라미터 자체가 없었다 — `analytics/aggregate.aggregate_tickets`
(COUNT 경로)에는 있었는데, list 경로에만 없어서 목록 건수와 COUNT가 애당초
같은 필터를 볼 수 없는 구조였다.

**원인 파일/함수**:
- `app/query/time_range.py::detect_time_scoped_list` — component 추출 없음
- `app/tickets/query.py::list_tickets` — component 파라미터 없음
- `app/query/analytics_intent.py`의 `_COMP_MAP`이 유일한 정의였고 재사용 불가 구조

**수정**:
- `app/query/component_map.py` 신설 — `COMP_MAP`/`extract_component()`를
  analytics_intent와 time_range 양쪽이 같이 쓰도록 공유(§7 "같은 predicate builder").
  "지원건 전체"(기술/장애 접두어 없음)는 여전히 component=None으로 남긴다 —
  이것이 지침이 요구한 "지원건 전체" vs "기술지원 이력"의 구분이다.
- `time_range.py::detect_time_scoped_list`에 `component` 필드 추가.
- `tickets/query.py::list_tickets`에 `component` 파라미터 + 필터 추가.
  `aggregate_tickets`와 동일한 predicate(`c != q and q.lower() not in c.lower()`)를
  그대로 사용해 list/COUNT parity를 보장.
- `query/planner.py::execute_plan`의 `time_scoped_list` 분기, `routers/tickets.py`의
  `GET /v1/tickets` REST 파라미터에도 `component`를 배선.
- 페이지네이션 무관 total 불변은 기존 구현이 이미 만족(필터링 후 슬라이싱 전에 `total=len(rows)` 계산) — 회귀시험으로 고정.

**완료 기준 검증**: `test_absolute_range_preserves_explicit_component_ko/iso`,
`test_absolute_range_bare_support_word_has_no_component`,
`test_absolute_range_swim_never_gets_a_component`(unit, DB 불필요) +
`test_list_and_aggregate_component_filter_counts_agree`,
`test_list_total_unaffected_by_pagination`(scratch DB로 실제 실행, 통과).

### P0-C — 요약이 원문을 이기는 문제 (§6, D08)

**현상**: `frames/extract.py::_better()`가 "더 긴 텍스트"를 최신성 신호로
써서, 짧은 원문 "5/13 17:00 PB2 Fabric 연결 변경 완료"가 긴 LLM 요약
"...모니터링을 진행 중이며 ... 계속 검토 중이다"에 밀렸다. 별도로
`frames/job.py::extract_frames(force=False)`는 IssueFrame 행이 **존재하기만
하면** 그 문서를 완전히 건너뛰어, Document.body_md가 바뀌어도 프레임이
영구히 갱신되지 않았다.

**원인 파일/함수**: `frames/extract.py:178-201,293-301`(길이 우선 `_better`),
`frames/job.py:64-66`(존재 여부만으로 skip).

**수정**:
- `frames/extract.py`에 `_COMPLETION_MARKER`/`_INPROGRESS_MARKER` 추가.
  명시적 완료 문구가 있는 새 후보는 "진행 중" 문구뿐인 기존 슬롯을 길이와
  무관하게 이기고, 반대로 "진행 중" 후보가 이미 "완료"로 채워진 슬롯을
  덮어쓰지 못하게 가드. 길이는 이 두 상태가 모두 아닐 때만 tie-break로 남음.
  타임스탬프를 포함한 완전한 `fact_status`(추정/확인/예정/완료/충돌) 필드는
  범위 밖으로 남겨 뒀다 — 이번 수정은 순서 버그만 고친다.
- `body_hash()`/`EXTRACTOR_VERSION` 추가. `extract_frame_from_markdown()`
  반환값에 `body_hash`/`extractor_version` 포함.
- `db/models.py::IssueFrame`에 `body_hash`/`extractor_version` 컬럼 추가
  (nullable, 추가적 — 기존 행은 NULL="이 마이그레이션 이전 추출"로 간주).
  `alembic/versions/20260930_0009_issue_frames_body_hash.py` — `upgrade head`
  로 scratch DB에 적용 확인 완료. **백필/재생성은 이 마이그레이션이 자동으로
  수행하지 않는다** — 다음 `extract_frames()` 실행 시 문서별로 자연스럽게 갱신.
- `frames/job.py::extract_frames()`: "이미 프레임이 있으면 skip"을
  "body_hash와 extractor_version이 모두 현재값과 같으면 skip"으로 교체.
  `skipped_fresh`/`regenerated_stale` 카운터로 두 경로를 구분해 보고.

**완료 기준 검증**: unit `test_explicit_completion_beats_longer_in_progress_summary`,
`test_stale_in_progress_cannot_overwrite_an_already_completed_slot`,
`test_frame_carries_body_hash_and_extractor_version` + DB integration
`test_second_run_without_change_skips_as_fresh_not_reprocessed`,
`test_body_change_triggers_regeneration_not_skip`,
`test_extractor_version_bump_forces_one_regeneration_pass` — 전부 scratch DB에서
실제 실행, 통과.

**같은 코퍼스 A/B (§12B) — 실측, 결과는 0건 변화**: `_better()` 수정 전/후
extractor를 **로컬에 실제 존재하는 `data/raw/support_history`(2,318개) +
`data/raw/incident_reports`(15,342개), 총 17,660개 문서** 전체에 순수 함수로
(DB 불필요) 실행해 `symptom`/`root_cause`/`resolution`/`workaround` 네 슬롯을
전부 비교했다. **결과: 17,660건 중 단 1건도 값이 바뀌지 않았다**
(`body_hash`/`extractor_version` 메타만 새로 붙는 것 제외). "## LLM 요약"
섹션을 가진 문서 192건, "검토 중/진행 중/확인 중" 문구를 가진 문서 282건이
로컬 코퍼스에 실재함을 확인했음에도 이 결과다 — 즉 **D08이 보여준 순서
결함 자체는 실재하고 unit/DB 테스트로 확정됐지만, 지금 로컬에 있는 원문
스냅샷에서는 "긴 요약(진행 중) vs 짧은 원문(완료)"이 실제로 충돌하는
사례가 없었다.** 두 가지 중 하나이거나 둘 다일 수 있다: (a) 운영 DB의
Document.body_md가 이 로컬 `data/raw/` 스냅샷과 다를 수 있음(업스트림
파이프라인이 이후 갱신했을 가능성) — 확인 안 됨(BLOCKED_EXTERNAL), (b) 이
정확한 충돌 형태가 실제로는 드묾. **"몇 건을 구했다"는 과장 없이 사실대로
보고한다: 이번 수정이 지금까지 알려진 재현 사례(D08)를 막는다는 것은
증명됐고, 로컬에서 구할 수 있는 실제 코퍼스에서는 그 수정이 어떤 문서의
출력도 바꾸지 않았다.** 운영 DB 코퍼스로 같은 스크립트를 재실행하면
확정할 수 있다(BLOCKED_EXTERNAL — 아래 §2).

### P0-A — 근거 계약 (§4) — 코드 경로 보존 + MCP 표면 정합성

**현상 (D09)**: `retrieval/search.py`는 이미 `SearchHit.evidence_eligible`/
`map_synced_at`을 계산하지만, `rag/pipeline.py::_prepare()`가 결과를
`SimpleNamespace`로 재구성하며 그 두 필드를 빼먹었고, `rag/packer.py::pack_chunks()`는
애초에 그 필드를 모르는 dataclass였다. `doc_access.py`는 confluence_map
행에도 `kb_get_document`가 "본문"을 준다고 안내했다 — 실제로는 저장된
~400자 발췌(snapshot)만 재반환한다. `mcp-server/server.py::_access_lines()`는
이 구분 없이 항상 같은 안내를 출력했다.

**수정**:
- `rag/packer.py::PackedChunk`에 `evidence_eligible`/`map_synced_at` 추가,
  `pack_chunks()`가 getattr로 이어받음. `format_context_block()`이
  `evidence_eligible=False` 청크에 `(POINTER — 원문 미확인, 사실 근거로 인용 금지)`
  태그를 붙여 생성 프롬프트 자체가 포인터를 사실로 착각하지 않게 함.
- `rag/pipeline.py`의 두 enrichment 루프(원 검색 결과 / sibling chunk 주입)
  모두 이 필드를 채워서 넘김. citations 딕셔너리에 `evidence_eligible`,
  `evidence_kind`("excerpt"|"pointer"), `map_synced_at` 노출.
- `doc_access.py::document_access()`에 `body_kind`("snapshot"|"fulltext")와
  `verify_via`(confluence_map일 때 `confluence-mcp.getPageByID(pageId, body.storage,version)`
  안내) 추가 — §4 item 3이 요구한 정확한 기본 안내.
- `mcp-server/server.py::_access_lines()`가 `body_kind`/`evidence_eligible`/
  `map_synced_at`/`verify_via`를 읽어 pointer 행에는 "⚠ pointer만 있음",
  `map_synced_at`, `원문 확인: confluence-mcp.getPageByID(...)`를 출력.
- `trust/engine.py::assess_trust()`에 선택적 `n_verified_citations_used` 인자
  추가 — 답변이 인용한 citation 중 evidence_eligible=True인 것만 "evidence"
  강도에 반영, pointer만 인용됐으면 "strong"/"medium"으로 올라가지 않고
  이유를 reasons에 남김(D10 부분 대응 — 독립 canonical source dedup까지는
  범위 밖, P1-A #9로 이월). `rag/pipeline.py::_finalize()`가 실제로 이 인자를
  계산해 전달하도록 배선. 기존 호출자(값 미전달)는 이전 동작 그대로 유지 —
  하위호환.

**D11(§4 item 5, §5) — legacy 필터 드롭**: `mcp-server/server.py::_search_impl()`이
`use_v1=False`인데 v1 전용 필터(exclude_*, environment, work_type,
include_irrelevant_maps, diversify_copies=False)가 실제로 지정된 경우
**자동으로 v1 경로로 전환**하고(조용히 버리지 않음), 응답에 전환 사실을
명시(`[note: ... v1로 자동 전환]`). §4 item 5의 두 선택지("v1에 위임" 또는
"명시적 오류") 중 위임을 택했다 — 사용자가 명시한 제외 조건을 실패시키는
쪽보다 안전.

두 번째 리뷰 패스(advisor)에서 지적됨: 이 자동 전환이 **반대 방향의 같은
버그**를 새로 만들 뻔했다 — `category`는 legacy 전용 필드로 `SearchFilters`(v1)에
대응 필드가 아예 없다. `use_v1=False, category="X", exclude_subtree_ids=[...]`처럼
둘을 같이 요청하면, 자동 v1 전환은 `category`를 v1에 실어 보낼 방법이 없어
그대로 삭제된다. 수정: 이 조합(`category` + v1 전용 필터)을 감지하면 자동
전환 대신 **명시적 오류**를 반환하도록 분기했다 — 어느 쪽도 둘 다 지원하지
않는 조합이라 "위임"이 성립하지 않기 때문. 단독으로 쓰이는 `category`
(legacy 유지) 또는 단독으로 쓰이는 v1 전용 필터(자동 전환)는 기존 동작 그대로.

**kb_ask 인자 불일치**: `kb_ask`/`_ask_impl`이 `exclude_page_ids`/
`exclude_source_types`/`exclude_subtree_ids`/`include_irrelevant_maps`를
받아 `/api/query`(WikiQueryRequest — 이미 이 필드들을 지원)로 전달하도록
`kb_query`와 동일하게 배선. 이전에는 REST가 지원하는 옵션을 MCP `kb_ask`로
쓸 방법이 없었다.

**남은 범위 (미착수, 다음 세션)**: `EvidenceRef` 공통 dataclass(§4 item 1의
`canonical_source_id`/`content_hash`/`lineage` 전체 필드 세트)는 아직
도입하지 않았다 — 이번엔 기존 `SearchHit`/`PackedChunk`에 필드를 보존/추가하는
선에서 그쳤다. `kb_query`의 비검색 intent별 적용 범위 명세(§4 item 6 후반),
문서 조회 length/offset/truncated 계약(§4 item 8), localhost:8572 링크
교정(§4 item 9)은 손대지 않았다.

### P0-B — 조상 ID 저장 우회 경로 (§5, 코드 확정분만)

**현상 (코드로 확정됨)**: `confluence/sync.py`(전체본문 수집 — confluence_docs/
tech_repo)는 Confluence API 응답의 `ancestors`를 breadcrumb 문자열 생성에만
쓰고 `ancestor_ids`로 저장하지 않았다. `retrieval/search.py`의
`exclude_subtree_ids` 필터는 **source_type과 무관하게** `Document.metadata_["ancestor_ids"]`를
읽는 일반 로직이라, map 이외의 소스(confluence_docs/tech_repo)에 같은
페이지가 있으면 그 경로로 subtree 제외가 우회될 수 있었다.

**v1 실패의 정확한 운영 원인(부모 제외해도 자식이 남은 사례)은 여전히
미확정**이다 — REVIEW.md §60이 명시한 대로 "DB 조상 데이터/배포 상태
미확인"이며, 이번 세션은 운영 DB read-only 조회 권한이 없어 §5 "진단 순서"의
배포 SHA/DB 상태/EXPLAIN 확인은 실행하지 않았다(BLOCKED_EXTERNAL, 아래
§3 참조). **이번에 고친 것은 코드로 확정된 별도 경로**(non-map 소스의
ancestor_ids 부재)다.

**수정**: `confluence/sync.py::build_frontmatter_confluence_docs`/
`build_frontmatter_tech_repo`에 `ancestor_ids` 파라미터 추가 →
`조상ID목록 : id1,id2,...` frontmatter 줄 생성(맵과 동일 필드명, additive —
기존 필드 순서/이름 불변). `_write_page()`가 `ancestors + [page_id]`로 계산해
전달. `ingest/adapters.py::parse_tech_repo_file`/`iter_confluence_docs`가
그 줄을 `metadata["ancestor_ids"]`(list)로 구조화 — `iter_confluence_map`과
동일한 파싱 패턴.

**완료 기준 검증**: `test_confluence_docs_carries_ancestor_ids`,
`test_confluence_docs_without_ancestors_has_no_ancestor_ids_key`,
`test_tech_repo_carries_ancestor_ids`(unit, tmp_path 기반 round-trip) — 통과.
`retrieval/search.py`의 exclude_subtree_ids 로직 자체는 변경하지 않았다
(이미 source_type 무관 일반 로직임을 코드 확인) — 이 조합으로 non-map 우회
경로가 닫힌다는 것은 코드 상 성립하지만, **실제 운영 코퍼스에 이 우회가
발생한 사례가 있었는지는 미검증**이다.

### 리뷰 패스에서 나온 추가 수정 (advisor 재검토)

구현 후 자체 재검토에서 3건이 지적되어 반영했다:

1. **`category`+v1전용필터 동시 요청 시 자동전환이 `category`를 삭제하는
   문제** — `SearchFilters`(v1)에는 `category` 필드가 아예 없어, D11 자동
   업그레이드가 반대 방향으로 같은 버그를 재현할 뻔했다. 이 조합을 명시적
   오류로 분리했다(위 D11 절 참고).
2. **P0-C 실제 영향 미검증 문제** — 로컬 `data/raw/`의 실제 17,660개 문서
   전체로 오프라인 A/B를 실행해 **0건 변화**를 확인·보고했다(위 P0-C 절 참고).
   기존 초안은 이 수치 없이 "정확도 개선"을 암시할 뻔했다.
3. **"pre-existing 실패" 주장이 근거 없이 단정됐던 문제** — `git stash`로
   변경 전 트리에서 같은 스위트를 실행해 동일하게 실패함을 확인
   (334 passed vs 344 passed, 차이 10건 = 이번에 추가한 테스트 수와 정확히
   일치). 이제 §2의 "pre-existing" 표현은 검증된 사실이다.

추가로 다음도 함께 고쳤다: `verify_via`가 confluence_map의 `external_id`를
항상 숫자 pageId로 가정했던 부분에 `eid.isdigit()` 가드 추가(파일명 stem이
fallback되는 경우 잘못된 `getPageByID` 안내를 만들지 않도록), DB 테스트
2건의 전역 카운터 절대값 assertion(`skipped_fresh == 0` 등)을 같은 scratch
DB를 공유하는 다른 테스트 파일의 fixture 행에 취약하지 않도록 자기 문서의
row/updated_at 직접 조회로 교체, `frames/job.py::extract_frames()`의
batching 비용(§ 위 P0-C 수정 설명 참고 — body_hash 비교가 body_md 전체를
매 호출마다 다시 읽게 만드는 구조적 비용)을 docstring에 명시.

## 1-B. 두 번째 라운드 — P1-A/P1-C 추가 구현

사용자가 "나머지도 계속 진행해줘"라고 요청해 §3에서 미착수로 남겼던 항목
중 **코드로 확정 가능하고 로컬에서 증명 가능한 것만** 추가로 구현했다.
P1-B(watermark/coverage 스키마)와 P2(성능 계측·튜닝)는 각각 성장하는
실제 코퍼스와 라이브 트래픽이 있어야 정직하게 검증할 수 있어 이번에도
착수하지 않았다 — 아래 표 참고. 커밋 3개(P0 이후): `e3d63d1`(P1-A 1차),
`64e4ef4`(P1-C), `f2cecb8`(자체 재검토 반영).

### P1-A — 검색 순위·신뢰도 정확성 (§8)

| 항목 | 현상 | 수정 | 검증 |
|---|---|---|---|
| D06 | `retrieval_trust()`: `fts_rank==1`이면 점수(예: 0.001)와 무관하게 무조건 "strong" | fts_rank==1은 이미 medium 문턱을 넘은 점수를 strong으로 한 단계 올리는 역할만 — 무에서 strong을 만들지 못하게 함 | unit 7건(`test_retrieval_search_pure.py`) |
| D07 | `classify_map_tech`: 명백한 재무/인사 문서도 본문에 "네트워크" 한 단어만 있으면 relevant | 비기술 마커가 **제목**에 있을 때만 단일 도메인 히트를 약한 신호로 취급해 irrelevant로 낮춤(2개 이상 도메인 히트는 여전히 relevant). 최초 구현은 제목+발췌 전체를 봤으나, 로컬에 발췌 데이터가 전혀 없어 영향 범위를 측정할 수 없었으므로 제목 전용으로 더 보수적으로 좁힘(아래 "검증 한계" 참고) | unit 4건(`test_improve_round.py`) |
| §8 item5 | 제목 완전일치 가산점이 고정 +0.25 — 2-list RRF 최대 점수(~0.033, k=60)의 7~8배. 완화된 multi-query 하위질의의 약한 후보도 제목만 맞으면 전부 역전 | `title_match_bonus()`로 분리 — 배치 내 최고 점수에 비례(상한 0.25, 하한 0.05)하도록 스케일 | unit 4건 + **실제 코퍼스 5,065건 A/B**(아래) |
| §8 item6 | `fts_search`/`vector_search`/`hybrid_search`의 후보 SQL 5곳에 동점 시 정렬 키가 없어 반복 검색마다 순서가 바뀔 수 있음(§2 "반복 top1 16/16→14/16"의 메커니즘) | 5곳 전부에 `Chunk.id` 동점 기준 추가 | DB 통합 테스트 2건 — **수정 전 코드로는 실제로 실패함을 확인**(임시 revert로 재현 후 복원) |

**title_match_bonus 실측 A/B**: scratch DB에 `data/raw/{support_history,tech_repo,confluence_docs}`
5,065건을 실제 ingest 파이프라인으로 적재하고, 실제 문서 제목에서 뽑은
16개 질의로 old(고정 +0.25) vs new(스케일링) `hybrid_search` top-10을
비교했다. **16개 전부 순위 동일**. 예: CITECTS-1024의 가산점 적용 전
fused 점수는 0.0164, old 코드는 +0.25로 0.266까지, new 코드는 +0.05로
0.066까지 올렸지만, 2위 후보가 0.0147이라 어느 쪽이든 1위는 바뀌지 않았다.
**이 결과는 긍정적이지만 좁다**: 16개 질의 모두 "제목과 거의 정확히 일치"하는
강한 사례였고, 리뷰가 실제로 우려한 "완화된 하위질의의 약한 후보가 제목
매치만으로 역전"하는 시나리오는 이 5,065건 슬라이스에 자연발생 사례가 없어
직접 재현하지 못했다. 공식이 구조적으로 그 시나리오의 점수 폭주를 막는다는
것은 산수로 보장되지만, 실제 그런 사례에서 순위가 달라지는지는 **미검증**이다.

### P1-C — 평가·생성 경로의 거짓양성 (§10)

| 항목 | 현상 | 수정 | 검증 |
|---|---|---|---|
| D13 | `eval/groundedness.evaluate_one`의 "ok"가 인용 형식만 보고 `llm_error`를 전혀 확인하지 않음 — provider 실패 스니펫도 `ok=True` | `ok`는 이제 `citation_format_ok`(기존 검사, 이름만 명확화) AND `not llm_error`. `run_eval`에 `citation_format_rate`/`llm_error_rate` 노출, `pass` 게이트에 `llm_error_rate <= max_llm_error_rate`(기본 0.0) 추가 | unit 4건(`test_eval_groundedness.py`), `run_fast_rag` mock으로 D13 정확 재현 |
| 근거목록 위장 | `rag/pipeline.py`의 "무인용 답변 자동보강"이 `[C1] 제목: 스니펫` 형식으로 붙여, `_extract_citation_ids`가 이를 모델이 실제 인용한 것처럼 오인 → trust가 부풀려질 수 있음 | 괄호 인용 형식 제거(`후보 C1: 제목 — 스니펫`), "모델이 인용하지 않은 검색 후보. 사실 근거로 확정하지 말 것" 명시 | unit 2건(`test_rag_pipeline_citation_repair.py`) — 새 형식이 파싱 안 됨을 양성 확인, 구 형식을 negative control로 남겨 버그 재발 시 즉시 실패하게 고정 |

### P2 — 확인된 사실 (계측·튜닝은 미착수)

§11 1단계("ID/기간/집계는 불필요한 임베딩 전에 결정론적 경로로 처리")는
**이미 만족되어 있음을 코드로 확인**했다 — `query/planner.py::execute_plan`은
`capacity`/`analytics`/`time_scoped_list`/`similar_incident`/`prevention`/
`exhaustive`/`checklist`/`entity_aggregate` intent 전부 `embed_query()`
호출 없이 처리되고, `embed_query`는 `intent == "hybrid_search"` 분기
안에서만 import·호출된다(planner.py:414-417). 이는 새로 만든 게 아니라
기존 설계가 이미 구현해 둔 것을 확인한 결과다. 2단계 이후(multi-query
조기종료, batch encode, cache, connection pool, reranker, stage별
deadline)는 p50/p95·후보수·토큰수 계측 없이는 "병목이 아닌 곳을 최적화"할
위험이 있어 손대지 않았다.

### 검증 한계 (자체 재검토에서 드러난 것)

- **D07**: 실제 confluence_map 발췌 데이터로 영향 범위를 측정하지 못했다
  (`data/raw/confluence_map/` 34,173개 파일 전수 확인 결과 `tech_relevant`
  필드가 있는 파일 0개 — 분류는 라이브 Confluence 크롤 시에만 계산되고
  로컬에 저장된 스냅샷은 그 이전 형식이다). 제목 전용으로 좁힌 것은 이
  미검증 상태에 대한 보수적 대응이지, 최적값을 실측해서 고른 것이 아니다.
- **title_match_bonus**: 위에서 설명한 대로 "순위 회귀 없음"은 확인했지만
  "의도한 시나리오에서 실제로 개선되는지"는 확인하지 못했다.
- 두 항목 모두 §12B가 요구하는 완전한 ablation이 아니라 **일부 실측**이다.
  다음 세션에서 운영 DB 또는 라이브 크롤 데이터로 재확인이 필요하다.

## 1-C. 세 번째 라운드 — P1-B 커서/신선도 정합성

사용자가 다시 "계속 진행해"라고 요청. §1-B에서 "P1-B는 성장하는 실제
코퍼스가 있어야 검증 가능해 손대지 않는다"고 썼던 판단을 재검토했다 —
**틀린 구분선이었다.** 성장하는 코퍼스가 있어야 하는 것은 watermark/coverage
**신규 스키마**이고, cursor 전진 순서 같은 **정합성 버그**는 P0-B/P0-C와
동일하게 "정상 동작 → fault injection → 재현 → 수정 → 재현 안 됨" 패턴으로
로컬에서 완전히 증명 가능하다. 이 구분으로 4개 커밋 추가.

| 커밋 | 내용 |
|---|---|
| `a877db0` | `sync()`/`_sync_map_body()` 둘 다 크롤 성공 직후 **ingest/embed 실행 전에** cursor를 전진시키고(map은 checkpoint도 삭제) 있었다(REVIEW.md item 8). ingest/embed 도중 프로세스가 죽으면 크롤된 페이지가 DB에 없는데 cursor는 이미 지나가 다음 실행이 Confluence에 재요청도 안 함 — 자동 재시도 창구가 영구히 닫힘. cursor/checkpoint 커밋을 ingest+embed 성공 이후로 미루도록 수정. DB 테스트 2개로 수정 전 코드가 실제로 실패함을 확인 후 복원. |
| `4507ab8` | 자체 재검토(advisor)에서 위 수정이 `embed_pending_chunks()` 실패도 cursor를 막는 것을 지적 — 이 함수는 모델 전체의 미임베딩 청크를 도는 전역 큐라 다음 성공 호출에서 자연 회복되는데, cursor를 막으면 "1건 일시 오류가 매번 전체 재부트스트랩을 강제" 문제(이 파일에 이미 있던 error-rate 완화 로직의 존재 이유)를 임베딩 경로에서 재현하게 된다. ingest만 cursor를 게이트하도록 분리, embed 실패는 try/except로 잡아 `embed_error`로만 노출. |
| `9a6b4a2` | confluence_map/confluence_docs/tech_repo 셋 다 Confluence API의 `version.number`를 가져오고도 버렸다(REVIEW.md item 8 "map frontmatter는 source version.number를 저장하지 않는다"). `버전번호` frontmatter 줄 → `metadata["source_version"]`으로 보존. **함정 확인**: `DocumentDraft.finalize()`의 content_hash 제외 목록에 `source_version`도 포함시키지 않으면 기존 113,611건 전체가 "내용 변경"으로 오인되어 전체 재청크·재임베딩된다 — 이를 막는 hash-exclusion을 함께 추가하고 단위테스트로 고정. source_version을 실제 신선도 판단(§9가 원하는 최종 형태)에 쓰는 것은 **의도적으로 미룸** — watermark 비교 로직·백필 비용 결정이 별도 필요. |
| `f2ea2d2` | `/v1/health`의 `documents_count`(전체 COUNT)와 `/api/wiki/stats`의 `total`(active만 COUNT)이 이름도 다르고 엔드포인트도 달라 대조 불가(REVIEW.md item 8). 기존 필드명은 그대로 두고 `active_documents_count`를 health에 추가(§9 "이름을 구분한다" — rename이 아님). |

**§9에서 여전히 미착수**: source→raw→document→active chunk→embedding→frame
단계별 coverage 리포트(existing `run_map_inventory`/ops dashboard 확장이
먼저 검토되어야 함 — 신규 서브시스템을 새로 만들지 않는다), scope/pending/
retry/dead-letter 추적, source_version을 실제 change-detection에 연결하는
전환(백필 비용 미산정). 이들은 §9가 요구하는 "기존 backfill/inventory
재사용" 원칙과 운영 정책 결정이 필요해 이번 라운드에도 시작하지 않았다.

## 1-D. 네 번째 라운드 — PR 생성 + coverage_gaps + 사용자 확인

PR #1을 생성(`https://github.com/pjhwa/citec-kb/pull/1`)한 뒤 "계속
진행해" 요청에 따라 진행. 먼저 PR 전체 diff를 커밋 단위가 아니라
파일 단위(`search.py`/`adapters.py`/`map_sync.py` — 3개 이상 커밋이 겹친
파일)로 재검토했고, 이상 없음을 확인했다(자체 재검토 이력상 처음).

**source_version→change-detection 연결 여부를 사용자에게 직접 확인**
(§1-C에서 "별도 승인 필요"로 남겼던 항목) — 배포 후 첫 sync에서 기존
confluence_map 문서 전체(로컬 스냅샷 34,173건)가 재청크·재임베딩되는
비용을 설명하고, confluence_map을 confluence_docs/tech_repo처럼 전체
본문 수집으로 바꾸는 대안도 함께 확인했다. **사용자 결정: 둘 다 보류,
현재 구조(필드만 추가, provenance 전용) 유지.** 커밋 `4c3d794`에 기록.

**추가 구현**: `coverage_gaps` — §9 "source→raw→document→active chunk→
embedding→frame 각 단계의 건수를 source ID로 대조" 중 기존
`ingest_progress()`가 다루지 않던 부분(ancestor_ids/source_version/
tech_relevant/frame 결측 카운트)을 같은 dashboard 모듈에 함수로
추가(`coverage_gaps`, `GET /v1/ops/dashboard`의 새 키) — 신규
서브시스템이 아니라 기존 모듈 확장(§9 원칙 준수). source-side 분모
(실제 Confluence/Jira에 몇 건이 있는지)는 여전히 `run_map_inventory()`의
몫이며 재구현하지 않았다.

**재현성**: `scripts/scratch_db_test.sh` 추가 — 매 커밋마다 손으로 치던
scratch DB 생성/마이그레이션/pytest 3단계를 한 명령으로. PR 리뷰어가
`scripts/scratch_db_test.sh`만 실행하면 이 세션의 모든 DB-integration
테스트를 재현할 수 있다.

## 1-E. 다섯 번째 라운드 — 운영 배포 + 실측 진단 (2026-10-01)

사용자가 PR #1을 `code.sdsdev.co.kr`(사내 GitHub)에 반영하고 운영 서버에
배포한 뒤, `scripts/collect_prod_diagnostics.sh`로 수집한 결과를 가져왔다.
배포 SHA `71272ed`(이 PR의 머지 커밋) 확인됨 — **이 섹션의 모든 수치는
BLOCKED_EXTERNAL이 아니라 실제 운영 DB(문서 113,611건, confluence_map
77,446건) 실측값이다.**

### [1] P0-B 근본 원인 — 이제 확정됨: 코드가 아니라 데이터 결측

OVE 자식(2553464543)·부모(2525893483) 두 페이지 모두 `ancestor_ids`가
**완전히 비어 있음**(타입조차 없음, NULL)을 직접 확인했다. "부모를
제외해도 자식이 v1에서 남았다"는 §5의 재현 실패는 **코드 버그가 아니라
이 두 페이지가 조상 데이터를 가진 적이 없었기 때문**이다 — 둘 다
`confluence_map`에만 존재(다른 source_type 사본 없음)하므로 "맵 이외
사본 우회" 가설도 배제된다. §5가 요구한 진단이 이제 완료됐다: **코드
수정은 필요 없고, 이 두 페이지(및 더 넓게는 아래 커버리지 결측 전체)를
포함하는 재크롤/백필이 필요하다.**

### [2] ancestor_ids/source_version 실제 커버리지 — 코드 배포 ≠ 데이터 반영 확인

| source_type | total_active | missing_ancestor_ids | missing_source_version |
|---|---|---|---|
| confluence_docs | 5,509 | **5,509 (100%)** | 5,509 (100%) |
| confluence_map | 77,446 | 28,859 (37.3%) | 77,446 (100%) |
| tech_repo | 2,800 | **2,800 (100%)** | 2,800 (100%) |

P0-B(ancestor_ids)와 P1-B(source_version) 수정 모두 코드상으로는 배포됐지만,
confluence_docs/tech_repo는 **기존 5,509+2,800건 전부**가 다음 증분
크롤 전까지 조상 정보가 없다 — §13 "코드 배포됨"과 "기존 데이터 완료"를
별도 상태로 본다는 원칙이 정확히 이 수치로 확인된다. confluence_map의
37.3% 결측은 이미 있던 기능(이 PR 이전)의 기존 백필 누락분으로 보인다.
OVE 두 페이지가 바로 이 28,859건에 포함돼 있었다.

tech_relevant 분류 분포(confluence_map, 77,446건): relevant 39,840(51.4%),
unknown 36,523(47.2%), irrelevant 979(1.3%), 미분류 104건(0.13%).

### [3] P1-A / D07 — 로컬에서 측정 못했던 블라스트 반경, 실제 데이터로 확정

실제 confluence_map 5,000건(제목+발췌+기존 tech_relevant)을 받아 신/구
`classify_map_tech()` 로직을 직접 재실행했다. 1차 시도에서 재현값과
기록값이 24% 불일치해 조사한 결과, `body_md`(breadcrumb+발췌 결합본)를
단순 500자 자르기한 탓에 발췌 경계가 어긋난 측정 오차였다 — 원래 구분자
(`\r\n\r\n`)로 정확히 분리하고, 발췌가 아예 없는 폴더형 페이지(breadcrumb만
있고 `\r\n\r\n` 없음)를 빈 발췌로 바로잡자 불일치가 1.3%(66/5,000)까지
줄었다. 이 66건(캡처 길이 500자 한계로 인한 잔여 측정오차)을 제외한
**검증된 4,934건 부분집합**에서:

- **relevant → irrelevant 전환: 2건, 전체의 0.04%**
- irrelevant → relevant, unknown 관련 전환: **0건**
- 두 전환 사례 모두 제목이 `【History】KPI 수립과정 (...)`,
  `3. 24년 KPI 항목별 정리` — 정확히 D07이 의도한 비기술 문서.

**세션 중 "로컬에 발췌 데이터가 없어 측정 불가"로 남겼던 §1-C/§1-D의
한계가 이제 해소됐다 — 실측 결과 D07 수정은 안전(오탐 0건)하고 효과적
(0.04%만 변경, 전부 의도된 방향)임이 확인됐다.** 제목 전용으로 보수적으로
좁혔던 설계가 과했는지는 — 전환 2건 모두 제목에 명확한 비기술 마커가
있었으므로 — 이번 샘플 기준으로는 추가로 넓힐 근거도, 더 좁힐 근거도
발견되지 않았다.

### [4] PIXEL 80022224 사례 재확인

`ancestor_ids`, `source_version` 모두 비어 있음, `최종수정일=2026-08-18`,
`updated_at=2026-09-24`. §9가 예시로 든 "v97 공지 누락" 여부 자체는 이
조회만으로 확정할 수 없다(Confluence 쪽의 실제 최신 버전 번호와 대조해야
함 — 그건 confluence-mcp로 직접 확인 필요, 이 세션 범위 밖). 다만
`source_version`이 비어 있다는 사실 자체가, 사용자가 §1-D에서 결정한
대로("필드만 유지, 연결은 보류") 아직 이 비교가 불가능한 상태임을
재확인한다.

### [5] documents_count(113,627) vs active_documents_count(113,611) 차이 16건 — 확정

전부 `confluence_map` source_type의 `status='archived'` 16건. REVIEW.md
item 8이 "실제 차이 16개가 어떤 상태인지 확정한 것은 아니다"로 남긴
질문에 대한 답: **vendor_docs가 아니라 archived된 confluence_map
페이지들**이었다.

### [6] 전체 ingest 체인 커버리지 (coverage_gaps 실측)

| source_type | documents | chunks | chunks_active | embeddings |
|---|---|---|---|---|
| confluence_docs | 5,509 | 39,543 | 9,514 (24%) | 9,514 |
| tech_repo | 2,800 | 31,625 | 21,192 (67%) | 21,192 |
| confluence_map | 77,446 | 135,989 | 77,462 | 77,462 |
| incident_reports | 15,358 | 36,441 | 18,802 (52%) | 18,802 |
| support_history | 2,371 | 12,571 | 11,676 (93%) | 11,676 |

모든 source에서 `embeddings == chunks_active` — 수집 시점 기준 임베딩
적체 없음(큐가 비어 있음). issue_frames 결측: incident_reports 14/15,358,
support_history 83/2,371 — 둘 다 낮음. **body_hash/extractor_version은
17,632/17,632(100%) NULL** — P0-C 마이그레이션 직후 상태 그대로이며,
이는 설계대로다(§13: 마이그레이션 자체는 재생성을 트리거하지 않음,
다음 `extract_frames()` 실행이 전체 17,632건을 1회 재생성 대상으로 봄).

### [7] exclude_subtree_ids 쿼리 실행 계획

```
Seq Scan on documents (cost=0.00..28284.78 rows=57563 width=26)
```

GIN 인덱스를 안 쓰고 Seq Scan — 다만 추정 일치 행이 57,563/113,611건
(약 50.7%)으로, **이 정도 비선택도에서는 Postgres가 인덱스보다 Seq
Scan을 고르는 것이 일반적으로 올바른 선택**이다(§5가 경고한 "GIN이
있다고 최적화를 가정하지 말라"의 반대 방향 확인 — 즉 이 자체는 버그가
아니라 "제외 조건에 해당 안 하는 쪽을 남긴다"는 질의 형태 자체가
선택도가 낮다는 뜻). exclude_subtree_ids가 실제로 소수의 ID만 제외하는
한 이 패턴은 계속 Seq Scan이 맞을 가능성이 높다 — 코퍼스가 수백만
건으로 커지면 재검토 대상.

### 다음 단계로 넘기는 것 (코드 아님, 운영 백필 승인 필요)

위 실측은 전부 **코드가 아니라 데이터/운영 백필**이 필요하다는 결론으로
수렴한다 — 이번 세션에서 코드를 추가로 고치치 않는다. 필요한 작업과
영향 범위를 수치로 명시해 다음 단계에 넘긴다:

1. confluence_docs(5,509건)·tech_repo(2,800건) 전체 재크롤 → ancestor_ids
   채움. source_version도 같은 크롤로 함께 채워짐(§1-D 결정에 따라 아직
   신선도 판단에는 미사용, provenance로만 저장).
2. confluence_map 중 ancestor_ids 결측 28,859건(OVE 두 페이지 포함)
   재크롤 — 기존 맵 백필 도구(`scripts/map_backfill.sh`) 재사용, 신규
   스크립트 불필요.
3. `extract_frames()` 1회 실행(전체 17,632 issue_frames 대상) —
   body_hash/extractor_version 채움. 비용: 17,632건 규칙 기반 재추출
   (LLM 호출 없음 — app.frames.extract는 순수 규칙 함수), DB 쓰기
   17,632건.
4. 위 3개 전부 **별도 운영 승인 후 진행** — 이 세션은 수치만 확정하고
   실행하지 않는다(§0 "운영 배포/대규모 백필은 해당 단계의 기존 승인
   범위를 확인하고 진행하라").

**2026-10-01 추가**: 사용자 요청으로 위 3개를 수행하는 스크립트
`scripts/backfill_p0b_p1b_metadata.sh`를 작성했다. 전부 **기존 도구
재사용**(새 수집기 없음): frames는 `app.frames.cli`, docs는
`Source.last_sync_at`을 NULL로 되돌린 뒤 기존 `scripts/confluence_sync.sh`
재실행(코드 변경 없이 `since=None`이면 전체 크롤하는 기존 동작을 그대로
이용), map은 기존 `scripts/map_backfill.sh --from-scratch`를 그대로 호출.
인자 없이 실행하면 현재 결측 수치만 재조회하고 아무것도 바꾸지 않음(기본값
항상 plan-only), `--apply {docs|map|frames|all}`로만 실제 적용되며 단계별
y/N 확인을 거친다. **이 스크립트 자체는 실행하지 않았다** — 운영 승인은
여전히 별도.

원본 진단 파일(제목/발췌 등 실제 운영 콘텐츠 포함, 3.6MB)은 git에 커밋하지
않고 세션 스크래치패드에만 보관했다 — `.gitignore`에 `diagnostics_*.txt`
패턴을 추가해 앞으로도 실수로 커밋되지 않게 했다.

## 1-F. 여섯 번째 라운드 — 백필 1회성 극대화 (2026-10-01)

사용자가 "백필을 여러 번 하는 느낌, 매번 수십시간 걸리니 이번 1회로
끝내고 싶다"고 문제 제기. 분석 결과:

- **"수십시간"의 정확한 원인**: `CONFLUENCE_RATE_LIMIT_RPS` 기본값 0.3(요청당
  3.33초)이 거의 전부를 설명한다 — `RateLimiter`가 요청 시작 간격만 맞추는
  단순 페이싱이라(`client.py`), 총 소요시간 ≈ 페이지 수 × 3.33초로 거의
  정확히 결정된다. confluence_map 77,446건 전체 재크롤 시 ~71.7시간,
  confluence_docs+tech_repo 8,309건 ~7.7시간. 사용자 확인: **일시
  1.0rps로 상향**(공유 계정 부하 고려해 2.0rps 대신 선택) — 환경변수이므로
  코드 변경 없음, 백필 실행 시 `-e CONFLUENCE_RATE_LIMIT_RPS=1.0` 전달.
- **"범위를 줄여 빠르게" vs "한 번에 완전하게" 트레이드오프 검토**:
  `_crawl_explicit_pages()`로 ancestor_ids 결측 28,859건만 타겟팅하면
  63% 빨라지지만, source_version은 confluence_map 77,446건 **전체**가
  결측이라 그 경우 48,587건이 영원히 비어 있게 되어 나중에 또 전체
  재크롤이 필요해진다 — "1회로 끝내기" 목표와 충돌해 **범위 축소는
  채택하지 않음**, 전체 재크롤 유지.
- **"지금 안 챙기면 나중에 또 긁어야 하는 필드" 점검** → 추가 확장 결정:
  1. **confluence_map 본문을 400자 발췌 대신 전체 저장**으로 전환.
     `get_page_full()`이 이미 `body.storage` 전체를 받아오고 있어 네트워크
     비용 추가 없음 — 지금까지 받아놓고 버리던 것을 안 버리기만 하면 됨.
     `classify_map_tech()`는 그대로 400자 발췌만 입력받아 §1-E에서 실측
     검증한 D07 동작을 그대로 보존. `chunk_markdown()`/`embed_pending_chunks()`는
     모든 source_type에 이미 공통 적용이라 코드 변경 없이 자동으로 더 많은
     청크를 만들고 임베딩한다. **`evidence_eligible`/`evidence_grade="C"`는
     변경하지 않음** — P0-A의 근거/신뢰도 계약(이번 세션 핵심 작업)은
     전혀 건드리지 않고, 발견/검색 품질만 개선(§9 "Neutron 매뉴얼 뒤쪽
     장애 절이 앞 400자 때문에 안 잡히는지" 문제를 직접 해결). 부수 효과:
     `content_hash`가 이제 본문 전체 기준이 되어, source_version을 별도
     신선도 신호로 쓰지 않아도 §9가 원래 걱정한 "본문 뒤쪽만 바뀌면 감지
     안 됨" 문제가 자연히 해소됨 — `adapters.py`의 해당 주석을 갱신.
  2. **버전 수정 시각(날짜 아닌 시각 단위) 추가 저장** — confluence_docs/
     tech_repo/confluence_map 전부 `최종수정일시각`(`version.when` 원본
     ISO8601) → `metadata["source_modified_at"]`. 추가 네트워크 호출
     없음(같은 응답의 다른 필드). §9가 나중에 요구할 "허용 지연 Δ/grace"
     계산에 필요한 시각 단위 정밀도를 지금 확보 — 역시
     `_HASH_EXCLUDED_METADATA_KEYS`에 포함해 기존 문서 재청크 유발 없음.
  3. **전체 본문의 완전 확장(confluence_map을 confluence_docs처럼 바꾸는
     것)은 검토 후 "의미 있는 작업"으로 결론**: 네트워크 비용이 이미
     매몰비용인 점, D02/D03(사본 지문 판별)도 본문 없이는 손 못 대고
     있었던 점(§8 item 8), evidence_eligible 계약을 전혀 안 건드리는 점을
     근거로 권고 — 사용자 확정.

**비용 재추정**: 임베딩 연산량이 confluence_map에서 현재(77,462개 청크,
거의 1:1)의 약 3~4배로 늘어날 것으로 추정(confluence_docs+tech_repo의
문서당 평균 활성 청크 수 ~3.7 기준 유추) — Confluence 레이트리밋과 무관한
순수 로컬 연산이라 통제 가능하지만 정확한 소요시간은 미실측. **사용자와
합의한 다음 단계: 전체 적용 전에 공간 하나로 먼저 파일럿 실행해 실제
청크/임베딩 소요시간을 측정한 뒤 전체 규모 재계산** — 이 세션은 코드
구현까지만 완료, 파일럿 실행은 하지 않았다.

## 2. 테스트 로그 (실행/실패/SKIP/BLOCKED 네 가지로 구분)

### 실행 — unit + contract (DB 불필요, `pytest tests/ -k "not _db"`)

```
368 passed, 10 skipped, 42 deselected, 1 failed in 8.00s
```

(§1-D의 coverage_gaps 3건은 DB 테스트라 이 unit 카운트에는 포함되지 않음 —
아래 DB 섹션 참고.)

(1차 라운드 종료 시점은 344 passed → §1-B에서 364 → §1-C의 P1-B 테스트
4건(source_version round-trip 3 + hash-exclusion 1)이 이후 추가되어 368.)

- **1 failed**: `test_ops_dashboard_auth.py::test_dashboard_allowed_when_auth_off_reaches_db_call`.
  이번 세션이 건드린 어떤 파일과도 무관한 서브시스템(auth/ops dashboard)이며,
  단독 실행하면 통과한다(`pytest tests/test_ops_dashboard_auth.py` → 3 passed).
  전체 스위트에서만 실패 — 테스트 간 상태 누수로 보이는 **기존 이슈**이며 이번
  구현이 원인이 아님을 확인했다(수정하지 않음 — 범위 밖, 별도 조사 필요).
- 10 skipped: `_db` 마커가 아닌데도 skip된 항목 — DB 접근성과 무관하게
  기존에 이미 skip 처리된 케이스(예: 외부 서비스 필요).

### 실행 — DB integration

`scripts/scratch_db_test.sh`(§1-D에서 추가)로 재현:

```
scripts/scratch_db_test.sh              # tests/ -k _db 전체
scripts/scratch_db_test.sh -q           # 전체 스위트(unit+db) 한 번에
```

내부적으로는 여전히:
```
docker exec citec-kb-postgres-1 psql -U citec -d postgres -c "CREATE DATABASE citec_kb_test;"
docker exec citec-kb-postgres-1 psql -U citec -d citec_kb_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_kb_test alembic upgrade head
CONFLUENCE_SYNC_TEST_DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_kb_test pytest tests/ -k _db
```

```
44 passed, 1 failed, 379 deselected
```

(1차 라운드 35 → §1-B에서 37(tie-break 결정성 2건) → §1-C에서 41(cursor
fault-injection 2건 + embed-failure 1건 + health active_documents_count
1건).) 같은 pre-existing 실패 1건(위와 동일 테스트, 동일 원인).
**citec_knowledge(운영 DB)는 이번 세션에서 한 번도 쓰기 연결하지 않았다**
— scratch DB `citec_kb_test`를 커밋마다(총 4회) 새로 만들고 검증 직후
`DROP DATABASE`로 정리했다(운영 컨테이너에 남기지 않음). §1-B의
title_match_bonus A/B에서도 같은 scratch DB에 `data/raw/`의 실제
5,065개 문서를 ingest해 사용했고, 검증 후 동일하게 정리했다. 다음 세션은
위 재현 커맨드 3줄로
동일하게 다시 만들면 된다.

### BLOCKED_EXTERNAL

- 운영 DB read-only 조회(§5 진단 순서: page의 실제 ancestor_ids 값/JSONB
  타입/배포 SHA/EXPLAIN) — 이번 세션에 운영 DB 접근 권한 없음.
  **다음 세션이 이어서 할 일**: `docker exec citec-kb-postgres-1 psql -U citec -d citec_knowledge`로
  read-only 접속 후 `SELECT id, external_id, metadata_->'ancestor_ids' FROM documents WHERE external_id IN ('2553464543','2525893483');`
  류의 조회로 실제 원인(조상 데이터 결측 vs 배포 SHA 지연)을 특정.
- 대량 backfill(P0-B의 ancestor_ids를 기존 confluence_docs/tech_repo 행에
  소급 적용) — 코드는 다음 증분 sync부터 새로 쓰는 행에 적용되지만, 기존
  행을 갱신하려면 재크롤 또는 별도 backfill 스크립트가 필요하다. 이번
  세션은 구현하지 않았다(§9 "운영 전체 백필을 코드 배포 후 자동 부수 작업으로
  실행하지 않는다"에 따라 별도 승인 필요).
- Confluence 계획안/결과/Tier 2 페이지의 버전 재확인(§1) — confluence-mcp
  라이브 접속 없이 이번 세션에서 재확인하지 않았다.

## 3. 이번에 하지 않은 것과 이유

| 절 | 내용 | 이유 |
|---|---|---|
| P0-A 나머지 | `EvidenceRef` 공통 dataclass, kb_query intent별 적용범위 명세, 문서 조회 length/offset 계약, localhost:8572 교정 | 이번 세션은 "포인터가 근거로 위장되는 구체적 경로"를 닫는 데 집중 — 전체 계약 재설계는 더 큰 단위 |
| P1-A 나머지 | 원질 보존(multi_query 재평가), 사본 content-fingerprint 다양성(D02/D03), 폴더/허브 표시, "content purpose vs 기술도메인" 완전한 2축 재설계 | D06/D07(부분)/§8 item5·6은 §1-B에서 구현. 나머지는 회귀 질의+holdout 코퍼스, 또는 사본 실체 확인용 라이브 Confluence 접근이 필요 |
| P1-B 나머지 | source→document→chunk→embedding→frame coverage 리포트(기존 ops dashboard/run_map_inventory 확장 검토 필요), scope/pending/retry/dead-letter 추적 | cursor 순서/source_version 보존/health 네이밍은 §1-C에서 구현(fault-injection으로 로컬 증명 가능했음). 나머지는 대규모 backfill·운영 정책 결정 또는 기존 서브시스템 확장 검토가 먼저 필요 |
| source_version→change-detection 연결 | confluence_map의 400자 이후 본문 변경 감지(§9 최종 형태) | **사용자에게 직접 확인함(2026-10-01)**: 연결 시 배포 후 첫 sync에서 기존 confluence_map 문서 전체(로컬 스냅샷 기준 34,173건)가 "버전 정보 없음"으로 재청크·재임베딩되는 비용을 설명 — "지금은 필드만 유지, 연결은 보류"로 명시적 결정. confluence_map을 confluence_docs/tech_repo처럼 전체 본문 수집으로 확장하는 대안도 함께 물었으나 "현재 구조 유지"로 결정. 코드는 이미 이 결정과 일치하는 상태(source_version은 provenance로만 존재, content_hash에서 제외되어 재임베딩을 유발하지 않음) — 추가 구현 불필요. |
| P1-C 나머지 | 내장 생성 capability 정직화(status/answer_kind 필드), trust/engine의 canonical-source 완전 dedup(D10 완전판) | D13/citation-repair 위장은 §1-B에서 구현. Fabrix가 현재도 미지원이라 생성 capability 자체의 실사용 영향은 적음 |
| P2 | 계측/성능 튜닝 | §11 1단계(결정론적 경로 우선)는 §1-B에서 **이미 충족됨을 확인**. 2단계 이후는 p50/p95 등 라이브 계측 없이는 손대지 않음(§11 원칙) |
| 파일럿(§12C) | Direct vs KB-assisted 업무효용 비교 | §12C 자체가 "차기 별도 파일럿"으로 설계 지정 — 이번 세션 범위 아님 |

## 4. 되돌리는 방법

모든 변경은 additive(신규 필드/신규 파라미터/신규 컬럼 nullable)이거나
동작을 명확한 버그의 반대 방향으로만 좁혔다(존재만으로 skip → hash 비교로
skip, 길이 우선 → 완료상태 우선, 필터 드롭 → v1 자동 위임). 되돌릴 경우:

- `alembic downgrade -1`로 `20260930_0009` 되돌리기 가능(추가한 두 컬럼만 drop).
- 나머지는 전부 코드 레벨 git revert로 충분 — 스키마 변경 외에 데이터
  마이그레이션이 필요한 변경은 없다(P0-B의 ancestor_ids도 다음 증분 sync가
  자연히 채우는 것이지, 되돌린다고 기존 행에서 지워야 할 것도 아님).
- 프로덕션 반영 전 남은 작업: §2의 BLOCKED_EXTERNAL 두 항목(운영 DB 진단,
  backfill 승인) + 위 §3 표의 미착수 항목들.
