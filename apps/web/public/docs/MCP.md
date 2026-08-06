# citec-kb MCP 서버

Claude Desktop / Claude Code / Cursor 등 MCP 클라이언트에서  
CI-TEC 지식베이스(검색·기간조회·집계·문서·RAG·통합질의·티켓·Insight)를 tool로 사용합니다.

구조: **경량 프록시** → citec-kb REST (`/api/*`, `/v1/*`). 검색·LLM 로직은 API에 두고 MCP는 프로토콜 어댑터만 담당합니다.

> **AI 에이전트(Claude 등)용 상세 가이드:** [AI_AGENT_GUIDE.md](./AI_AGENT_GUIDE.md)  
> 도구 선택 트리 · 파라미터 · 시나리오 · 안티패턴 · REST 요약

---

## 도구 목록

### 자연어 통합 (권장 엔트리)

| Tool | 설명 | 백엔드 |
|------|------|--------|
| **`kb_query`** | 의도 자동 분기: 기간 목록·집계·유사장애·체크리스트·용량·검색 | `POST /v1/query` |
| `kb_tools_help` | 도구 선택 가이드 | (로컬) |

### 문서 검색 · 원문

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_search` / `wiki_search` | 하이브리드 FTS+vector (section/area/environment/work_type) | `POST /v1/search` (기본) |
| `kb_get_document` / `wiki_get_document` | 문서 본문 | `GET /api/wiki/file` |
| `kb_ask` / `wiki_ask` | RAG 답변 (SSE) | `POST /api/query` |

### 기간 조회 · 티켓 목록

| Tool | 설명 | 백엔드 |
|------|------|--------|
| **`kb_list_tickets`** | `relative` / `date_from`·`date_to` 기간 목록 | `GET /v1/tickets` |
| `kb_ticket` | 티켓 전체 본문 | `GET /v1/tickets/{id}` |

### 집계 · 분석

| Tool | 설명 | 백엔드 |
|------|------|--------|
| **`kb_analytics`** | year/month/component/issue_type 등 그룹 집계 | `GET /v1/analytics/tickets` |
| `kb_entity_share` | 키워드 점유율 | `GET /v1/analytics/entity_share` |
| `kb_title_tokens` | 제목 토큰 빈도 | `GET /v1/analytics/title_tokens` |

### 유사장애 · 체크리스트 · 용량

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_similar_incident` | 증상 기반 SI | `POST /v1/similar-incident` |
| `kb_list_checkitems` | PISA 항목 검색 | `GET /v1/checkitems` |
| `kb_get_checkitem` | PISA 항목 상세 | `GET /v1/checkitems/{code}` |
| `kb_capacity_estimate` | 공수/용량 규칙 추정 | `POST /v1/capacity/estimate` |

### 실패 버킷 (장애 패턴, 다중 플러그인 — network/cluster/windows 등)

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_register_failure_bucket` | 실패 버킷(장애 패턴) 등록 — 판별 신호 포함. `fb_domain`/`evidence_ref` 필수, `source_plugin` 권장 | `POST /v1/failure-buckets` |
| `kb_refine_failure_bucket` | 신호 추가/확인/반박, 신뢰도 자동 재계산 | `POST /v1/failure-buckets/{id}/refine` |
| `kb_match_failure_bucket` | 관찰 신호로 후보 버킷 순위화. `fb_domain` 선택 필터 | `POST /v1/failure-buckets/match` |
| `kb_list_failure_buckets` | 등록된 버킷 목록. `fb_domain` 선택 필터 | `GET /v1/failure-buckets` |
| `kb_get_failure_bucket` | 버킷 상세(신호/원인/조치) | `GET /v1/failure-buckets/{id}` |

`fb_domain` 값 목록은 [failure-bucket-domains.md](../references/failure-bucket-domains.md) 참고.
플러그인 개발자용 상세 지침은 [FAILURE_BUCKET_PLUGIN_GUIDE.md](./FAILURE_BUCKET_PLUGIN_GUIDE.md).

### Insight · 상태

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_list_insights` / `wiki_list_synthesis` | Insight 목록 | `GET /api/synthesis` |
| `kb_get_insight` / `wiki_get_synthesis` | Insight 상세 | `GET /api/synthesis/{id}` |
| `kb_health` | API 헬스 | `/api/health` + `/v1/health` |
| `kb_stats` | 코퍼스 통계 | `GET /api/wiki-stats` |

