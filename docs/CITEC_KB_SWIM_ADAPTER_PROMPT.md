# citec-kb: SWIM 장애보고서(incident_reports) 인입 어댑터 추가

## 배경

citec-kb 코퍼스에는 `data/raw_manifest.json`에 `incident_reports`라는 source_type이
이미 예약되어 있다(`"files": 0` — 처음부터 빈 슬롯으로 설계돼 있었다는 뜻). 이번 작업은
SWIM(전사 장애관리 시스템) 장애보고서 데이터를 이 슬롯에 채워 넣는 것이다.

SWIM 원본 데이터는 별도 사내 로컬 코퍼스(SQLite, 이 폐쇄망 밖)에 `source='swim'`으로
약 15,333건 이미 수집돼 있고, 그중 일부(장애 1건당 파일 1개)를 이번 작업의 입력으로
`data/raw/incident_reports/` 아래 미리 옮겨 두었다(또는 이 문서와 함께 전달된 샘플
파일들을 그 자리에 넣어라). **이 프롬프트는 그 파일들을 파싱해 DB에 적재하는 어댑터
코드만 다룬다 — 실데이터 대량 반입/전송 자체는 범위 밖이다.**

## 목표 (Definition of Done)

1. `apps/api/app/ingest/adapters.py`에 `incident_reports` 소스용 파서를 추가하고
   `ADAPTERS` 딕셔너리에 등록한다.
2. `apps/api/tests/test_adapters_single_file.py`에 기존 관례(예: `test_parse_support_history_file`)를
   따르는 유닛테스트를 추가한다.
3. `data/raw/incident_reports/`에 있는 샘플 파일들로 `python -m app.ingest.cli --raw-dir data/raw --sources incident_reports`
   를 로컬(docker-compose) 환경에서 실행해 에러 없이(`errors: 0`) 적재되는 것을 확인한다.
4. 적재된 문서를 `GET /v1/tickets/{external_id}?source_type=incident_reports` (또는
   해당 라우터가 실제로 쓰는 엔드포인트/쿼리)로 조회해 title/body/metadata가 원본과
   일치하는지 확인한다.
5. **스키마 마이그레이션은 만들지 않는다.** `documents` 테이블은 이미 `source_type`이
   자유 문자열이고 `metadata` JSONB에 GIN 인덱스가 있어, 신규 source_type 추가에
   Alembic 마이그레이션이 필요 없다. 마이그레이션을 새로 만들려고 하면 먼저 이유를
   재검토할 것.
6. **MCP 서버(`mcp-server/server.py`) 코드 변경도 필요 없다.** `kb_ticket`,
   `kb_list_tickets`, `kb_analytics` 계열, `kb_search(section=...)`는 이미
   `source_type`/`section` 파라미터를 그대로 API에 통과시키는 제네릭 구조라(코드 재확인:
   `kb_ticket`은 `server.py:1255`, `kb_search`의 `section`→`filters["source_type"]` 매핑은
   `server.py:147`), 어댑터+ingest만 끝나면 아래 MCP 도구 호출로 **코드 수정 없이 바로**
   조회가 된다는 걸 최종 검증 단계에서 확인하라:
   - `kb_ticket(external_id="<failSeq>", source_type="incident_reports")`
   - `kb_search(query="<샘플 제목 일부>", section="incident_reports")`
   - `kb_list_tickets(source_type="incident_reports", relative="지난 주")`
   (단, `kb_search` 독스트링의 `section/source_type: support_history|...` 나열에는
   `incident_reports`가 빠져 있으니 문서 문자열에 추가해 두는 것 정도는 이번 작업에
   포함해도 된다 — 기능에는 영향 없는 문서 갱신.)

## 입력 파일 포맷 (실측 — 아래 그대로 파싱할 것, 새 포맷 설계 금지)

파일명 패턴: `swim_<failSeq>.md` (예: `swim_26090761356.md`). `failSeq`가 SWIM 장애번호
(`external_id`로 그대로 사용).

파일 본문 구조:

```
[<failSeq>] <failTitle>
<key1>: <val1> | <key2>: <val2> | ... (가변 개수, pipe(" | ")로 구분된 key: value 나열)
■ <섹션명1>: <내용 — 여러 줄일 수 있음>
■ <섹션명2>: <내용 — 여러 줄일 수 있음>
...
```

