# 개발 담당 Claude에게 전달할 프롬프트: citec-kb 신뢰도·검색 효율·신선도 개선

> 작성: 2026-09-30. 이 문서는 실제 Confluence 검증 기록, 로컬 코드, 원격 최신 코드 및 오프라인 재현을 바탕으로 한 개발 지침이다. 아래 분석을 검증하며 단계적으로 구현하라. 단순 제안서로 끝내지 말고 구현·회귀시험·운영 반영 계획까지 구체화하되, 운영 배포/대규모 백필은 해당 단계의 기존 승인 범위를 확인하고 진행하라.

## 0. 목적과 고정 전제

너는 ~/dev/citec-kb 개발 담당자다. 부서 확산의 근거를 만들 수 있도록 다음을 개선하라.

1. 사내 용어·업무·상세 아키텍처 질문에서 실제 원문을 더 잘 찾는다.
2. 맵/검색 발췌/오래된 요약을 검증된 최신 사실로 오인하지 않는다.
3. 직접 Confluence/Jira MCP로 조사할 때보다 정확한 근거에 도달하는 시간과 도구 호출을 줄일 수 있는지 측정한다.
4. 데이터의 수집 범위·변경 반영 시점·파생 요약 상태를 이용자가 알 수 있게 한다.
5. 오류와 부분 성공을 숨기지 않고, “정확도 개선”을 거짓 양성 평가로 포장하지 않는다.

**citec-wiki-qa는 폐기됐다. citec-kb만 대상으로 한다.** wiki_* 별칭/API 경로 호환성은 유지할 수 있지만 옛 프로젝트의 서버·DB·캐시 문제를 현재 설계에 이식하지 않는다. 문서/스킬의 옛 설명보다 실제 코드·실측을 우선한다.

현재 작업 트리에는 사용자 작성 미추적 파일과 평가 artifacts가 있다. 삭제·덮어쓰기·일괄 커밋하지 않는다. 필요한 경우 별도 브랜치/작업 디렉터리를 사용하되 사용자의 변경을 보존한다. 코드 반영과 운영 DB 마이그레이션/백필/배포를 구분한다. 이 지침 자체가 다른 저장소 변경이나 외부 메일 전송을 승인하는 것은 아니다.

Confluence는 confluence-mcp, Jira는 jira-mcp, 원격 GitHub는 github-mcp를 먼저 사용한다. 보호 PDF가 꼭 필요하면 설치된 NASCA 스킬의 승인된 네이티브 앱 경로로만 읽는다. 이번 핵심 수정에 PDF 재추출은 필요하지 않다.

## 1. 시작 전에 읽을 자료와 버전 기준

### 필수 근거

- 계획안: https://devops.sdsdev.co.kr/confluence/x/VnMAm — 2550166358 v6
- 결과: https://devops.sdsdev.co.kr/confluence/x/YXMAm — 2550166369 v25
- 하위 Tier 2: https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=2560241077 — v1
- 위 루트의 하위 페이지는 2개, 손자 페이지는 없었음. 개발 착수 시 버전 변경 여부만 재확인.
- 로컬 심층 분석: artifacts/citec-kb-improvement-review-20260930/REVIEW.md
- 코드 근거/재현: 같은 폴더의 remote-4ef19e7/, 두 *source-snapshot.json, offline_probes.py, offline-probe-results.json
- 실측 원시 자료: artifacts/citec-kb-evaluation-20260930/의 protocol.json, paired-searches.json, controls-and-regressions.json, followup-evidence.json, current-O1.json, summary.json
- 기존 개선 프롬프트 4개: docs/CITEC_KB_EVAL_V32_IMPROVEMENT_PROMPT.md, CITEC_KB_MAP_HARDENING_PROMPT.md, CITEC_KB_MAP_COVERAGE_GAP_PROMPT.md, CITEC_KB_SWIM_INGESTION_GAP_PROMPT.md

Confluence 접속이 없으면 저장된 페이지 본문 JSON과 원시 결과를 읽고, 새 운영 검증은 미실행으로 기록한다. 결과를 추측하지 않는다. 과거 420회 평가를 무작정 재실행하지 않는다.

### 코드 상태

분석 당시 로컬 HEAD는 **878d83d0387ca193c39668d9c564ea38029ff556**, 원격 최신은 **4ef19e7e94f15851ff0b3fc2c9a4ed0422e819b0**였다. 운영은 version=0.2.0, alembic=20260928_0008이지만 배포 SHA는 미노출이다. 원격 HEAD를 운영 배포 SHA로 간주하지 않는다.

아래 파일 줄 번호는 원격 4ef19e7 기준이다. 개발 착수 시 HEAD/dirty 상태/원격 변경을 먼저 기록하고 해당 함수로 다시 위치를 찾는다. 로컬이 오래됐다고 사용자 변경 위에 무조건 pull/reset하지 않는다.

### 이미 구현된 기능을 반복 구현하지 말 것

