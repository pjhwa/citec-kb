# citec-kb: Phase 2 배포 후 검증 실패 2건 수정

## 배경

`CITEC_KB_SWIM_PHASE2_PROMPT.md`(트랙 A: `kb_similar_incident`에 SWIM 포함, 트랙 B:
`kb_query` 자연어 라우팅에 SWIM 인식 추가)를 운영서버에 배포한 뒤 MCP로 직접 검증했다.
결과: **트랙 B는 라우팅까지는 되는데 집계 로직이 0건을 반환하는 버그가 있고, 트랙 A는
SWIM 문서가 전혀 결과에 안 섞여 나온다.** 아래는 각각 재현 방법과 근본원인(트랙 B는
코드까지 특정 완료), 수정 방향이다.

데이터 자체는 문제없다 — 이번 검증에서 `kb_stats`(`incident_reports: 15333`),
`kb_ticket(source_type="incident_reports")`, `kb_search(section="incident_reports")`는
전부 정상 확인됐다. 이번 수정은 트랙 A/B 코드에 국한된다.

## 버그 1 (트랙 B) — 근본원인 특정 완료: `aggregate_tickets`가 SWIM 문서를 전부 날짜
필터에서 탈락시킨다

### 재현
`kb_query(q="지난 주 SWIM 장애 몇 건이야")` MCP 호출 결과:
```
note: 집계·건수/제목토큰 — incident_reports metadata COUNT (LLM 미사용).
param.group_by=total
param.date_from=2026-08-31 param.date_to=2026-09-06
group_by=total total=0 method=metadata_aggregate
```
`source_type=incident_reports`로 라우팅 자체는 정확하다(트랙 B의 키워드 감지는 배포·
동작 확인됨). 그런데 해당 기간에 SWIM 장애가 실제로 여러 건 존재하는데(예: failSeq
`26083161345`(2026-08-31), `26090161346`(2026-09-01), `26090361350`/`26090361351`
(2026-09-03/04), `26090561354`(2026-09-05) 등) `total=0`이 나왔다.

### 근본원인
`apps/api/app/analytics/aggregate.py`의 `aggregate_tickets()`:

```python
if date_field not in {"Created", "Resolved", "Updated"}:
    date_field = "Created"
...
raw = meta.get(date_field)              # meta = Document.metadata_ (JSONB)
dt = parse_meta_date(raw if isinstance(raw, str) else None)
if date_from or date_to:
    if dt is None:
        continue                         # ← 여기서 전부 탈락
```

`date_field`는 항상(호출부에서 명시적으로 다른 값을 안 주면) `"Created"`로 고정된다.
그런데 `incident_reports` 어댑터가 만든 `Document.metadata_`에는 `"Created"` 키가
없다 — SWIM 원본 헤더 필드는 `"발생일시(한국)"`라는 다른 키로 들어가 있다(어댑터
프롬프트 명세대로 pipe 헤더의 key를 그대로 metadata dict 키로 썼다면). 그래서
`meta.get("Created")`가 항상 `None` → `dt = None` → 날짜 범위가 지정된 모든 조회에서
SWIM 문서가 전부 걸러진다. `parse_meta_date`의 정규식(`tickets/query.py:16`,
`(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})`)은 "2026-08-31 03:10" 같은 값에서도 문제없이
날짜를 뽑아내므로, **문제는 정규식이 아니라 어떤 metadata 키를 읽느냐다.**

### 수정 방향

1. 먼저 실제 배포된 `incident_reports` 문서의 `metadata_` 키 이름을 확인한다:
   ```bash
   docker compose exec postgres psql -U citec -d citec_knowledge -c \
     "SELECT metadata FROM documents WHERE source_type='incident_reports' LIMIT 1;"
   ```
   날짜가 들어있는 실제 키 이름이 `"발생일시(한국)"`이 맞는지 먼저 확인 — 다르면 아래
   매핑을 그 키 이름으로 맞춘다.

2. `apps/api/app/analytics/aggregate.py`에 source_type별 날짜 필드 매핑을 추가한다.
   `date_field` 파라미터 검증 로직을 아래처럼 source_type-aware로 바꾼다(기존
   `support_history` 동작은 절대 바꾸지 않는다 — 회귀 금지):
   ```python
   _VALID_DATE_FIELDS = {
       "support_history": {"Created", "Resolved", "Updated"},
       "incident_reports": {"발생일시(한국)"},
   }
   _DEFAULT_DATE_FIELD = {
       "support_history": "Created",
       "incident_reports": "발생일시(한국)",
   }

   def aggregate_tickets(*, source_type: str = "support_history", ..., date_field: str = "Created", ...):
       allowed = _VALID_DATE_FIELDS.get(source_type, {"Created", "Resolved", "Updated"})
       if date_field not in allowed:
           date_field = _DEFAULT_DATE_FIELD.get(source_type, "Created")
       ...
   ```
   `title_token_stats`(같은 파일, `analytics/title_tokens.py`도 동일 패턴이면 같이 확인)도
   똑같은 `date_field` 하드코딩 문제가 있는지 점검.

