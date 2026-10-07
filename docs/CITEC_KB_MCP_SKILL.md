# citec-kb MCP 검색 스킬

```yaml
name: citec-kb-search
description: >
  citec-kb를 MCP(kb_* 도구)로 검색/조회/분석할 때 반드시 적용한다.
  트리거: 사용자가 CI-TEC 지원이력/장애/체크리스트/Confluence/튜닝노트/
  반복장애 도메인 등 citec-kb가 다루는 주제에 대해 묻거나, citec-kb MCP
  서버가 연결돼 있는 모든 대화.
```

**이 문서의 역할**: `AI_AGENT_GUIDE.md`(도구별 상세 레퍼런스)를 전부 읽을
여유가 없을 때도 최소한 이 문서의 지시만 따르면 사고를 피할 수 있도록
압축한 **행동 지시문**이다. 상세 근거/표/시나리오는 항상
[AI_AGENT_GUIDE.md](./AI_AGENT_GUIDE.md)를 참고하라 — 이 문서와 내용이
어긋나면 AI_AGENT_GUIDE.md가 더 최신/정본이다.

---

## 0. 연결 확인 (대화 시작 시 1회)

citec-kb MCP가 연결돼 있는지 불확실하면 `kb_health()`로 먼저 확인하고,
어떤 도구가 있는지 모르면 `kb_tools_help()`를 호출하라. 추측하지 말 것.

---

## 1. 절대 규칙 (매번 지킬 것)

1. **먼저 조회하고 나중에 답하라.** 기억이나 추측으로 CITECTS 티켓 번호,
   장애 건수, 체크리스트 코드를 만들어내지 않는다. 검색/조회 결과가
   없으면 "코퍼스에서 확인하지 못했습니다"라고 말한다.
2. **인용 전엔 전체 본문을 읽어라.** 검색 snippet만 보고 결론 내리지
   말고, 중요한 인용은 `kb_get_document`/`kb_ticket`/`kb_get_checkitem`으로
   전체 텍스트를 가져온 뒤 말한다.
3. **숫자는 직접 세지 말고 집계 도구를 써라.** 건수/기간/비중 질문은
   `kb_analytics`/`kb_entity_share`/`kb_list_tickets`/
   `kb_citec_recurring_patterns`로만 답한다 — 검색 결과를 눈으로 세서
   답하지 않는다.
4. **모호한 한국어 자연어 질문은 `kb_query(q=...)`부터** 시도한다 (기간·
   집계·유사장애·체크리스트·검색을 자동 분기). 구조화된 파라미터(날짜,
   group_by 등)를 이미 알 때만 전용 도구로 바로 간다.
5. **`evidence_grade`를 확인하고 신뢰도를 명시하라** (§2).
6. **답변엔 항상 근거를 남긴다**: 제목 + `external_id`(또는 page_id) +
   `path`/`web_url`. 근거 없는 결론은 금지.

---

## 2. 신뢰도 규칙 — `evidence_grade` (반드시 확인)

| grade | 의미 | 행동 |
|---|---|---|
| **A / A-** | 본문 그대로 사실 | 그대로 인용 가능 |
| **B** | 아직 미종결(열린 티켓 등) | "아직 진행 중"임을 밝히고 인용 |
| **C** (`confluence_map` 전용) | 자동 크롤, 검증/최신성 보장 없음 — **본문이 있어도** 정책상 C 유지 | 같은 page_id가 `tech_repo`/`confluence_docs`에도 있으면 그걸 우선 사용; 없으면 답은 하되 "미검증 색인" 임을 밝힘; 본문이 제목/경로뿐이면 2026-10 이전 데이터라 실제 페이지를 열어 확인 |
| **draft** | 검토 전 Insight 등 | 확정 사실로 제시하지 말 것 |

**가장 자주 하는 실수**: `confluence_map` 결과를 `tech_repo`/
`confluence_docs`보다 우선시키는 것. **항상 후자를 우선한다.**

---

## 3. 도구 선택 — 1초 결정표

| 질문 유형 | 도구 |
|---|---|
| 모호한 한국어 질문 (기간/건수/있나?/유사/체크리스트 섞임) | `kb_query` |
| 기간 목록 ("지난 주 지원건") | `kb_list_tickets` |
| 집계/비중 ("연도별", "SCP 비중") | `kb_analytics` / `kb_entity_share` / `kb_title_tokens` |
| 주제/키워드 문서 검색 | `kb_search` → `kb_get_document` |
| 서술형 답변(인용 포함) | `kb_ask` |
| 과거 유사 장애 | `kb_similar_incident` |
| PISA 체크리스트 | `kb_list_checkitems` / `kb_get_checkitem` |
| 특정 티켓 CITECTS-#### | `kb_ticket` |
| 공수/비용 추정 | `kb_capacity_estimate` |
| CI-TEC 11개 도메인 반복장애/커버리지 갭 | `kb_citec_domain_catalog`(먼저) → `kb_citec_recurring_patterns` / `kb_citec_failure_bucket_coverage` |
| 진단 패턴(네트워크/클러스터 등) 매칭·등록 | `kb_match_failure_bucket` → 없으면 `kb_register_failure_bucket`, 맞았으면 `kb_refine_failure_bucket(confirm=True)` |
| 뭘 써야 할지 모름 | `kb_tools_help` |

---

## 4. 금지 패턴

- 모델 기억만으로 CITECTS 번호/날짜/건수 답하기
- `SCP`, `2026`처럼 단일 고빈도 토큰으로만 검색하기 (잡음 多) — 전체 구문 또는 `kb_query` 사용
- `confluence_map` 결과를 검증 없이 확정 사실로 제시하기
- 검색 결과 목록을 눈으로 세어 통계처럼 말하기
- `kb_query`의 `intent` 필드를 무시하고 엉뚱한 후속 도구로 가기
- CI-TEC 도메인명을 추측해서 쓰기(`kb_citec_domain_catalog()`로 정확한 철자 확인 먼저)

---

## 5. 답변 형식 (권장)

```markdown
## 결론
- …

## 근거
1. **CITECTS-#### / page_id** — 제목
   - 1~3문장 요약 (본문 기준)
   - path 또는 web_url
   - (confluence_map 근거면) "C등급, 미검증" 명시

## 추가 확인
- 더 볼 티켓/기간/도메인 제안
```

---

## 6. 더 깊이 볼 때

- 도구별 전체 파라미터/REST 매핑/워크드 시나리오: [AI_AGENT_GUIDE.md](./AI_AGENT_GUIDE.md)
- failure_bucket 플러그인 개발: [FAILURE_BUCKET_PLUGIN_GUIDE.md](./FAILURE_BUCKET_PLUGIN_GUIDE.md)
- Confluence draw.io 다이어그램: [CONFLUENCE_DRAWIO_MCP_GUIDE.md](./CONFLUENCE_DRAWIO_MCP_GUIDE.md)
- 패킷분석 self-improvement 루프 예시: [PACKET_ANALYSIS_MCP_GUIDE.md](./PACKET_ANALYSIS_MCP_GUIDE.md)
- 서버에서 바로 보는 축약판: MCP 도구 `kb_tools_help()`

---

## 7. 버전

2026-10-08 최초 작성. `AI_AGENT_GUIDE.md`의 2026-10-07/08 신뢰도 모델
개정(§6.1~6.3)과 동기화된 상태. `AI_AGENT_GUIDE.md`가 바뀌면 이 문서의
§2~4도 같이 확인할 것 — 둘이 어긋나면 혼란의 원인이 된다.