원격에는 exclude_subtree_ids·조상 메타데이터 저장, 같은 공간/제목 사본 묶기, include_irrelevant_maps 명시 false, total_candidates 노출, Neutron 운영매뉴얼 root 추가, whole-space 설정, SWIM 유사장애 source_types 및 environment 소프트 가중치가 있다.

과거 지침의 “SWIM 완전 제외”, “Neutron은 단순 코퍼스 공백”, “total_candidates 미표시”는 현재 코드에 그대로 적용되지 않는다. 이전 라운드의 9/10번 제외는 당시 범위 결정이다. 이번에는 신선도를 분석/개선 대상으로 포함하되, 외부 Jira 수집기 신설부터 시작하지 않는다. **같은 KB 원문에 최신 조치가 있는데 요약이 뒤처진 문제는 citec-kb 내부에서도 먼저 해결할 수 있다.**

## 2. 해석을 고정할 실측

| 관측 | 해석 |
|---|---|
| 52회 동일 인자 검색에서 limit 부족 9→0회 | 반환 수 개선. 정답률 아님 |
| R1 1위 직접 관련 전체 5/8→8/8, 맵 5/8→7/8 | 작은 키워드 표본에서 개선 |
| OVE 2199354279: R3 전 조건 밖→1위 | 기존 유효 원문 발견 개선 |
| Multi-Segment 1928655522: MQ0 순위 하락, MQ1 10위 밖 | 전체 질문의 개선을 주장할 수 없음 |
| 반복 top1 동일 16/16→14/16 | 집합 안정성 개선과 별개로 1위 후퇴 |
| R3 중앙값 1.822→1.993초, 최대 28.074→63.709초 | 캐시/부하/코퍼스 비통제. 글로벌 속도 비교 금지 |
| 현재 맵 제외 시 SCP MAZ 검증 원문이 1위로 상승 | 맵의 효과는 주제/경로별로 다름 |
| 내장 생성 0/3, 스니펫 대체 3/3 | fallback 개선과 생성 성공을 분리 |
| 기간 KB 8건, component=기술지원 7건, Jira 11건 | 필터 오류와 수집 범위 차이를 분리 |
| 원문 vs LLM 요약의 조치 상태 불일치 | 검색 성공만으로 신뢰도 확보 안 됨 |

코퍼스는 70,322→113,611건, 맵 34,173→77,446건으로 변했다. 이를 순수 코드 A/B로 해석하지 않는다. 새 기능은 같은 코퍼스의 baseline/candidate와 별도 비교해야 한다.

## 3. 구현 원칙과 목표 구조

권고 흐름:

질문 → 공통 QuerySpec(목적·필터·기간·제외정책) → 저비용 후보 탐색 → 원질 기준 재평가/사본 정리 → 근거 패키지 → 필요한 원문을 matching MCP로 확인 → 답변/문서 작성

맵은 discovery pointer이고 원문 검증은 별도 단계다. 외부 Claude/Codex가 생성하는 경로에서 KB가 반드시 다시 LLM을 호출할 필요는 없다. 우선 검색·집계·근거 패키지의 유용성을 높인다. 내장 생성은 별도 capability로 정상화한다.

한 번에 검색엔진/DB/모델을 교체하지 않는다. 후보 생성, 순위, 데이터 갱신, 응답 계약을 분리해 측정 가능한 작은 변경으로 진행한다. 특정 평가 ID를 올리는 하드코딩과 정답 문장/골드 기반 질의 확장은 금지한다. CI-TEC/SCP/제품 버전 별칭은 출처·유효시점·관리자를 가진 일반 사전으로 관리한다.

## 4. P0-A: 근거 계약과 MCP 경로를 먼저 고쳐라

### 확인된 코드

- retrieval/search.py:353–366,775: map은 evidence_eligible=false.
- rag/pipeline.py:151–192와 rag/packer.py:29–65: 그 정보를 유지/검사하지 않고 인용 문맥에 넣는다.
- doc_access.py:75–83: 맵에도 kb_get_document를 full body 도구로 안내.
- mcp-server/server.py:229–243,274–288: pointer/freshness/meta가 사라진다.
- mcp-server/server.py:197–207: use_v1=false이면 exclude_* 및 multi_query 등의 옵션이 전송되지 않는다.
- _ask_impl:374–386: MCP kb_ask에는 제외 정책을 넘길 수 없다.
- offline D09/D11에서 재현.

### 변경 요구