3. 호출부(`apps/api/app/query/time_range.py`, `analytics_intent.py`, `exhaustive.py`,
   `planner.py`)가 `source_type="incident_reports"`로 분기했을 때 `date_field`를
   `"Created"`로 하드코딩해서 넘기고 있지 않은지 확인한다 — 트랙 B 구현 때 SWIM 분기를
   추가하면서 `"date_field": "Created"`를 그대로 복붙했을 가능성이 높다. 이 경우
   2번에서 `aggregate_tickets`가 방어적으로 고쳐도, 호출부가 애초에 유효한 값을
   넘기도록 같이 고치는 게 맞다(둘 다 고치되, 호출부 우선 — API 레이어는 방어용).

4. **재현 테스트로 고정할 것**: 위 버그 재현 케이스(`kb_query(q="지난 주 SWIM 장애
   몇 건이야")` 또는 `aggregate_tickets(source_type="incident_reports", date_from=...,
   date_to=...)` 직접 호출)를 유닛테스트로 추가해 `total > 0`을 assert. 회귀 방지용
   `support_history` 케이스(`date_field="Created"`, 기존 지원이력 기간 집계)도 여전히
   똑같이 동작하는지 같이 assert.

## 버그 2 (트랙 A) — SWIM이 `kb_similar_incident`에 전혀 안 섞여 나옴

### 재현
```
kb_similar_incident(symptom="지역정전으로 사내시스템 및 인터넷 접속 불가", top_k=5)
kb_similar_incident(symptom="Redis 타임아웃", product="모니모")
```
두 케이스 다 결과가 전부 `CITECTS-####`(support_history)뿐, `incident_reports` 문서가
0건. 첫 번째 증상 문장은 SWIM 코퍼스에 실제로 "지역정전" 패턴 장애가 다수 있고
(`kb_search(query="지역정전 인터넷 접속 불가", section="incident_reports")`로 직접
검색하면 잘 나옴 — 즉 색인/검색엔진 문제가 아니라 `si/retrieve.py`가 SWIM을 아예
검색 안 하는 것으로 보임), `kb_search`는 정상인데 `kb_similar_incident`만 안 되는
상황이다.

### 원인 특정이 안 된 상태 — 아래 순서로 좁혀갈 것

**1) 배포 여부부터 확인.** 가장 먼저 확인할 것은 트랙 A 코드가 실제로 배포된 게
맞는지다:
```bash
docker compose exec api grep -n "incident_reports\|SI_SOURCE_TYPES" /app/app/si/retrieve.py
```
아무것도 안 나오면 **트랙 A가 애초에 배포 안 된 것** — 코드 자체를 아직 안 만들었거나,
`--code` 번들에서 이 파일이 빠졌거나, 배포 스크립트가 이 파일을 반영 못 한 것이니
`CITEC_KB_SWIM_PHASE2_PROMPT.md` 트랙 A 지시대로 다시 구현/배포한다.

**2) 코드는 있는데 안 걸리는 경우.** `SI_SOURCE_TYPES` 루프는 있는데 결과가 여전히
support_history뿐이면, 다음을 의심:
- 루프가 `("support_history", "incident_reports")` 두 번째 순회에서 예외가 나고
  조용히 삼켜지고 있을 가능성(예외 로깅 없이 `except: pass` 류로 감쌌다면) — 컨테이너
  로그(`logs/api.log`)에서 `si/retrieve.py` 관련 에러 확인.
- `hybrid_search`가 `SearchFilters(source_type="incident_reports")`를 받았을 때
  실제로 `incident_reports` 문서를 후보로 내는지 별도로 확인: 같은 쿼리 문자열로
  `POST /v1/search`를 `source_type=incident_reports` 필터로 직접 호출해보고 후보가
  나오는지(= `kb_search(section="incident_reports")`가 잘 되는 걸 이미 확인했으므로
  이건 될 가능성이 높음), 되는데 `si/retrieve.py`에서만 안 걸리면 병합 로직
  (`ordered_docs`/`seen`/`hit_by_doc` 갱신) 자체의 버그.
- 최종 랭킹(206번째 줄 `scored` 계산)에서 SWIM 문서가 `frames` 테이블(IssueFrame)에
  없어 `qboost`/`resolution` 가산점을 못 받고, `top_k`(기본 3~5)에 못 들 정도로
  점수가 낮게 나오는 경우도 배제하지 말 것 — 이 경우는 버그가 아니라 랭킹 특성이니
  `top_k`를 크게(예: 20) 줘서 SWIM 문서가 순위 밖으로만 밀린 것인지, 아예 후보에도
  없는 것인지부터 구분해야 한다.

**3) 위 조사 결과를 먼저 보고할 것.** 원인이 "미배포"인지 "배포됐는데 버그"인지
"배포·정상이지만 랭킹상 밀림"인지에 따라 조치가 완전히 다르므로, 코드를 바로 고치기
전에 어느 경우인지부터 확인해서 알려달라.

## 완료 후 보고 형식

1. 버그 1: `metadata_` 실제 키 확인 결과 → 수정 diff → 재현 테스트(`total>0`) 통과 →
   `kb_query(q="지난 주 SWIM 장애 몇 건이야")` 재호출 결과(0이 아닌 실제 건수)
2. 버그 2: 위 1)~3) 조사 결과 원인 분류 → (배포 문제면 재배포, 코드 버그면 수정 diff,
   랭킹 문제면 그 사실만 보고하고 조치는 별도 논의) → `kb_similar_incident(symptom=
   "지역정전으로 사내시스템 및 인터넷 접속 불가", top_k=10)` 재호출 결과에
   `incident_reports` 문서가 섞여 나오는지
3. 기존 support_history 관련 회귀 테스트 전체 재실행 결과

