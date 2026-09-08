# citec-kb: SWIM(incident_reports) 유사장애·자연어질의 확장 (Phase 2)

## 배경

Phase 1(어댑터 추가 + 15,333건 대량 적재 + 재임베딩)이 운영서버에서 완료됐다.
`source_type='incident_reports'`로 SWIM 장애 15,333건이 이미 `documents` 테이블에
있고, `kb_ticket(source_type="incident_reports")`/`kb_search(section="incident_reports")`
로는 이미 조회가 된다(코드 변경 없이 됨 — Phase 1 완료 시 확인됨).

이번 Phase 2는 그걸로는 안 되는 두 가지를 고친다:

1. **`kb_similar_incident`(유사장애 검색)가 SWIM을 전혀 못 찾는다** — API 레벨에서
   `source_type="support_history"`로 하드코딩돼 있어서다.
2. **`kb_query`(자연어 통합 질의)가 "SWIM 장애 몇 건" 같은 질문을 지원이력(CITECTS)
   집계로 잘못 라우팅하거나 아무 것도 못 찾는다** — 의도분류기 4개 파일이 전부
   "지원건/기술지원" 키워드만 `source_type="support_history"`로 매핑하고 SWIM 관련
   키워드는 아예 인식하지 않기 때문이다.

## 목표(DoD) — 두 트랙, 독립적으로 진행 가능

### 트랙 A: `kb_similar_incident`에 SWIM 포함 (우선순위 높음, 변경 범위 작음)

**파일: `apps/api/app/si/retrieve.py`**

`similar_incidents()` 함수(98번째 줄) 안, 123번째 줄:
```python
filters = SearchFilters(source_type="support_history", status="active")
```

**하지 말 것**: `SearchFilters.source_type`을 `Optional[str]` → `list[str]`로 바꾸는 것.
이 클래스와 그 값을 `==` 비교로 쓰는 `hybrid_search`(`retrieval/search.py`)는 코퍼스
전역에서 공유되는 코드라, 타입을 바꾸면 다른 호출부(검색 API 등)에 영향을 줄 수 있다.
블라스트 반경을 최소화하려면 **`similar_incidents()` 내부에서 `hybrid_search`를
`source_type`별로 따로 호출해서 병합**하는 방식을 쓴다 — 이 파일이 이미 156~184줄에서
"검색 결과 + 별도 쿼리 결과를 `ordered_docs`/`hit_by_doc`/`seen`에 병합"하는 패턴을
쓰고 있으니 그 패턴을 그대로 재사용한다:

```python
SI_SOURCE_TYPES = ("support_history", "incident_reports")

...
for st in SI_SOURCE_TYPES:
    filters = SearchFilters(source_type=st, status="active")
    if environment:
        filters.environment = environment
    resp = hybrid_search(
        session,
        SearchRequest(q=q, top_k=max(top_k * 8, 40), filters=filters),
        query_vector=qvec,
    )
    for h in resp.results:
        if h.document_id in seen:
            continue
        seen.add(h.document_id)
        ordered_docs.append(h.document_id)
        hit_by_doc[h.document_id] = h
```
(원래 있던 단일 `hybrid_search` 호출을 이 루프로 교체. `resp.trust_retrieval` 등
마지막 응답 메타를 어떻게 합칠지는 기존 `retrieval` 필드 구성을 참고해서 판단할 것 —
예: 마지막 루프 결과의 `trust_retrieval`을 쓰거나, `candidates`만 합산.)

157~184번째 줄의 두 번째 쿼리(IssueFrame 텍스트 매칭 주입, `Document.source_type ==
"support_history"`로 고정)는 **이번 트랙에서 건드리지 않는다** — 이유는 아래 "알려진
제약" 참고.

**MCP 서버(`mcp-server/server.py`)는 이번 트랙에서 변경하지 않는다.** `kb_similar_incident`
도구(714번째 줄)에 새 파라미터를 추가하는 게 아니라, API가 기본으로 검색하는 범위 자체를
넓히는 방식이라 MCP 시그니처는 그대로 둬도 자동으로 SWIM이 결과에 섞여 나온다. (예전에
"API+MCP 양쪽 다 고쳐야 한다"고 판단했던 건, source_type을 호출자가 고르는 파라미터로
노출하는 설계를 전제로 한 것이었다 — 이번엔 그 설계를 안 쓰고 기본 검색범위 자체를
바꾸는 더 간단한 방식을 쓴다.)

**알려진 제약(이번 트랙에서 고치지 않음, Phase 3 후보):**
`IssueFrame`(증상/근본원인/조치 구조화 테이블)은 `frames/job.py`가 `source_type=
"support_history"`로 고정 추출한 것만 있다. SWIM 문서는 `IssueFrame` 행이 없으므로,
검색에 걸려도 `si/retrieve.py` 244~273번째 줄의 "issue_frame 없음 — 검색 스니펫만
제공" 폴백 경로를 탄다(원인/조치 없이 title/스니펫만). **이 정도로도 "유사 사례가
있다"는 신호는 충분히 준다** — SWIM까지 frame 추출을 확장하는 건 이번 스코프 밖.

### 트랙 B: `kb_query` 자연어 라우팅에 SWIM 인식 추가

대상 파일 4개, 전부 같은 원칙: **기존 "지원이력/기술지원" 키워드 분기는 절대 건드리지
말고, SWIM 전용 키워드를 감지하는 새 분기를 추가**한다. SWIM 트리거 키워드 제안(부서
관례상 이미 쓰는 표현): `SWIM`, `전사\s*장애`, `장애\s*보고서`, `SWIM\s*장애` — 필요시
조정.