1. EvidenceRef를 공통 모델로 정의하라. 최소 canonical_source_id, source_type, source_uri, source_version, source_modified_at, observed_at, indexed_at, evidence_kind(pointer/excerpt/fulltext/derived), evidence_read_status, freshness_status, content_hash, lineage를 구분한다. 없는 값은 null/unknown이며 현재 시각으로 채우지 않는다.
2. 검색 결과 → MCP → 문서 조회 → RAG packer → citations에서 정보가 보존되어야 한다. “원문”이라는 단어를 저장된 맵/발췌와 실제 Confluence 본문에 구분 없이 쓰지 않는다.
3. map 결과는 source_uri와 실제 source lookup을 제공한다. 기본 안내는 confluence-mcp.getPageByID(pageId, body.storage,version)이다. KB snapshot을 다시 조회하는 도구는 snapshot 조회라고 명시한다.
4. 원문 확인 전 포인터를 사실 생성의 증거로 사용하지 않는다. 검색 결과 목록/“확인할 문서”에는 사용할 수 있다. freshness 요청이나 최신 정책 판단 시 오래된 본문도 검증 대기임을 드러낸다.
5. legacy가 지원하지 못하는 필터는 조용히 무시하지 않는다. 동일한 공통 요청으로 v1에 위임하거나 명시적 unsupported 오류를 반환한다. 기존 명칭 호환성과 필터 의미를 모두 보존한다.
6. kb_search/kb_query/kb_ask의 파라미터 → API → QuerySpec → SQL → context 일관성을 계약 테스트로 검증한다. kb_query의 비검색 intent에서도 적용 범위를 명확히 하라.
7. 구조화 응답에 status=ok/degraded/error, answer_kind=generated/snippet_fallback/abstained/retrieval_only, applied_filters, ignored_filters(정상이어야 빈 목록), source coverage를 제공하라. 구형 text 클라이언트는 이를 간결한 문장으로 렌더한다. 오류 문자열을 정상 결과로만 보내지 않는다.
8. 문서 조회에 길이 제한·섹션/offset·truncated/continuation 정보를 추가해 대형 로그와 문서가 context를 소진하지 않게 한다. 조용한 절단은 금지한다.
9. 실제 운영 응답의 localhost:8572 링크를 외부 이용자가 쓸 수 있는 public URL/source_uri로 교정하고 설정 테스트를 추가한다.

### 완료 기준

- pointer-only 입력으로 unsupported 사실 답변을 생성하지 않는다.
- 동일 page의 map/fulltext/여러 chunk가 독립 근거 여러 개로 세어지지 않는다.
- v1/legacy/MCP/REST에서 같은 지원 옵션의 의미가 같다. 미지원 옵션은 명확히 실패한다.
- source body를 열지 않았을 때 “원문 확인 완료” 표시는 0건이다.
- 큰 문서에서 범위를 나눠 읽어도 원문 위치와 버전이 추적된다.

## 5. P0-B: 평가 제외 정책의 실효성을 확인하고 보완하라

### 실패 재현

공통 인자: query="OVE vs. Tanzu vs. OpenStack-Helm", section="confluence_map", limit=10, multi_query=false, use_v1=true.

1. 제외 없이 child 2553464543 노출 확인.
2. exclude_subtree_ids=["2525893483"] 추가 → 과거 실측에서 child가 여전히 1위.
3. exclude_page_ids=["2553464543"] 추가 → child 제거.
4. v1을 반복하고 legacy 경로는 P0-A 계약 수정 후 별도 검증.

현재 v1 실패가 코드 부재라고 결론 내리지 말라. 조상 저장과 SQL은 이미 있다.

### 진단 순서

- 배포 build SHA/API/MCP 버전과 요청의 applied_filters를 확인.
- DB read-only로 해당 page의 모든 source_type 표현, ancestor_ids의 존재/JSON 타입/실제 값, 수집 시각, active 상태를 확인.
- map adapter/backfill 버전과 페이지 이동 여부를 대조.
- source별 ancestor coverage를 집계. 맵 외 confluence_docs/tech_repo의 동일 page 표현도 확인.
- filters의 JSONB 연산이 의도대로 컴파일/실행되는지 scratch DB에서 검사. 일반 metadata GIN이 있다고 해당 식의 최적화를 가정하지 말고 EXPLAIN으로 확인.

### 설계

일반 검색과 strict evaluation 모드를 분리한다. 일반 검색은 unknown 조상 정보를 무조건 버려 recall을 망치지 않는다. 독립 평가에서는 검증되지 않은 lineage를 근거로 사용하지 않거나 실행을 오염 미검증으로 차단한다. strict 모드의 적용 범위와 누락 수를 응답한다.

동일 Confluence page의 map/fulltext, 명시적 copy/include 관계에 일관된 source identity를 사용한다. subtree와 별개인 사본까지 정확 ID 제외가 자동 보장한다고 설명하지 않는다. 평가 manifest에 페이지/조상/관련 평가 산출물/검증된 copy family를 고정한다. 매 검색마다 전체 Confluence 트리를 걷는 방식은 기본 경로로 채택하지 않는다.

기존 ancestor 메타데이터만 채우는 변경은 재임베딩을 유발하지 않아야 한다. backfill에는 source별 분모/결측/성공/실패/재시도/검증 receipt를 남긴다. “코드 배포됨”과 “기존 데이터 완료”를 별도 상태로 보고한다.

### 테스트

