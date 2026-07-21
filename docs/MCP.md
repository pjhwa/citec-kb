# citec-kb MCP 서버

Claude Desktop / Claude Code / Cursor 등 MCP 클라이언트에서  
CI-TEC 지식베이스(검색·문서·RAG·통합질의·티켓·Insight)를 tool로 사용합니다.

구조: **경량 프록시** → citec-kb REST (`/api/*`, `/v1/*`). 검색·LLM 로직은 API에 두고 MCP는 프로토콜 어댑터만 담당합니다.

---

## 도구 목록

| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_search` / `wiki_search` | 하이브리드 문서 검색 | `GET /api/wiki/search` |
| `kb_get_document` / `wiki_get_document` | 문서 본문 | `GET /api/wiki/file` |
| `kb_list_insights` / `wiki_list_synthesis` | Insight 목록 | `GET /api/synthesis` |
| `kb_get_insight` / `wiki_get_synthesis` | Insight 상세 | `GET /api/synthesis/{id}` |
| `kb_ask` / `wiki_ask` | RAG 답변 (SSE) | `POST /api/query` |
| `kb_query` | 통합 의도 질의 (집계·SI·목록…) | `POST /v1/query` |
| `kb_ticket` | 티켓 전체 본문 | `GET /v1/tickets/{id}` |
| `kb_health` | API 헬스 | `/api/health` + `/v1/health` |
| `kb_stats` | 코퍼스 통계 | `GET /api/wiki-stats` |

`wiki_*` 이름은 [citec-wiki-qa](https://github.com/pjhwa/citec-wiki-qa) MCP와 호환됩니다.

---

## Docker로 기동

호스트 포트 **8577** (할당 대역 8572–8580).

```bash
cd ~/dev/citec-kb
docker compose up -d --build mcp

# 스모크 (API 경유 로직 테스트)
CITEC_KB_BASE_URL=http://localhost:8573 python3 mcp-server/test_smoke.py
```

| 변수 | 기본 | 설명 |
|------|------|------|
| `CITEC_KB_BASE_URL` | `http://api:8000` (compose 내부) | API 베이스 |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8100` | 컨테이너 리스닝 |
| `MCP_TRANSPORT` | `streamable-http` | 또는 `stdio` |
| `CITEC_KB_TOKEN` | (빈값) | AUTH 켠 경우 Bearer |

---

## Claude Desktop 연동

### A. Streamable HTTP (Docker MCP, 권장)

1. `docker compose up -d mcp`
2. Claude Desktop 설정 → MCP → config 예시:

`mcp-server/claude_desktop_config.example.json`:

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

> 클라이언트 버전에 따라 URL 경로가 `/mcp` 또는 루트일 수 있습니다.  
> 연결 실패 시 `http://localhost:8577/mcp` / `http://localhost:8577` 를 번갈아 확인하세요.

### B. stdio (로컬 프로세스)

```bash
cd ~/dev/citec-kb/mcp-server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

`mcp-server/claude_desktop_stdio.example.json` 경로를 절대 경로로 수정:

```json
{
  "mcpServers": {
    "citec-kb": {
      "command": "/path/to/citec-kb/mcp-server/.venv/bin/python3",
      "args": ["/path/to/citec-kb/mcp-server/server.py"],
      "env": {
        "CITEC_KB_BASE_URL": "http://localhost:8573",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

### Claude Code

프로젝트 또는 사용자 MCP 설정에 동일 JSON을 등록:

```bash
# 예: streamable-http
claude mcp add --transport http citec-kb http://localhost:8577/mcp
```

(CLI 플래그는 Claude Code 버전에 따라 다를 수 있음)

---

## 사용 예 (에이전트 관점)

1. `kb_search("모니모 Redis timeout", section="support_history")`
2. 결과 path로 `kb_get_document("support_history/CITECTS-2502.md")`
3. 또는 한 번에 `kb_ask("모니모 Redis 타임아웃 원인과 조치", template="support_history")`
4. 집계: `kb_query("2026년에 지원한 기술지원의 유형을 알려줘")`
5. 티켓 상세: `kb_ticket("CITECTS-2502")`

---

## 관련 문서

- 외부 REST: `docs/EXTERNAL_API.md`
- 카탈로그 JSON: `GET http://localhost:8573/v1/external/catalog`
