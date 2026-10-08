# citec-kb 지식그래프 2단계 — MCP 조회 노출(`kb_graph_explore`) 설계

- 작성일: 2026-10-09
- 배경: 1단계(`docs/superpowers/specs/2026-10-07-knowledge-graph-design.md`,
  PR #15, `main`에 머지됨)에서 Neo4j에 전체 코퍼스(114,930건 문서 + 8건
  failure_bucket)를 백필하는 파이프라인을 구축했다. 하지만 §6("범위 밖")에
  명시된 대로 **실시간 조회 API/MCP 도구 노출은 전혀 하지 않았다** —
  `mcp-server/server.py`·`docs/AI_AGENT_GUIDE.md`·`kb_tools_help()` 어디에도
  그래프 참조가 0건이다. 이 문서는 그중 "장애 분석 시 연관
  컴포넌트/문서/과거 장애 탐색" 기능 하나를 MCP로 노출하는 2단계 설계다.
- **범위 밖(이 문서가 다루지 않음, 별도 2-2단계 후보)**:
  - failure_bucket 플라이휠(`create_bucket`/`refine_bucket`) 훅에 그래프
    갱신 연동 — 쓰기 경로는 이번에도 손대지 않고 `scripts/graph_sync.sh`
    배치뿐이다.
  - 티켓-티켓 `SIMILAR_TO` 확장(1단계 설계 §6 참고, 기존
    `apps/api/app/si/retrieve.py:140`의 `similar_incidents()` 재사용 가능).
  - `issue_frames.components` ↔ `checkitems.area` 매핑(1단계 설계 §6 초안,
    CI-TEC 담당자 리뷰 필요).
  - LLM 기반 의미 추출 보강.

---

## 0. 결론 먼저

**신규 REST 엔드포인트(`POST /v1/graph/explore`) + MCP 프록시 툴
(`kb_graph_explore`) 하나를 추가한다.** 기존 "MCP = REST 위의 얇은 프록시"
아키텍처(`mcp-server/server.py`)를 그대로 따른다 — Neo4j 자격증명은
`api` 컨테이너에만 있고(이미 그 상태), `mcp` 컨테이너는 바꾸지 않는다.
기존 엔드포인트/MCP 툴은 전혀 수정하지 않는 순수 추가 작업이다.

입력은 4가지 앵커 타입(`failure_bucket`/`document`/`component`/
`symptom_text`)을 받는 **단일 툴**로 묶는다 — AI가 지금 무엇을 들고
있는지에 따라 타입만 바꿔 같은 툴을 호출하면 되므로, 4개로 쪼갰을 때
생기는 `kb_tools_help()` 중복 설명과 관리 비용을 피한다.

탐색 깊이는 **고정 2hop**이며, `is_hub=true`로 표시된 범용 Component는
순회 결과에서 기본 제외한다 — 1단계 실측(REPORT.md)에서 `SCP`
39,184건/`Network` 28,054건 같은 허브가 실제로 존재함이 확인됐고, 이걸
그대로 2hop 순회에 넣으면 의미 없는 결과가 쏟아진다(1단계 설계 §6에서
이미 예견한 문제).

---

## 1. 아키텍처

```
MCP client(AI) → kb_graph_explore(anchor_type, anchor_value)
              → MCP server (얇은 프록시, httpx — mcp-server/server.py)
              → POST /v1/graph/explore (신규, apps/api)
              → Neo4jClient.explore() (신규 읽기 전용 메서드) → Neo4j
              ← { as_of, anchor, documents[], components[], failure_buckets[],
                  excluded_hub_components[], truncated }
```

- `app/graph/neo4j_client.py`의 `Neo4jClient`에 읽기 전용 메서드
  `explore(anchor_type, anchor_value, max_hops=2)`를 추가한다. 기존
  `merge_document`/`merge_failure_bucket`/`ensure_constraints`(쓰기용)와는
  분리된 읽기 경로 — 같은 클래스에 두되 메서드만 구분한다.
- 응답의 `as_of`는 `graph_sync_state` 테이블에서 가장 최근
  `synced_at`(최대값)을 조회해 채운다 — 신선도는 날짜 한 줄로만 안내하고
  실시간 재검증은 요구하지 않는다(사용자 확정, 2026-10-09).
- 기존 `kb_search`/`kb_get_failure_bucket`/`kb_similar_incident` 등
  어떤 기존 엔드포인트·MCP 툴도 수정하지 않는다.

---

## 2. API 계약 + 입력 해석 로직

### 엔드포인트: `POST /v1/graph/explore`

**요청**:

```json
{
  "anchor_type": "failure_bucket",
  "anchor_value": "FB-12"
}
```

`max_hops`는 요청 바디에 받지 않는다 — 서버가 항상 2로 고정한다(허브
폭주 방지, 사용자 확정). 클라이언트가 바꿀 수 없다.

### `anchor_type`별 해석

