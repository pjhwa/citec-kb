# citec-kb 외부 연동 API

> 기준: [citec-wiki-qa](https://github.com/pjhwa/citec-wiki-qa) 외부 연동 표면  
> (MCP `wiki_search` / `wiki_get_document` / `wiki_list_synthesis` / `wiki_get_synthesis` / `wiki_ask`)

> **LLM/Claude 에이전트:** MCP 도구 선택·기간조회·집계 패턴은  
> [AI_AGENT_GUIDE.md](./AI_AGENT_GUIDE.md) · [MCP.md](./MCP.md) 를 우선 참고.

citec-kb는 자체 **`/v1/*`** API를 유지하면서, wiki-qa 클라이언트 마이그레이션을 위해  
**호환 경로 `/api/*`** 를 제공합니다.

| 용도 | 권장 |
|------|------|
| 신규 연동 | **`/v1/*`** (스키마·Trust·Planner 완전) |
| wiki-qa / MCP 기존 클라이언트 | **`/api/*`** 호환 레이어 |

기본 베이스: `http://localhost:8573`

---

## 1. wiki-qa 호환 (`/api/*`)

### Health / meta

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/health` | 경량 헬스 `{ok, ts, version}` |
| GET | `/api/version` | 버전·환경 |
| GET | `/api/wiki-stats` | 소스 타입별 문서 수 |
| GET | `/api/recent-questions` | 최근 질의 감사 로그 |

### 검색 · 문서

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/wiki/search?q=&section=&area=&limit=` | 하이브리드 검색 (wiki_search) |
| GET | `/api/wiki/search/facets` | section/area/category 목록 |
| GET | `/api/wiki/file?path=` | 본문 조회 (wiki_get_document) |

**section → source_type 매핑**

| wiki-qa section | citec-kb source_type |
|-----------------|----------------------|
| `checkitems` | `checkitem` |
| `support_history` | `support_history` |
| `incident_reports` | `support_history` |
| `tech_repo` | `tech_repo` |
| `tuning_ai` / `sql_tuning` | `tuning_ai` |
| `vendor_docs` | `vendor_docs` |
| `synthesis` | `insight` |
| `general` / 빈값 | (전체) |

**path 해석** (`/api/wiki/file`):  
`support_history/CITECTS-2502.md`, `CITECTS-2502`, document UUID, `source_uri` 부분 일치.

검색·질의 응답의 **모든 문서 hit** 에는 원문 접근 필드가 포함됩니다
(`app.doc_access.attach_document_access`):

| 필드 | 설명 |
|------|------|
| `path` | `source_type/external_id.md` |
| `body_api` | 상대 경로 `GET /v1/tickets/{eid}?source_type=` |
| `body_api_url` | 절대 URL (PUBLIC_API_BASE) |
| `body_api_file` / `_url` | `GET /api/wiki/file?path=` |
| `web_path` / `web_url` | 브라우저 `/doc.html?eid=&st=` (PUBLIC_WEB_BASE) |
| `access` | 위 필드 묶음 + `mcp_tool` / `mcp_args` |
| `mcp_tool` | 항상 `kb_get_document` |
| `mcp_args` | `{ "path": "…" }` |

```json
{
  "results": [
    {
      "path": "support_history/CITECTS-2502.md",
      "section": "support_history",
      "title": "…",
      "snippet": "…",
      "score": 0.42,
      "external_id": "CITECTS-2502",
      "body_api": "/v1/tickets/CITECTS-2502?source_type=support_history",
      "body_api_url": "http://localhost:8573/v1/tickets/CITECTS-2502?source_type=support_history",
      "web_url": "http://localhost:8572/doc.html?eid=CITECTS-2502&st=support_history&path=…",
      "access": { "mcp_tool": "kb_get_document", "mcp_args": { "path": "support_history/CITECTS-2502.md" } }
    }
  ],
  "total": 1,
  "fts_ready": true,
  "backend": "citec-kb"
}
```

적용 엔드포인트: `POST /v1/search` · `POST /v1/query` items · `POST /v1/chat` citations ·
`GET /api/wiki/search` · analytics samples · `GET /v1/tickets` 목록/상세.

### Q&A (SSE) — MCP `wiki_ask`

```http
POST /api/query
Content-Type: application/json

{"query": "모니모 Redis 타임아웃", "template": "support_history"}
```

SSE 이벤트 (wiki-qa 호환):

| type | 필드 | 설명 |
|------|------|------|
| `status` | `text` | 진행 메시지 |
| `sources` | `files[]` | 경로 목록 |
| `token` | `text` | 토큰 스트리밍 |
| `error` | `text` / `error` | 오류 |
| `done` | `result` | 최종 결과 (citec-kb trust·citations 포함) |

`stream: false` 이면 JSON 한 번에 반환.

`template`: `general` · `checkitems` · `support_history` · `tech_repo` · `tuning_ai` · `synthesis` …

### Synthesis ≈ Insight

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/synthesis?limit=&offset=` | Insight 목록 (slug=id) |
| GET | `/api/synthesis/{slug}` | Insight 상세 (`answer`=body_md) |

> wiki-qa의 파일 기반 synthesis 대신 **Insight 승인 플로우** 데이터를 노출합니다.

### Feedback

```http
POST /api/feedback
{"verdict": "helpful", "target_type": "answer", "target_id": "<query_or_answer_id>"}
```

또는 citec-kb 네이티브: `{"rating": 1, "target_type": "answer", "target_id": "…"}`  
verdict: `helpful` | `not_helpful` | `resolved` | `failed` | `edited`

---

## 2. 네이티브 외부 연동 (`/v1/*`)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/v1/health` | Redis/PG/LLM 포함 상세 헬스 |
| POST | `/v1/search` | 하이브리드 검색 (필터·multi_query) |
| POST | `/v1/chat` | Fast/Deep RAG (JSON 또는 SSE) |
| POST | `/v1/query` | 의도 분류 + 검색/집계/SI 등 |
| GET | `/v1/tickets/{external_id}` | 티켓 전체 본문 |
| GET | `/v1/analytics/tickets` | 기간·유형 집계 |
| POST | `/v1/similar-incident` | 유사장애 |
| GET | `/v1/insights` | Insight CRUD 계열 |
| GET | `/v1/external/catalog` | 이 문서의 기계 가독 카탈로그 |
| GET | `/v1/external/search` | 간단 GET 검색 |
| GET | `/v1/external/document?path=` | 문서 본문 |
| GET | `/v1/external/health` | `/api/health` 동일 |

OpenAPI: `http://localhost:8573/docs`

---

## 3. MCP 서버 (Claude 등)

citec-kb 전용 MCP 서버: **`mcp-server/`** · 문서 **`docs/MCP.md`**

```bash
docker compose up -d --build mcp   # http://localhost:8577
```

| MCP tool | REST |
|----------|------|
| `kb_search` / `wiki_search` | `GET /api/wiki/search` |
| `kb_get_document` / `wiki_get_document` | `GET /api/wiki/file` |
| `kb_list_insights` / `wiki_list_synthesis` | `GET /api/synthesis` |
| `kb_get_insight` / `wiki_get_synthesis` | `GET /api/synthesis/{slug}` |
| `kb_ask` / `wiki_ask` | `POST /api/query` SSE |
| `kb_query` | `POST /v1/query` |
| `kb_ticket` | `GET /v1/tickets/{id}` |

Claude Desktop 예시: `mcp-server/claude_desktop_config.example.json`

---

## 4. 인증

파일럿 기본 `AUTH_MODE=off` — Bearer 불필요.  
`apikey` / `oidc` 모드에서는 `/v1/*` 와 동일하게 게이트가 적용될 수 있습니다 (호환 `/api/*` 는 현재 공개 연동용으로 게이트 없음; 운영 시 리버스 프록시·API 키를 권장).

---

## 5. curl 스모크

```bash
curl -s http://localhost:8573/api/health | jq .
curl -s 'http://localhost:8573/api/wiki/search?q=Redis&section=support_history&limit=3' | jq .
curl -s 'http://localhost:8573/api/wiki/file?path=CITECTS-2502' | jq '.title,.path'
curl -s -N -X POST http://localhost:8573/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"Redis timeout","template":"support_history"}'
curl -s http://localhost:8573/v1/external/catalog | jq .
```