자식·손자·자기자신·이동된 페이지·unknown/빈/잘못된 타입의 조상·다른 소스의 같은 page·사본·정상 무관 페이지를 포함한다. DB opt-in 테스트가 SKIP이면 통과로 세지 않는다. 기존 tests가 legacy metadata를 의도적으로 유지한다는 사실을 strict mode 검증과 혼동하지 않는다.

## 6. P0-C: 요약과 파생 프레임을 원문 revision에 묶어라

### 원인

frames/extract.py:178–201,293–301은 요약을 먼저 채우고 더 긴 텍스트로 덮어쓰는 규칙이다. frames/job.py:64–66은 기존 frame을 source 변경과 무관하게 건너뛴다. si/retrieve.py:329–331,419–420은 저장된 frame을 브리핑에 사용한다.

D08에서 긴 “조사/검토 중” 요약이 짧은 “5/13 17:00 PB2 Fabric 연결 변경 완료” 원문을 이긴다. CITECTS-2502의 실제 원시 결과도 참고하라. Jira 상태가 닫혔다는 이유만으로 완료를 추론해서는 안 된다.

### 구현

- 원문 사실과 파생 요약을 구조적으로 구분한다. 원인/조치/계획 필드별 source_ref, source_hash/version, 시점, fact_status(추정/확인/예정/완료/충돌)를 유지한다.
- “더 길다”를 최신성 우선순위로 사용하지 않는다. 명시적인 원문 완료 문장과 이전 요약이 충돌하면 원문 근거를 제시하고 conflict/stale을 표시한다. 해석 불가면 단정하지 않는다.
- frame/summary에 derived_from_hash와 extractor_version을 기록한다. Document 내용 변경 또는 추출기 버전 변경 시 해당 문서만 재생성한다.
- 재생성 전 구 frame을 fresh로 제공하지 않는다. 원문 접근은 유지하고 stale 표시/브리핑 사용 제한을 적용한다.
- 큐 처리의 idempotency, 중복 이벤트, 실패 재시도, 중간 source 변경 시 낡은 파생물 덮어쓰기 방지를 시험한다.
- provenance가 없는 upstream LLM 요약은 “최신 확인”으로 간주하지 않는다. 기존 upstream 수집기의 계약/담당자/업로드 상태를 조사하되 citec-kb에 새 Jira 자격증명을 넣는 작업으로 이 수정을 지연시키지 않는다.
- SWIM 확장 섹션은 원문에 없음/수집 안 됨/값 비어 있음/해당 없음의 뜻을 보존한다. 기존 swim-kb-sync 작업을 중복 구현하지 않는다.

### 완료 기준

2502와 합성 회귀 사례에서 최신 원문 조치가 오래된 검토 문장에 가려지지 않는다. 원인 미확정은 확정으로 바뀌지 않는다. hash 불변 문서는 재생성하지 않고 hash 변경 문서만 재생성한다. source revision이 없는 요약은 그 한계가 조회 결과에 노출된다.

## 7. P0-D: 날짜·업무유형·집계 범위를 동시에 보존하라

planner.py:163–168의 절대 기간 early return을 단순히 위치만 바꿔 고치지 말라. intent와 제약 추출을 분리한 공통 QuerySpec을 사용하라.

필드: source_types, component/work_type, entity, status, date_field, date_from/date_to, timezone, count/list/group_by, exclusions, provenance. 사용자 표현 “지원건 전체”와 Jira component “기술지원”을 구별한다. 리스트/COUNT/analytics에 같은 predicate builder를 사용하고 normalized 필터를 응답에 표시한다.

필수 회귀:

- ISO 및 한글 2026-09-07~2026-09-13
- “지원건은 몇 건” vs “기술지원 이력 총 건수”
- 기술지원 목록, 장애지원 목록, 생성일/수정일/해결일 구분
- 상대 날짜, KST 일 경계/윤년/월말, 필터 조합
- 페이지네이션을 바꿔도 total 불변, 목록 ID와 COUNT 일치

기존 스냅샷에서 전체 8/기술지원 7이었지만 운영 데이터가 달라지면 숫자를 하드코딩하지 말고 fixture 기준을 고정한다. Jira 11과 차이 나는 2639/2640/2642는 포함 정책·업스트림 export·업로드·파싱·active 전환 각 단계로 추적한다. 업무 정책으로 제외된 TEST를 무조건 가져오거나 누락을 검색 랭킹으로 보완하지 않는다.

## 8. P1-A: 원질을 보존하는 후보 생성·순위·사본 다양성

### 확인한 문제