| anchor_type | anchor_value | 해석 |
|---|---|---|
| `failure_bucket` | `FailureBucket.id` | 해당 노드에서 바로 2hop 순회 |
| `document` | `documents.id` 또는 `external_id` | `Document.id` MATCH 후 2hop 순회 |
| `component` | `Component.canonical_name` (대소문자 무시, `lexicon_terms`의 변형어도 허용 — 예: "넷앱"→`NetApp`, 기존 `app.lexicon.seed.load_lexicon_map()` 매핑 재사용) | 해당 `Component`에서 2hop 순회. **단, 이 컴포넌트 자신이 `is_hub=true`면 순회를 생략**하고 즉시 `{"excluded_hub_components": [...], "documents": [], ...}` + 안내 문구만 반환 — 토큰 낭비 방지 |
| `symptom_text` | 자유 텍스트 | 그래프 호출 **전에** 기존 `app.graph.extract.extract_lexicon_components()`를 재사용해 텍스트에서 컴포넌트를 먼저 추출 → 추출된 컴포넌트들(허브 제외)을 앵커 목록으로 2hop 순회(복수 앵커를 합쳐 중복 제거). 컴포넌트가 하나도 안 잡히면 빈 결과 + `"매칭된 컴포넌트 없음"` 메시지 |

### 응답

```json
{
  "as_of": "2026-10-09",
  "anchor": {"type": "failure_bucket", "resolved_id": "FB-12", "resolved_name": "..."},
  "documents": [
    {"id": "...", "title": "...", "source_type": "incident_reports",
     "evidence_grade": "A", "relation": "HAS_EVIDENCE", "hops": 1}
  ],
  "components": [
    {"canonical_name": "NetApp", "relation": "HAS_COMPONENT", "hops": 1, "tag": "EXTRACTED"}
  ],
  "failure_buckets": [
    {"id": "...", "title": "...", "relation": "SIMILAR_TO", "hops": 2}
  ],
  "excluded_hub_components": ["Network", "Storage"],
  "truncated": false
}
```

- `documents`/`components`/`failure_buckets` 각각 **최대 50건**으로
  cap — 넘으면 `truncated: true`. 비허브 컴포넌트 하나만 걸려도 수백 건이
  나올 수 있으므로 필수.
- 정렬: `hops` 오름차순 → `evidence_grade`(A>B>C) → 동순위는 임의.
- `anchor_value`가 해석 불가(존재하지 않는 id/이름)면 `404` + 그대로 에러
  메시지 반환 — MCP 프록시 쪽은 기존 `_api_error_detail()` 패턴으로 처리.

---

## 3. `is_hub` 구현

### 3.1 하드코딩 목록이 아니라 동적 집계

1단계 설계 §6은 13종(`Network`/`Storage`/`Cluster`/`Windows`/`Apache`/
`Firewall`/`HANA`/`Nginx`/`NetApp`/`OpenStack`/`SQL Server`/`ESXi`/
`VMware`)을 하드코딩 후보로 적어뒀지만, **1단계 실측(REPORT.md)으로
재확인한 결과 이 목록 자체가 이미 틀렸다** — 실측 상위권에
`SCP`(39,184건)와 `Kubernetes`(10,027건)가 있는데 둘 다 §6 목록에
없다. 즉 하드코딩 목록은 데이터가 바뀔 때마다(lexicon 확충, 신규
source_type 반입 등) 다시 틀어질 수 있다는 걸 이번에 실제로 증명한
셈이다.

따라서 **하드코딩 대신 백필 직후 degree 기반 동적 집계**로 간다:

```cypher
MATCH (c:Component)<-[:HAS_COMPONENT]-()
WITH c, count(*) AS degree
SET c.is_hub = (degree > 5000)
```

- `scripts/graph_sync.sh` 전체 백필 실행이 끝난 뒤 이 스텝을 1회
  추가 실행한다(`app.graph.pipeline`에 `recompute_hub_flags()` 함수로
  추가, CLI에 `--skip-hub-recompute` 플래그로 끌 수 있게 — 증분 실행마다
  매번 돌릴 필요는 없으므로 기본은 켜두되 선택적으로).
- 임계값 `5000`은 1단계 실측 분포(`SCP 39,184 / Network 28,054 /
  Storage 26,892 / OpenStack 20,655 / Cluster 14,705 / Kubernetes
  10,027 / Firewall 9,800 / VMware 9,605`)에서 상위 8종과 그 아래를
  가르는 지점으로 1차 설정한 것이며, **운영 전체 백필 완료 후 실측
  분포로 재검증**해야 한다(개발 미러와 운영 데이터가 완전히 같다는
  보장은 없음 — 1단계에서도 반복 확인된 원칙).
- 매 백필 실행마다 재계산되므로 "13종 목록"처럼 시간이 지나며 틀려지는
  문제가 구조적으로 없다.

### 3.2 테스트

- `extract_lexicon_components` 재사용 경로: 기존
  `test_graph_extract.py`에 `symptom_text` 입력 케이스 추가(새 추출
  로직이 아니라 기존 함수 재사용이므로 회귀 테스트 수준).