**중요 — 포맷이 100% 균일하지 않다.** 실제 샘플 3건에서 이미 편차가 확인됐다:

- 헤더(2번째 줄)의 key 집합이 레코드마다 다르다. 신규 수집분은 7개 필드
  (`발생일시(한국)`, `고객사`, `진행상태`, `예상등급_SDS`, `장애유형`, `운영부서`,
  `신고자`, `기록구분`)만 있지만, 과거 수집분에는 `서비스 중단시간`, `GDC 여부`,
  `경영진 보고` 같은 필드가 더 붙어 있다. **고정된 키 목록을 기대하지 말고, `key: value`
  쌍을 pipe로 split해서 전부 `metadata` dict에 넣는 범용 파서로 작성한다.**
- `■` 섹션도 개수가 고정이 아니다. `■ 장애상황`/`■ 장애원인`/`■ 장애조치`는 항상
  있지만, 과거 수집분에는 `■ 반복항목`(조치자 정보 나열)이 추가로 붙기도 한다.
  **`^■\s*([^:：]+)[:：]\s*` 패턴으로 섹션 시작을 찾고, 다음 `■` 라인 전까지(또는
  파일 끝까지)를 그 섹션의 값으로 묶어라(멀티라인 허용, `re.M`/`re.S` 조합 또는 라인
  단위 상태머신으로 처리).**
- 첫 줄의 `[failSeq] title`에서 얻은 title이 실제 표시용 title이다(대괄호 안 failSeq는
  external_id로만 쓰고 title 문자열에서는 제거).

### 실제 샘플 3건 (그대로 테스트 픽스처로 사용할 것)

**샘플 A — 신규 포맷(필드 7개, 반복항목 없음):**

```
[26090761356] [삼성전자] 이라크 사무소(SELV-Iraq) 지역정전으로 사내시스템 및 인터넷 접속 불가
발생일시(한국): 2026-09-07 03:10 | 고객사: 삼성전자 | 진행상태: 조치완료 | 예상등급_SDS: X등급 | 장애유형: NW | 운영부서: 삼성SDS-SDSI | 신고자: Mirza Aziz | 기록구분: 실장애
■ 장애상황: 사내시스템 및 인터넷 접속 불가
■ 장애원인: 전원문제로 확인되며 고객사 건물 또는 지역 이슈인지 파악중
지역정전
■ 장애조치: 전원복구 후 정상화
지역정전 해소 후 서비스 정상
```

**샘플 B — 과거 포맷(필드 더 많음, `■ 반복항목` 있음, 섹션 내 멀티라인):**

```
[26082261323] [대외고객사 S-OIL] 자동배차시스템 로그인 불가
발생일시(한국): 2026-08-22 06:40 | 고객사: 대외고객사 | 진행상태: 원인분석중 | 예상등급_SDS: 4등급 | 장애유형: Infra | 운영부서: MSP인프라기술그룹(MSP인프라운영) | 신고자: 이서영 | 서비스 중단시간: 281분 | GDC 여부: N | 기록구분: 실장애 | 경영진 보고: N
■ 장애상황: 정유차량 자동 배차 불가 (장애 발생 시간에 수동배차로 진행)
※ 대외사 장비로 SWIM 조회 불가
자동배차시스템(ATSS) 로그인 및 배차 불가 (수동 배차로 우회하여 서비스 영향 최소화)
■ 장애원인: Oracle DB 블럭 손상
Oracle DB 블럭 손상 추정, 상세원인 파악 중
Oracle DB 내부 힙메모리 오류로 추정, 상세원인 파악 중
DB의 손상된 블록 복구 실패로 SMON 비정상 종료로 발생, 손상 원인은 파악 중
■ 장애조치: MSP인프라기술그룹에서 백업 파일로 DB 복구하여 정상화
넷백업 DB 파일 및 아카이브 리스토어, DB Recovery 및 재기동하여 정상화
■ 반복항목: 조치자(분류): Infra / 조치자(팀): MSP인프라운영팀 / 조치자(부서): MSP인프라기술그룹(MSP인프라운영) / 조치자: 이서영
```

**샘플 C — 짧은 케이스(섹션 내용 없음 = "상세원인 파악 중" 같은 placeholder 문구):**