- search.py:135–223은 SCP/v2/네트워크 단독 분기를 만든다(D01).
- multi_query.py의 실제 6개 확장 중 원질 외 5개에는 Multi-Segment가 없다(D04).
- 하위 질의 제목 가산점 +0.25가 RRF 점수보다 훨씬 크고, weighted max 병합은 원질 관련성을 다시 평가하지 않는다.
- fts_rank가 있거나 단어 하나가 겹치면 lexical support가 참이다(D05).
- FTS/LIKE/exact 후보 LIMIT에 동점 기준이 빠진 경로가 있다.
- RAG의 set slicing 및 unordered sibling LIMIT도 제거 대상이다.
- 사본 key는 (space_key, normalized_title), 실제 Multi-Segment 3개 사본 공간이 서로 다름(D02). 같은 공간의 서로 다른 내용도 제목만 같으면 접힘(D03).

### 구현 방향

1. 원질을 must-have concept/optional context/버전/작업 의도로 분해하되 모든 단어를 hard AND로 만들지 않는다. SCP+v2+Multi-AZ+Multi-Segment처럼 식별력 있는 개념을 유지하는 확장부터 선택한다.
2. multi_query=false에서도 내부 FTS가 여러 분기를 수행한다는 현재 의미를 문서화하고 fanout_count를 계측한다.
3. candidate_budget과 returned top_k를 분리한다. 각 확장에서 조기 top_k 절단하여 좋은 후보를 잃지 않게 하되 예산을 제한한다.
4. 낮은 식별력 단어 분기는 후순위/보충용으로만 사용한다. 완화한 질의에서 얻은 결과도 **원질 기준** 개념 충족·근거 종류·본문 유무·버전 적합성을 재평가한다.
5. map/fulltext/지원이력/장애이력이 한 소스 물량에 잠식되는지 source별 후보 기여를 측정한다. 무조건 고정 quota나 evidence grade만으로 순위를 정하지 않는다.
6. deterministic tie-break를 후보 SQL 단계까지 적용한다. ANN 특성·인덱스/코퍼스 변경을 분리해 반복 안정성을 측정한다.
7. folder/empty/link-only/include-only 페이지는 탐색 허브임을 표시하고 직접 근거처럼 상위 노출되는 것을 줄인다. 연결 원문에 도달할 수 있다면 다음 lookup을 제공한다.
8. 사본은 normalized title로 후보를 만들고 실제 본문 지문·source lineage를 확인한다. 다른 space의 확인된 사본은 그룹화하되 버전/환경이 다른 문서를 삭제하지 않는다. 대표 선정 근거, 접힌 고유 source ID, 대체 버전을 반환한다.
9. 동일 원문의 map/fulltext를 하나의 source family로 연결해 검증된 본문을 우선 활용한다. 여러 chunk/여러 확장에서 duplicate_count가 중복 합산되지 않게 한다.
10. 최신 문서라는 이유만으로 역사적 질문에 우선하지 않는다. current/as-of/버전 질문을 구분해 최신성 가중치를 적용한다.

### 기술 무관 분류

classify_map_tech는 도메인 단어가 있으면 손익/KPI 문서도 relevant로 만든다(D07). 콘텐츠 목적(기술 절차/장애/설계/조직 업무/재무/기타)과 기술 도메인을 별도 축으로 분류하라. unknown은 irrelevance와 구별한다. technical 조사에 대한 잡음 억제와 CI-TEC 업무 소개 검색이 충돌하지 않게 한다. 태그 버전·분류 이유·space별 분포/오탐 샘플을 기록한다.

### 회귀 질의와 수용 기준

R1/R2/R3를 개발 회귀 집합으로 사용하고 별도 holdout을 확보한다.

- SCP v2 Multi-AZ 네트워크 Multi-Segment
- OpenShift Virtualization Engine OVE
- PISA Lookin PIXEL 관계
- OpenStack Neutron L3 에이전트 장애
- Multi-AZ with OpenStack Multi-Segment Configuration
- Ceph OSD 장애 스토리지 성능 저하
- CI-TEC 부서 주요 업무 역할
- 팀 손익 매출액 근태 인사평가
- “쿼카 은하철도 마이크로서비스 존재하지않는용어” 같은 미확인 결합
- ID exact/빈 결과/존재하나 무관/상충 버전/한글·영문 동의어

known-ID rank와 정답 원문 전체의 recall을 구분한다. 확인된 duplicate family가 top10 슬롯을 여러 개 차지하는 비율, 독립 근거 다양성, MRR/nDCG, 관련 문서의 원문 도달 성공률을 함께 본다. 알려진 OVE/CI-TEC 개선을 잃지 않아야 한다.

## 9. P1-B: 수집 범위·신선도·부분 실패를 운영 가능한 데이터로 만들기

### 기준 데이터

맵은 앞 400자, modified 날짜만 저장하며 source version.number를 잃는다. map_synced_at은 DB 갱신시각이다. 수집 cursor는 ingest/embed 전에 전진한다. 총 문서 수는 갱신 완료를 증명하지 않는다.

### 단계별 watermark와 coverage

source별로 다음을 분리한다.