1. **`apps/api/app/query/time_range.py`** (123~145번째 줄, `detect_time_scoped_list`):
   현재 `supportish`(`_SUPPORT_HINT` 매치) 감지 후 `source = "support_history"`로
   고정하는 로직 옆에, SWIM 키워드 감지 시 `source = "incident_reports"`가 되는 분기
   추가. "지난 주 SWIM 장애 몇 건" 같은 질의가 대상.

2. **`apps/api/app/query/exhaustive.py`** (37~38번째 줄, `detect_exhaustive_intent`):
   `source_type = "support_history"` 판정 옆에 SWIM 키워드 분기 추가.

3. **`apps/api/app/query/analytics_intent.py`**: `detect_analytics_intent`가 리턴하는
   여러 분기(`title_tokens` 모드, 일반 집계 모드 등)에 전부 `"source_type":
   "support_history"`가 하드코딩돼 있다(93, 161번째 줄 등). "SWIM 장애 월별 추이",
   "SWIM 장애유형별 건수" 같은 질의를 위해 SWIM 키워드 감지 시 `source_type=
   "incident_reports"`로 바뀌는 분기 추가. **주의**: `_COMP_MAP`(장애지원/기술지원/
   진단컨설팅 — Jira Component 값)과 SWIM의 `장애유형`(NW/Infra/Cloud/...) 필드는
   이름은 비슷해도 다른 축이다 — 혼동해서 매핑하지 말 것. SWIM 집계축이 필요하면
   `group_by="component"`가 아니라 별도 처리가 필요할 수 있음(아래 "확인 필요" 참고).

4. **`apps/api/app/query/planner.py`**: 위 세 detector의 리턴값을 그대로 실행하는
   `execute_plan()`이 `plan.get("source_type") or "support_history"` 폴백을 여러 곳에서
   쓴다(224, 231, 243, 315, 370번째 줄 등). detector가 `source_type="incident_reports"`를
   올바르게 채워 리턴하면 `execute_plan`은 **수정 없이 그대로 통과**시킬 가능성이 높다 —
   먼저 위 1~3번을 고치고 `plan_query()` 출력만 확인해본 뒤, 실제로 `execute_plan`
   수정이 필요한지 판단할 것(불필요한 수정 금지 원칙).

**확인 필요 — 구현 전에 먼저 판단할 것**: `apps/api/app/analytics/aggregate.py`의
`aggregate_tickets()`/`title_token_stats()`가 `source_type` 파라미터를 받아 그대로
`Document.source_type == source_type` 필터링만 하는지, 아니면 `support_history`
전용 컬럼(예: Jira `Component`, `Status` 같은 `metadata` 키)에 의존하는 로직이 섞여
있는지 반드시 먼저 읽어보고 판단하라. SWIM의 `metadata` 키 이름(`장애유형`, `진행상태`
등)은 지원이력의 `Component`/`Status`와 다르므로, `aggregate_tickets`가 특정
`metadata` 키를 하드코딩해서 읽는다면 SWIM 집계는 별도 함수가 필요할 수도 있다 —
이 경우 무리하게 기존 함수를 억지로 재사용하지 말고, 사용자(요청자)에게 먼저 확인하라.

## 테스트

- 트랙 A: `si/retrieve.py`에 대한 기존 테스트(`test_si_ranking.py`)가 있다 — 이걸
  먼저 통째로 돌려서 **기존 support_history 케이스가 회귀 없이 그대로 통과하는지**
  확인한 뒤, SWIM 문서가 섞여 나오는 신규 케이스를 추가한다(가짜 `incident_reports`
  문서 fixture 하나 심어서 검색되는지 확인).
- 트랙 B: `test_time_range.py`, `test_analytics_intent.py`, `test_planner.py`,
  `test_prevention_exhaustive.py` 각각에 기존 지원이력 케이스가 회귀 없이 통과하는지
  먼저 돌려보고, SWIM 키워드 질의 케이스를 추가.
- **두 트랙 다 "기존 케이스 회귀 없음"이 새 케이스 추가보다 우선이다.** 기존 테스트가
  하나라도 깨지면 그 변경은 되돌리고 다시 설계할 것.

## 스코프 밖

- SWIM 문서에 대한 `IssueFrame` 추출(`frames/job.py` 확장) — Phase 3 후보, 이번엔
  스니펫 기반 폴백으로 충분.
- `kb_search`/`kb_ticket`/`kb_list_tickets` — 이미 Phase 1에서 동작 확인됨, 이번엔
  손대지 않는다.
- SWIM 신규 발생분 정기 반영(증분 파이프라인) — 별도 작업.

## 완료 후 보고 형식

1. 트랙 A/B 각각 변경 파일 목록 + diff 요약
2. 기존 테스트 스위트 전체 실행 결과(회귀 없음 확인 — 실패 0건)
3. 신규 테스트 실행 결과
4. 트랙 A 실증: `kb_similar_incident(symptom="<SWIM 샘플 제목에서 뽑은 증상 문장>")`
   MCP 호출 결과에 `incident_reports` 문서가 `cases`에 포함되는지
5. 트랙 B 실증: `kb_query(q="지난 주 SWIM 장애 몇 건이야")` 류 호출이 올바른
   `source_type=incident_reports`로 라우팅되는지 (API 응답의 `note`/`params` 확인)
6. 트랙 B에서 "확인 필요" 항목(aggregate_tickets의 metadata 의존성)을 조사한 결과와,
   별도 함수가 필요하다고 판단했다면 그 근거

