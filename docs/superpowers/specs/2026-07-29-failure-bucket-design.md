# 실패 버킷(failure_bucket) 카테고리 설계

- 작성일: 2026-07-29
- 배경: 사내 Claude가 citec-kb MCP를 통해 네트워크 패킷 분석을 수행하며, 과거에 확인된 실패 버킷(예:
  "LB idle-timeout으로 인한 RST", "TLS record 재조립 지연")과 그 판별 신호를 citec-kb에 지식으로
  등재·조회할 수 있게 한다. 새로운 `source_type` 카테고리(`support_history`, `tech_repo` 등과
  동급)로 신설한다.
- 참고: [TN-AutoRCA](https://arxiv.org/abs/2507.18190) (self-improving alarm-based RCA 벤치마크)에서
  "오판 사례가 쌓이면 판별 기준이 정제된다"는 self-improving 철학만 차용했다. 해당 논문은
  "evaluate-analyze-repair" 루프로 LLM 오답(Bad Cases)을 집단 분석해 프롬프트를 갱신하는 방식이며,
  "실패 버킷/판별 신호"라는 이름의 영속적 구조화 스키마는 논문에 없다 — 아래 스키마는 citec-kb
  자체 설계다.

## 1. 카테고리 범위

- `source_type = "failure_bucket"` — 범용으로 설계한다. 현재 첫 사용처는 네트워크 패킷 분석이지만
  `protocol` 필드로 도메인을 태깅해, 추후 다른 진단 영역(DB 락 경합, JVM GC 정지 등)에도 같은 틀을
  재사용할 수 있게 한다.
- 표시 라벨: "실패 패턴 라이브러리".

## 2. 데이터 모델

`apps/api/app/db/models.py`에 신규 테이블 `FailureBucket` 추가. `Checkitem`/`IssueFrame`과 같은
패턴(구조화 테이블 + `documents` 미러 행, FK 연결)을 따른다.

```
failure_buckets
├ id                       str(64), PK
├ document_id              FK → documents.id, unique, ON DELETE CASCADE  (검색/청킹/임베딩용 미러)
├ bucket_name              str(256)           "LB idle-timeout으로 인한 RST"
├ protocol                 str(32), nullable  "TCP" | "TLS" | "HTTP" | …  (indexed)
├ symptom                  text               관찰되는 현상 설명
├ discriminating_signals   ARRAY(str)         ["RST 직전 idle ≥ N초", "FIN 없이 RST"]
├ counter_signals          ARRAY(str)         ["RST 이전 재전송 다수 관찰"]  (반증 신호)
├ root_cause               text
├ recommended_action       text
├ confidence                float, default 0.5
├ support_count             int, default 0    # 판정에 부합해 확인된 횟수
├ counter_count             int, default 0    # 오탐으로 반박된 횟수
├ evidence_grade            str(8)            "machine" 고정 (§5)
├ created_by                str(128), nullable  # 세션/호출자 식별자
├ created_at / updated_at
└ Index(protocol), Index(bucket_name)
```

**신뢰도 갱신 규칙**: 정제 호출 시 `support_count`/`counter_count`를 증가시키고
`confidence = support_count / (support_count + counter_count + 1)` (라플라스 스무딩)로 재계산한다.

**재임베딩 규칙**: `discriminating_signals`/`counter_signals` 등 신호 리스트가 실제로 바뀔 때만
미러 `Document`를 재생성 → 재청킹 → 재임베딩한다. `confidence`/카운트만 바뀌는 정제 호출은
테이블 행만 갱신하고 문서 재생성을 생략한다 (매 확인/반박마다 재임베딩하면 낭비가 크다).

미러 `Document`의 `body_md`는 전문검색이 가능하도록 신호를 평문으로 풀어쓴다:

```
# [failure_bucket] LB idle-timeout으로 인한 RST
증상: ...
판별 신호:
- RST 직전 idle ≥ N초
- FIN 없이 RST
반증 신호:
- RST 이전 재전송 다수 관찰
근본원인: ...
조치: ...
```

`metadata_`(JSONB)에는 구조화 필드 전체를 중복 저장한다. `apps/api/app/taxonomy.py`의
`infer_domain()`은 `source_type == "checkitem"`일 때 `metadata["Area"]`로 domain을 바로 채우는
기존 분기와 같은 방식으로, `source_type == "failure_bucket"`일 때 `protocol`을 domain으로 매핑하는
분기를 추가한다.

## 3. API 표면 (`/v1/failure-buckets/*`)

신규 라우터 `apps/api/app/routers/failure_buckets.py` (`insights.py`/`checkitems.py` 패턴 준용):

| 메서드 | 경로 | 역할 |
|---|---|---|
| `POST` | `/v1/failure-buckets` | 신규 버킷 등록(write) — `bucket_name`, `symptom`, `discriminating_signals`, `counter_signals`, `root_cause`, `recommended_action`, `protocol`, `created_by` → Document 미러 생성 + 임베딩까지 즉시 수행 |
| `POST` | `/v1/failure-buckets/{id}/refine` | 신호 추가/수정, support/counter 카운트 갱신, confidence 재계산(신호 변경 시만 재임베딩) |
| `GET` | `/v1/failure-buckets/{id}` | 단건 조회(구조화 필드 전체) |
| `GET` | `/v1/failure-buckets` | 목록(protocol/min_confidence 필터) |
| `POST` | `/v1/failure-buckets/match` | `observed_signals[] + symptom` → 후보 버킷 순위 |

### 매칭 로직 (`/match`)

`apps/api/app/si/retrieve.py`의 applicability 패턴을 재사용한다: 관찰 신호와
`discriminating_signals`/`counter_signals`의 토큰 겹침을 스코어링하고, `confidence`를 가중해
최종 순위를 산출한다. 반증 신호가 매칭되면 해당 버킷은 감점되고 응답의 `contradicted` 목록에
표시된다. 응답 형태 예:

```json
[
  {
    "bucket_id": "...",
    "bucket_name": "LB idle-timeout RST",
    "matched_signals": ["RST 직전 idle ≥ N초"],
    "contradicted": [],
    "confidence": 0.82,
    "label": "가능"
  }
]
```

### 기존 하드코딩 지점 업데이트

- `apps/api/app/doc_access.py`: `checkitem`처럼 `failure_bucket`에 대해 `body_api_rel`을
  `/v1/failure-buckets/{eid}`로 매핑하는 분기 추가.
- `apps/api/app/routers/external_compat.py`: `_SECTION_MAP`, `_TEMPLATE_LABELS`,
  `_resolve_document()`의 하드코딩된 source_type 집합(L426–435)에 `failure_bucket` 추가.
  빠뜨리면 `/api/wiki/file` 및 `kb_get_document`가 `failure_bucket/xxx.md` 경로를 못 찾는다.

## 4. MCP 도구 (`mcp-server/server.py`)

```python
kb_register_failure_bucket(
    bucket_name: str, symptom: str, discriminating_signals: list[str],
    root_cause: str, recommended_action: str,
    counter_signals: list[str] = [], protocol: str = "",
) -> str

kb_refine_failure_bucket(
    bucket_id: str, add_signal: str = "", add_counter_signal: str = "",
    confirm: bool = True, note: str = "",
) -> str

kb_match_failure_bucket(
    observed_signals: list[str], symptom: str = "", protocol: str = "",
) -> str

kb_list_failure_buckets(
    protocol: str = "", min_confidence: float = 0.0, limit: int = 20,
) -> str

kb_get_failure_bucket(bucket_id: str) -> str
```

- `kb_tools_help()` (server.py L820)에 위 5개 도구를 반드시 추가한다 — 목록에 없으면 에이전트가
  도구 존재를 인지하지 못한다.
- `kb_search`/`kb_ask`의 `source_type`/`template` 화이트리스트에도 `failure_bucket`을 추가한다.
- `mcp-server/test_smoke.py`에 신규 도구 스모크 테스트를 추가한다.

## 5. 쓰기 신뢰 경계

- MCP를 통한 쓰기이므로 `evidence_grade`를 사람이 작성한 문서와 구분되도록 `"machine"` 값으로
  고정 저장하고, `created_by`에 세션/호출자 식별자를 남긴다.
- 별도 인증 게이트는 두지 않는다 — citec-kb는 폐쇄망 내부망 서비스이며, 기존 `/v1/insights` write
  경로도 동일하게 무인증이다.
- `apps/api/app/ops/dashboard.py`에 "최근 등록된 failure_bucket" 위젯을 추가해 사람이 사후
  검토할 수 있게 한다(최근 N건: bucket_name, protocol, confidence, created_by, created_at).

## 6. 문서/스크립트 갱신 대상

- `README.md` (MCP 도구 표에 5개 도구 추가)
- `docs/MCP.md` (도구 표 상세)
- `docs/EXTERNAL_API.md` (`/v1/failure-buckets/*` 엔드포인트 문서화)
- `docs/AI_AGENT_GUIDE.md` (도구 선택 가이드에 실패 버킷 조회/등록 시나리오 추가)
- `references/corpus-taxonomy.md` (신규 카테고리 섹션 추가 — 단, 이 문서는 `data/raw/` 실측
  스캔 산출물이므로 `failure_bucket`은 raw 파일 기반이 아니라 API/MCP로 직접 생성되는 카테고리임을
  명시)
- 위 `docs/*.md` 수정 후 `.venv/bin/python scripts/render_docs_html.py` 재실행 (생성된
  `apps/web/public/docs/*.html`은 직접 편집하지 않는다)
- Alembic 마이그레이션 신규 파일 1개 (`apps/api/alembic/versions/`) — `failure_buckets` 테이블 생성

## 7. 범위 밖 (Out of scope)

- `data/raw/`에서 파일 기반 배치 적재하는 어댑터(`ingest/adapters.py`의 `ADAPTERS` 등록)는 만들지
  않는다. 등재는 MCP 쓰기 툴을 통한 실시간 등록만 지원한다.
- Insight의 draft→review→approved 승인 워크플로는 재사용하지 않는다. `failure_bucket`은 등록 즉시
  검색에 노출되며, 대시보드 위젯으로 사후 가시성만 제공한다.
- 사용자/세션별 쓰기 권한 분리, rate limit은 이번 설계에 포함하지 않는다.