- scope: 전체 space인지 roots 부분집합인지, 조회/포함/제외 정책과 버전
- source_cursor: 어디까지 발견했는지
- fetched/ingested/indexed watermark: 어느 revision이 실제 제공 가능한지
- pending/retry/dead-letter와 oldest age
- missing source_version/ancestor/classification/embedding/frame 비율
- last_successful_reconciliation_at와 변경 반영 지연 분포
- deleted/moved/inaccessible/archived/unknown 상태

수집량이 늘었다는 이유로 full coverage라고 표시하지 않는다. source→raw→document→active chunk→현재 model embedding→frame 각 단계의 건수를 source ID로 대조하라. health의 전체 COUNT와 stats의 active COUNT 차이는 문서화하고 이름을 구분한다.

### 변경·실패·백필

- 수정 timestamp는 timezone 포함 원값과 source version을 보존한다. Document.version(내부 갱신 번호)을 Confluence version으로 대체하지 않는다.
- body_hash/index_text_hash/metadata_hash를 구분해 의미 있는 내용 변경만 재임베딩한다. 같은 날 뒤쪽 본문 변경도 revision 차이는 감지해야 한다.
- cursor 전진을 금지해 전체를 매번 다시 읽는 방식만 제시하지 않는다. crawl cursor와 serving watermark를 분리하고 누락 페이지를 내구성 있는 retry queue로 보완한다.
- 기존 backfill의 resume/lock/retry를 재사용하되 config/schema/extractor 버전이 바뀌면 이전 “완료” checkpoint를 그대로 신뢰하지 않는다.
- 삭제·이동·권한 오류와 일시 timeout을 구분한다. 불완전 inventory로 대량 archive하지 않는 기존 안전장치를 유지한다.
- source 정책 수정에 대한 dry-run, 영향 건수, backfill 비용, 재개/중단/롤백 계획을 산출한다. 운영 전체 백필을 코드 배포 후 자동 부수 작업으로 실행하지 않는다.

### 맵 내용의 확장

한꺼번에 모든 원문을 임베딩하기보다 먼저 제목/헤딩/절별 대표 발췌/원문 링크/include 관계를 보존하는 “검색용 내용”을 평가하라. Neutron 매뉴얼의 뒤쪽 장애 절이 앞 400자 때문에 안 잡히는지 쿼리-절 매칭으로 검증한다.

핵심·자주 조회되는 원문부터 상세 섹션 색인/필요시 원문 확보를 적용하는 대안을 비교한다. 상세 구조를 수집하더라도 근거의 snapshot/current 구분은 유지한다. PDF·첨부·draw.io를 수집한 것처럼 표시하지 말고 coverage를 따로 둔다.

### 완료 기준

PIXEL 80022224 v97 공지와 수정시각을 기존 맵과 대조할 수 있다. 문서 이동·동일 날짜 수정·본문 뒤쪽 변경·embedding 실패·수집기 중단·부분 root 추가를 재현해 상태/재시도/검색 반영 시각을 확인한다. 원문 갱신 후 허용 지연 Δ와 grace는 source별 기존 수집 정책을 확인해 제안하라. 확인 없이 전부 실시간으로 약속하지 않는다.

## 10. P1-C: 내장 생성 capability와 신뢰도 평가를 정직하게 만들기

현재 운영 Fabrix의 non-stream/stream은 미지원이며 코드가 이를 명시적으로 거절한다. API가 살아 있다는 사실을 생성 가능으로 표현하지 않는다.

- 검색/원문/집계/임베딩/생성 capability를 따로 제공한다.
- 생성 미지원이면 retrieval_only 또는 snippet_fallback을 기계적으로 판별할 수 있게 한다. Fast/deep가 이름만 다르고 같은 실패를 내는 상태를 숨기지 않는다.
- Fabrix adapter가 승인된 API 계약을 가지고 구현 가능한지 조사한다. 미지원 streaming은 non-stream 가능 시 한 번만 호출하고, 모두 미지원이면 불필요한 생성 재시도를 하지 않는다.
- 외부 OpenRouter로 사내 내용을 자동 전송하는 fallback을 새로 넣지 않는다.
- 실패 시 같은 문서 chunk 여러 개를 중복 출처처럼 나열하지 않는다.
- citation repair 후 단순 출처 목록 추가를 factual verification으로 보지 않는다. 문장과 근거의 대응/주체/시점/부정/수치/조치 상태가 맞는지 검증한다.
- trust/engine.py는 독립 canonical source, 원문 열람 여부, 시점, 상충 여부를 입력받아 신호를 제공한다. RRF 점수·순위·토큰 중복을 정답 확률로 표시하지 않는다.
- eval/groundedness.py의 llm_error/fallback도 ok가 되는 경로(D13)를 수정한다. 기존 검사는 citation-format/overlap 검사로 남기되 answer correctness와 분리한다.

생성 미지원이더라도 외부 AI+KB 검색/근거 패키지의 업무 시험은 가능하다. 이를 내장 생성 성공률에 합산하지 않는다.

## 11. P2: 먼저 측정하고 속도를 개선하라

초기 계측은 request_id/trace_id로 다음을 구분한다.