```
[26082461325] [삼성SDI] ERP EP시스템 AP서버 이중화전환
발생일시(한국): 2026-08-24 13:31 | 고객사: 삼성SDI | 진행상태: 조치완료 | 예상등급_SDS: FO등급 | 장애유형: Infra | 운영부서: ERP인프라운영파트(공통플랫폼인프라운영) | 신고자: 박재현 | GDC 여부: N | 기록구분: 실장애 | 경영진 보고: N
■ 장애상황: ERP EP시스템 접속 불가
이중화전환되어 업무영향없음
■ 장애원인: 상세원인 파악 중
■ 장애조치: 자동 이중화전환
■ 반복항목: 조치자(부서): 아키텍처그룹(ERP기술) / 조치자: 전우제 / 조치자(분류): Infra / 조치자(사업부): 클라우드서비스사업부 / 조치자(팀): 공통플랫폼인프라운영그룹(MSP인프라운영) / 조치자(부서): ERP인프라운영파트(공통플랫폼인프라운영) / 조치자: 박진규
```

## 구현 지침

### 1. `apps/api/app/ingest/adapters.py`

기존 패턴을 그대로 따른다 — `parse_support_history_file`/`iter_support_history`가 가장
가까운 참고 사례다(파일 하나 파싱 함수 + 디렉터리 순회 함수 분리, `DocumentDraft.finalize()`
호출로 마무리). 새 함수:

```python
def parse_incident_report_file(path: Path) -> DocumentDraft:
    ...

def iter_incident_reports(root: Path) -> Iterator[DocumentDraft]:
    d = root / "incident_reports"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        yield parse_incident_report_file(path)
```

필드 매핑:

- `source_type`: `"incident_reports"` (고정)
- `external_id`: 파일명 또는 첫 줄 `[...]`에서 뽑은 failSeq
- `title`: 첫 줄에서 `[failSeq] ` 접두어를 제거한 나머지 (`_clip(title, 1000)` 적용)
- `body_md`: 파일 전체 원문 그대로(또는 `clean_md()` 적용 — `tech_repo`/`support_history`가
  이미 쓰는 그 함수, 재구현하지 말 것)
- `metadata`: 헤더 pipe 필드를 전부 key: value로 파싱해 dict에 넣고, `filename`도
  기존 관례대로 포함. `■` 섹션들도 `metadata["장애상황"]`, `metadata["장애원인"]`,
  `metadata["장애조치"]`, (있으면) `metadata["반복항목"]`처럼 넣는다.
- `evidence_grade`: 헤더의 `진행상태` 값이 `"조치완료"` 또는 `"종료확정"`이면 `"A"`,
  그 외(원인분석중/FRB 준비중 등 진행 중 상태)는 `"B"` — `support_history`의
  `Status` 기반 등급 로직(`"닫힘"`→A)과 동일한 사고방식을 그대로 적용.
- `source_uri`: `f"file://incident_reports/{path.name}"` (다른 어댑터 관례와 동일)
- `domain`: 헤더의 `장애유형` 값을 아래 매핑으로 변환. **매핑에 없는 값은 억지로
  추측해서 채우지 말고 `None`으로 둬라** (`taxonomy.py`의 기존 원칙 — 근거 없는 값 금지).

  | 장애유형 | domain |
  |---|---|
  | NW | network |
  | Cloud | cloud |
  | Application | middleware |
  | Infra | (매핑 안 함 — None. `taxonomy.enrich_draft_fields`의 키워드 규칙에 위임) |
  | 상용SW | (매핑 안 함 — None) |
  | Facility | (매핑 안 함 — None) |

  이 매핑 테이블은 `taxonomy.py`의 `_FB_DOMAIN_TO_CORPUS_DOMAIN`과 같은 스타일로
  `adapters.py` 안에 작은 dict로 둬도 되고, `taxonomy.py`에 `_SWIM_TYPE_TO_CORPUS_DOMAIN`
  이름으로 추가해도 된다 — 기존 `_FB_DOMAIN_TO_CORPUS_DOMAIN` 옆에 나란히 두는 편을
  권장(같은 목적의 매핑 테이블이 흩어지지 않게).
- `environment`: 채우지 않는다(`None`). SWIM 헤더에 environment에 대응하는 필드가 없다
  (`corpus-taxonomy.md` §3의 "raw 문서 어디에도 명시적으로 없음" 원칙과 동일하게 적용).

