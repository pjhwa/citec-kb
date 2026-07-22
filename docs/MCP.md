# citec-kb MCP 서버

Claude Desktop / Claude Code / Cursor 등 MCP 클라이언트에서  
CI-TEC 지식베이스(검색·기간조회·집계·문서·RAG·통합질의·티켓·Insight)를 tool로 사용합니다.

구조: **경량 프록시** → citec-kb REST (`/api/*`, `/v1/*`). 검색·LLM 로직은 API에 두고 MCP는 프로토콜 어댑터만 담당합니다.

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

### Insight · 상태

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_list_insights` / `wiki_list_synthesis` | Insight 목록 | `GET /api/synthesis` |
| `kb_get_insight` / `wiki_get_synthesis` | Insight 상세 | `GET /api/synthesis/{id}` |
| `kb_health` | API 헬스 | `/api/health` + `/v1/health` |
| `kb_stats` | 코퍼스 통계 | `GET /api/wiki-stats` |

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