MCP 전송 → API 대기 → planner → embedding(원질/확장별) → FTS 분기별 SQL → vector SQL → fusion/rerank → dedupe → 문서/섹션 조회 → 외부 원문 확인 → pack → generation/retry → 직렬화.

측정 항목: p50/p95/timeout, actual SQL/embedding 수, 후보/탈락/사본 수, payload bytes, 토큰 추정치/실제값, cache hit/miss, tool 호출 수, 최초 유효 근거까지/최종 검증 문서까지의 시간. 실패/기권을 제외한 성공 사례 평균만 보고하지 않는다.

후보 최적화 순서:

1. ID/기간/집계는 불필요한 임베딩 전에 결정론적 경로로 처리.
2. multi-query 최대 6회 순차 호출을 무조건 수행하지 않기: 충분한 근거가 확보되면 종료하거나 부족한 개념만 보충.
3. 같은 모델의 확장 질의를 batch encode할 수 있는지 측정. 동시성은 별도 DB session과 제한된 예산으로 처리.
4. query embedding cache는 model/config/normalization 버전에 묶고, 검색 결과 cache는 corpus revision/filter/exclusion/권한 범위를 포함. 답변 cache는 근거 revision 변경 시 무효화.
5. MCP AsyncClient 재사용/연결 풀, 중복 원문 fetch 제거, 작은 근거 패키지로 직렬화 비용 절감.
6. 선택적 reranker는 성능/정확도 ablation 이후 도입. 먼저 모델을 늘려 해결했다고 하지 않는다.
7. pgvector 검색이 embedding model을 구분하는지 확인한다. 현재 Embedding은 (chunk_id,model)을 허용하지만 vector_search에는 model 필터가 없다. 모델 교체 실험 시 서로 다른 벡터를 섞지 않도록 인덱스/serving version 계약을 먼저 보완.
8. stage별 deadline과 degraded 응답을 설계한다. timeout을 크게 늘리는 것만으로 해결하지 않는다.

코퍼스·모델·동시성·호스트·실행 순서·warm/cold를 고정한 뒤 속도를 비교한다. 기존 63.709초를 특정 병목 탓으로 단정하지 않는다.

## 12. 검증 계획 — 제품 회귀와 도입 효용을 분리

### A. 즉시 실행 가능한 개발 검증

먼저 python -X utf8 artifacts/citec-kb-improvement-review-20260930/offline_probes.py를 실행해 13개 진단을 이해하라. 현재 스크립트의 assertion은 **문제 동작 재현 확인**이다. 수정 후 제품 성공 기준으로 그대로 사용하지 말고 대응 회귀시험을 작성하라.

로컬 분석 환경에는 pytest/SQLAlchemy/pgvector가 없었으므로 공식 suite를 실행하지 않았다. 개발 환경에서는 requirements-ci와 기존 CI 설정을 확인하고 관련 unit tests를 실행하라. DB tests는 disposable scratch PostgreSQL+pgvector에서만 실행한다. CONFLUENCE_SYNC_TEST_DATABASE_URL 미설정 SKIP은 검증 완료가 아니다.

필수 계층:
- pure unit: QuerySpec, 타임존, source identity, copy key, stale 비교, 응답 상태
- contract: MCP text/structured/REST 인자 전달·출력 의미
- DB integration: 제외 조건, ancestor 결측/이동, 필터 COUNT, model version, hash 기반 파생물 갱신
- ingestion fault injection: fetch/parse/embed/frame 실패와 재개
- retrieval fixture: 핵심 개념 보존/후보 다양성/empty handling
- read-only live: 위 실제 page/ticket 사례를 snapshot 시각과 함께 재검증

### B. 같은 코퍼스의 이전/수정 코드 A/B

코드 SHA, schema, corpus manifest/hash, index build, embedding 모델, lexicon, query 설정을 동결한다. 한 번에 바뀐 요소를 기록한다. 코드/코퍼스 변경을 분리할 수 없으면 observational이라고 표시한다.

개발 집합과 숨겨진 holdout을 분리한다. 기존 R1/R3에만 맞추지 말고 제품/기간/장애유형/문서형태/신선도/무답/상충 질문을 포함한다. 평가 산출물 자체가 색인되는 누출도 차단한다.

### C. 직접 MCP 대비 업무 효용 — 차기 별도 파일럿 설계

기존 v4의 “KB 미사용군 제외” 결과는 바꾸지 않는다. 아래는 이번 개선 목표를 검증하기 위한 **새 실험**이며, 옛 420회 결과와 합산하지 않는다. 모델의 사전 지식만 사용하는 무도구 arm은 만들지 않는다.

| 조건 | 도구/자료 |
|---|---|
| Direct | 같은 AI/지침/질문/메일·PDF, confluence-mcp+jira-mcp 직접 검색·원문 확인 |
| KB-assisted | 위와 동일 + citec-kb 검색/집계/근거 패키지, 원문 확인에는 같은 matching MCP |
| Optional ablation | KB map 제외/확장 제외/원문 연결 제외를 하나씩, 품질 원인 분석용 |