### Confluence draw.io 다이어그램

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_confluence_find_pages` | 공간(space)명으로 페이지 검색해 page_id 조회 (CQL) — 공간명이 바뀌어도 매번 인자로 지정 | `GET /v1/confluence/spaces/{space_key}/pages` |
| `kb_confluence_list_diagrams` | 페이지의 draw.io 다이어그램 목록(매크로+첨부) | `GET /v1/confluence/pages/{id}/diagrams` |
| `kb_confluence_get_diagram` | 다이어그램 원본 XML 조회 | `GET /v1/confluence/pages/{id}/diagrams/{name}` |
| `kb_confluence_put_diagram` | 다이어그램 XML 업로드/갱신 | `PUT /v1/confluence/pages/{id}/diagrams/{name}` |

page_id를 모를 때는 먼저 `kb_confluence_find_pages(space_key=...)`로 페이지를 찾은 뒤 그 `page_id`로 나머지 도구를 사용하세요.

> **draw.io 다이어그램 읽기/쓰기 상세 가이드:** [CONFLUENCE_DRAWIO_MCP_GUIDE.md](./CONFLUENCE_DRAWIO_MCP_GUIDE.md)
> 워크플로 · mxGraph XML 구조 · 오류 처리 · 안티패턴

`wiki_*` 이름은 [citec-wiki-qa](https://github.com/pjhwa/citec-wiki-qa) MCP와 호환됩니다.

---

## 사용 예 (에이전트)

| 사용자 의도 | 도구 |
|-------------|------|
| “지난 주 지원건 목록” | `kb_list_tickets(relative="지난 주")` 또는 `kb_query("지난 주 지원건")` |
| “2026년 1~3월 장애지원” | `kb_list_tickets(date_from="2026-01-01", date_to="2026-03-31")` + search filter |
| “올해 SCP 유형 분류” | `kb_analytics(group_by="issue_type", relative="올해", entity="SCP")` |
| “연도별 건수” | `kb_analytics(group_by="year")` 또는 `kb_query("연도별 지원 건수")` |
| “Multi-AZ 가용성 테스트 있나” | `kb_search` / `kb_query` / `kb_ask` |
| “Redis timeout 유사 장애” | `kb_similar_incident(symptom="...")` |
| “Linux OOM 체크리스트” | `kb_list_checkitems(q="OOM", area="Linux")` |
| 원문 인용 | 결과의 `path` → `kb_get_document` |
| "LB idle-timeout RST 패턴 등록해줘" | `kb_register_failure_bucket(bucket_name=..., discriminating_signals=[...], fb_domain="network", evidence_ref=...)` |
| "이 RST idle 62초 신호로 어떤 장애 패턴이 유력해?" | `kb_match_failure_bucket(observed_signals=["RST 직전 idle 62초"])` |

에이전트 규칙: 목록만 나열하지 말고 필요 시 `kb_get_document` / `kb_ticket` 으로 원문을 가져와 인용하세요.

---

## Docker로 기동

호스트 포트 **8577** (할당 대역 8572–8580).

```bash
cd ~/dev/citec-kb
docker compose up -d mcp
# server.py 는 호스트 마운트 — code 배포 후 compose restart mcp

CITEC_KB_BASE_URL=http://localhost:8573 python3 mcp-server/test_smoke.py
```

| 변수 | 기본 | 설명 |
|------|------|------|
| `CITEC_KB_BASE_URL` | `http://api:8000` (compose 내부) | API 베이스 |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8100` | 컨테이너 리스닝 |
| `MCP_TRANSPORT` | `streamable-http` | 또는 `stdio` |
| `CITEC_KB_TOKEN` | (빈값) | AUTH 켠 경우 Bearer |
| `CONFLUENCE_BASE_URL` | (빈값, API 서버 설정) | Confluence 베이스 URL — 미설정 시 `kb_confluence_*` 도구는 503 오류 반환 |
| `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` | (빈값, API 서버 설정) | Confluence Basic Auth 자격 증명 (PAT 아님) |

`CONFLUENCE_BASE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_PASSWORD`는 **API 서버**(`apps/api`) 쪽에 설정합니다 — MCP 컨테이너가 아닙니다. MCP 도구는 그 설정이 없을 때 503을 그대로 전달할 뿐입니다.

---

## Claude Desktop / Claude Code

Streamable HTTP:

```json
{
  "mcpServers": {
    "citec-kb": {
      "url": "http://localhost:8577/mcp",
      "transport": "streamable-http"
    }
  }
}
```

stdio 예시는 `mcp-server/claude_desktop_stdio.example.json` 참고.

```bash
claude mcp add --transport http citec-kb http://localhost:8577/mcp
```