- `recompute_hub_flags()`: CI 경량 Neo4j에 degree 5건/5,001건 더미
  Component를 넣고 임계값 양쪽 경계(5000/5001) 테스트.
- `/v1/graph/explore` 통합 테스트: 4개 `anchor_type` ×
  (정상/허브-즉시반환/빈결과/404) 조합, `truncated` 경계(51건 입력 시
  50건 + `true`).
- MCP 툴: 기존 `kb_similar_incident` 테스트 패턴을 따라 httpx mock으로
  프록시 동작만 검증(핵심 로직은 API 쪽에 있어 MCP 단은 가벼움).

---

## 4. MCP 툴 정의 + 문서 반영

### 4.1 `mcp-server/server.py`

```python
@mcp.tool()
async def kb_graph_explore(
    anchor_type: str,   # "failure_bucket" | "document" | "component" | "symptom_text"
    anchor_value: str,
) -> str:
    """장애 분석 시 연관 컴포넌트·문서·과거 장애를 지식그래프에서 탐색한다.

    그래프는 하루 1회 배치로 갱신되므로 응답의 as_of 날짜보다 최신
    변경은 반영 안 돼 있을 수 있다 — 최종 확인은 kb_search/kb_get_document로.
    """
    ...
```

- 기존 `_client()`/`_err()`/`_api_error_detail()` 패턴을 그대로 써서
  `POST /v1/graph/explore`를 프록시한다.
- 결과는 JSON 그대로 던지지 않고, `kb_similar_incident`처럼 사람이 읽기
  좋은 텍스트로 렌더링한다.
- `excluded_hub_components`가 비어있지 않으면 응답 텍스트에
  `"제외된 범용 컴포넌트: Network, Storage"` 식으로 한 줄 명시 — AI가
  "관련 컴포넌트가 없다"고 잘못 판단하지 않도록.

### 4.2 `kb_tools_help()`

다른 툴들과 동일한 포맷으로 `kb_graph_explore` 항목을 추가한다 — 입력
4종 설명 + "그래프는 배치 갱신, 신선도 낮을 수 있음" 캐비엇 한 줄.

### 4.3 `docs/AI_AGENT_GUIDE.md`

"언제 `kb_graph_explore`를 쓰나" 절을 신설한다. 핵심 메시지: 이 도구는
`kb_match_failure_bucket`/`kb_similar_incident`로 1차 후보를 찾은
**다음 단계**다 — 검색의 대체재가 아니라 "찾은 것의 주변을 더 깊이
파는" 용도임을 명시한다. 배치 위치는 기존 evidence_grade 신뢰 모델
절(confluence_map의 C등급 설명이 있는 자리) 바로 아래 — 그래프 결과의
`evidence_grade` 필드도 같은 등급 체계를 그대로 쓰므로 문맥이 자연스럽게
이어진다.

### 4.4 스킬

이 기능 자체를 위한 신규 스킬은 만들지 않는다 — `kb_graph_explore`는
`AI_AGENT_GUIDE.md`의 기존 검색 흐름에 자연스럽게 편입되는 도구 하나일
뿐이다.

---

## 5. 영향 범위 / 안전성

- 순수 추가(additive): 기존 테이블·엔드포인트·MCP 툴·검색 경로 전혀
  수정 없음.
- 쓰기 경로 없음: 이 설계는 조회(read)만 다룬다. 그래프 데이터 갱신은
  여전히 `scripts/graph_sync.sh` 배치(현재 cron 미등록 — 별도 운영
  반영 절차는 `docs/KNOWLEDGE_GRAPH_PRODUCTION_ROLLOUT.md` 참고)뿐이다.
- Neo4j 장애 시: `/v1/graph/explore`만 503/502로 실패하고, 기존
  검색/API(`kb_search` 등)는 Neo4j와 무관하므로 영향받지 않는다 — 이
  엔드포인트 핸들러에서 Neo4j 연결 예외를 격리해서 잡아야 한다(1단계
  `sync_document`의 에러 격리 원칙과 동일하게, 조회 경로에도 적용).

---

## 6. 자체 검토(Self-Review)

- **플레이스홀더**: 없음 — 모든 섹션이 구체적 함수명/쿼리/임계값을
  명시.
- **내부 일관성**: §3.1의 임계값(5000)이 §0의 "허브 제외" 원칙 및 §2의
  `excluded_hub_components` 필드와 일치. §2의 `component` 앵커가
  허브일 때의 동작이 §4.1의 텍스트 안내와 일치.
- **범위 점검**: 단일 기능(조회 노출)에 집중돼 있고, §6 범위 밖 항목은
  명시적으로 분리해 뒀다 — 추가 분할 불필요.
- **모호성 점검**: `max_hops` 고정값(요청에서 변경 불가)과 `is_hub`
  동적 집계 방식(하드코딩 아님)을 명시적으로 확정해 두 해석 여지를
  없앴다.