조건별 fresh session, 같은 모델/설정, 결과 공유 금지, 질문별 무작위·교차 실행 순서, 같은 시간/호출 예산, 같은 접근권한을 사용한다. 정답·해설을 본 개발 담당 세션이 독립 생성 답안을 만들지 않는다. 검색캐시 warm/cold 조건을 분리하고 평가자에게 arm을 가린다.

권고 시작 표본은 8개 이상 family, 40개 이상 질문 + 장문 작성 4과제다. 이는 설계 제안이며 통계적으로 충분하다는 보장이 아니다. 파일럿 분산으로 표본 규모를 재산정하고 confidence interval을 보고한다.

장문 과제에는 Gen1/SCP v1/v2/OpenShift/OVE 비교를 포함한다. 동일 메일/PDF를 제공하고 완료된 답안 2525893483 및 자식/예상답안/AI 보조문서/이번 평가 문서를 차단한다. 고정된 원문 근거로 다음을 채점한다.

- vCenter 풀·IP 공유 제약
- SCP v1 NSX/대체안과 검증 전 상태
- SCP v2 CP/PP/PK 작업의 릴리스·대상 범위
- Multi-Segment Network/Segment/physnet/VLAN/AZ 설명
- OpenShift/OVE의 범위, PoC와 도입 결정 구분
- 계획/완료, 당시/현재, 사실/추론 분리
- 각 중요 주장과 실제 원문 위치·버전 연결

주요 지표:
- 필수 사실 정확도/누락률, unsupported·stale·scope error
- 인용 원문이 해당 문장을 실제 뒷받침하는 비율
- 정확한 근거를 확보한 업무 완료율과 적절한 기권율
- 최초 유효 원문까지/최종 검증 답변까지 시간
- 호출 수·열어본 무관 문서 수·사용자 추가 질의·토큰/비용
- 검색 R@K/MRR/nDCG와 known-anchor rank는 보조 지표

새 파일럿 목표 예: Direct 대비 중앙 업무시간 20% 감소, 정확도 비열등, p95 악화 없음. **이는 미검증 제안 목표**다. 질문별 paired 결과와 오차범위가 이를 지지하지 않으면 우월성을 주장하지 않는다. 작은 표본에서 “차이가 유의하지 않음”을 동등함의 증거로 쓰지 않는다.

## 13. 단계별 작업 순서와 납품

1. **Baseline**: build/배포/코퍼스 상태 기록, 기존 기능 확인, 재현 증거와 미확인 사항 정리.
2. **P0**: 근거/필터/실패/평가 계약, summary/frame invalidation, 날짜+component 보존. 문제 재현 → 최소 수정 → 회귀시험.
3. **P1 검색**: 원질 보존, 후보 다양성, 사본/폴더/문서 목적 분류. ablation으로 원인 분리.
4. **P1 데이터**: revision/watermark/coverage/retry, 필요한 부분 backfill과 운영 receipt.
5. **P2 성능**: 계측으로 확인된 병목만 최적화. 같은 조건에서 품질과 지연을 함께 검증.
6. **파일럿**: 코드 A/B 결과와 직접 MCP 대비 업무 효용을 별도 보고.

가능하면 작업 단위별 작은 PR로 나누고 schema/migration/MCP/UI/문서를 함께 맞춘다. 마이그레이션은 확장 → 구버전 호환 → backfill → 검증 → 기본 동작 전환 순서를 검토한다. strict exclusion/랭킹/새 응답은 feature flag로 단계 반영하고 롤백 조건을 남긴다.

최종 납품:
- 현상→원인→수정 파일/함수→검증→남은 불확실성 표
- 구현 diff와 관련 테스트 로그(실행/실패/SKIP/BLOCKED 구분)
- API/MCP 응답 계약과 클라이언트 이전 지침
- source별 coverage/freshness 및 backfill plan/receipt
- 같은 코퍼스 A/B 지표와 실패 사례 원문
- 후속 독립 업무 파일럿의 동결 질문·채점 기준·세션 지침
- 운영 대시보드에 나타낼 capability/지연/갱신/오류 지표
- 되돌리는 방법과 운영 반영 전 남은 작업

배포·DB 확인 권한이 없으면 코드/오프라인 테스트/리뷰 가능한 migration·백필 계획까지 완성하고 운영 검증만 BLOCKED_EXTERNAL로 남긴다. 소스 수정만으로 “운영 데이터 개선 완료”라고 보고하지 않는다. 외부 수집기 변경은 담당 저장소/계약을 별도로 확인하고 의존성으로 분리한다.

마지막 보고는 “몇 건 더 색인했다”보다 **어떤 잘못된 판단을 막았고 어떤 질문에서 더 적은 시간으로 올바른 원문을 찾게 됐는지**를 실제 증거와 함께 설명하라.