`ADAPTERS` 딕셔너리에 `"incident_reports": iter_incident_reports` 한 줄 추가.

### 2. 파싱 로직 관련 주의

- pipe 헤더 라인 split: `line.split(" | ")` 후 각 조각을 `": "` 또는 `":"` 기준으로 한
  번만 split(`maxsplit=1`) — 값 안에 콜론이 다시 나오는 경우(예: 시간 표기)가 있을 수
  있으니 주의.
- `■` 섹션 split은 정규식 `re.split(r"\n(?=■\s*[^:：]+[:：])", body)`류로 섹션 블록
  단위로 먼저 자른 뒤, 각 블록에서 `^■\s*([^:：]+)[:：]\s*(.*)$` (re.S)로 헤더/본문을
  분리하는 방식을 권장 — 상태머신을 직접 짜는 것보다 실수가 적다.
- 첫 줄이 `[failSeq] title` 형식이 아닌 경우(형식 오류)는 `support_history`가 제목 없을
  때 `path.stem`으로 폴백하는 것과 동일하게, `external_id`는 파일명에서 유추
  (`swim_<id>.md` → `<id>`), title은 원문 첫 줄 그대로 사용.

### 3. 테스트 — `apps/api/tests/test_adapters_single_file.py`

기존 3개 테스트(`test_parse_support_history_file` 등) 바로 아래에 위 샘플 A/B/C 중
최소 2개(신규 포맷 1개 + 과거 포맷/반복항목 있는 것 1개)를 `_INCIDENT_REPORT_MD_*`
상수로 넣고 `test_parse_incident_report_file` 류 테스트를 추가한다. 검증 포인트:
`source_type`, `external_id`, `title`(대괄호 failSeq 제거됐는지), `evidence_grade`
(조치완료→A, 원인분석중→B), `domain`(NW→network 등), `metadata`에 `■` 섹션들이
들어갔는지, `content_hash` 존재.

## 스코프 밖 (이번 작업에서 건드리지 말 것)

다음은 별도 작업(Phase 2)이다 — 이번 프롬프트 범위가 아니니 손대지 마라:

- `apps/api/app/query/planner.py`, `analytics_intent.py`, `time_range.py`, `exhaustive.py`
  — `kb_query` 자연어 의도분류가 "지원건" 키워드를 `source_type="support_history"`로만
  라우팅하는데, SWIM 자연어 질의 라우팅 추가는 다음 단계다.
- `apps/api/app/si/retrieve.py` + `mcp-server/server.py`의 `kb_similar_incident` 도구
  (`server.py:714`) — `si/retrieve.py:123,173`가 `source_type="support_history"`로
  API 레벨에서 고정돼 있는 데다, **MCP 도구 시그니처 자체에도 `source_type` 파라미터가
  없다**(`symptom, environment, product, service, top_k`뿐). 즉 이건 API만 고쳐서 될
  일이 아니라 API + MCP 도구 시그니처 양쪽을 같이 고쳐야 SWIM이 유사장애 검색 대상에
  포함된다 — 두 레이어 다 별도 작업(Phase 2)이며 이번 프롬프트 범위가 아니다.
- `apps/api/app/query/planner.py` 등 4개 파일이 API 레벨에서 하는 자연어 의도분류는
  위에서 이미 언급했고, 이건 API 레이어만의 문제다 — `kb_query` MCP 도구(`server.py:402`)는
  질의 문자열을 그대로 API에 넘기기만 하므로 MCP 쪽은 손댈 필요 없다.
- 실데이터 15,333건 대량 반입/재임베딩 — 이번엔 소량 샘플로 어댑터 동작만 검증한다.

## 완료 후 보고 형식

1. 변경 파일 목록 + diff 요약
2. `python -m app.ingest.cli --raw-dir data/raw --sources incident_reports` 실행 결과
   (`inserted`/`updated`/`errors` 건수)
3. 신규 유닛테스트 통과 여부(`pytest apps/api/tests/test_adapters_single_file.py -v`)
4. 조회 검증 결과 — API 직접 조회 응답 1건 + 위 목표 6번의 MCP 도구 3종 호출 결과(코드
   변경 없이 조회됐는지)

