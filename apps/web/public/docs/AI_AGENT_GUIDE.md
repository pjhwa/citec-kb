# citec-kb — AI Agent Guide (Claude / MCP / REST)

**Audience:** Claude Desktop, Claude Code, Cursor, and other LLM agents that call **MCP tools** or **HTTP APIs**.  
**Goal:** Choose the right tool, fetch evidence correctly, and answer with citations—not guesses.

Related docs:

| Doc | Focus |
|-----|--------|
| [MCP.md](./MCP.md) | MCP install, tool table, client config |
| [EXTERNAL_API.md](./EXTERNAL_API.md) | REST `/api/*` + `/v1/*` compatibility |
| [DEPLOY.md](./DEPLOY.md) | Air-gap packaging (ops, not for Q&A) |

---

## 1. What citec-kb is

CI-TEC internal knowledge platform:

- **Corpus:** support tickets (CITECTS-*), PISA checkitems, Confluence tech_repo, tuning notes, insights  
- **Search:** hybrid **PostgreSQL FTS + pgvector** (multilingual-e5-base, 768-d)  
- **Planner:** natural language → intent (time list, analytics, similar incident, checklist, capacity, hybrid search, RAG)  
- **LLM:** external only (OpenRouter / Fabrix)—not embedded in MCP  

**MCP server is a thin proxy.** It does not re-implement search; it calls the API and formats text for the model.

### Default ports (host)

| Port | Service |
|------|---------|
| **8572** | Web UI (nginx) |
| **8573** | API (FastAPI) |
| **8574** | Postgres |
| **8575** | Redis |
| **8577** | MCP (streamable-http) |

API base examples:

- From host: `http://localhost:8573`  
- From MCP container: `http://api:8000`  

Auth (pilot often **off**): if `AUTH_MODE=apikey|oidc`, send `Authorization: Bearer <token>` (MCP: env `CITEC_KB_TOKEN`).

---

## 2. Hard rules for agents (do this every time)

### 2.1 Evidence before claims

1. **Search or list first** (`kb_query` / `kb_search` / `kb_list_tickets` / …).  
2. For any document you cite, **load full text** with `kb_get_document(path=…)` or `kb_ticket(external_id=…)` when snippet is insufficient.  
3. Prefer answers grounded in returned titles, snippets, bodies, and **external_id** (e.g. `CITECTS-2386`).  
4. If retrieval is empty or weak: say you could not find it; suggest alternate queries—**do not invent tickets**.

### 2.2 Always use access fields

Every hit should expose (or MCP text will print):

| Field | Use |
|-------|-----|
| `path` | Pass to `kb_get_document` |
| `body_api` / `body_api_url` | Direct GET of ticket/checkitem JSON/body |
| `web_url` / `web_path` | Human-readable UI link |
| `external_id` | Ticket key / page id / checkitem code |
| `source_type` | `support_history`, `tech_repo`, `checkitem`, … |

When answering users, include at least: **title + external_id + path or web_url**.

### 2.3 Prefer the planner for vague NL

If the user speaks Korean natural language with **time, counts, types, “is there…?”, similar incidents, checklists**, start with:

```text
kb_query(q="<user question>")
```

Use specialized tools when you already know the structured parameters (dates, group_by, area).

### 2.4 Tool budget

| Situation | Approach |
|-----------|----------|
| One clear fact question | `kb_query` or `kb_search` → 1–2 `kb_get_document` |
| Period list | `kb_list_tickets` (not N full-text searches) |
| Counts / breakdown | `kb_analytics` (not LLM counting) |
| “Similar past outage” | `kb_similar_incident` |
| Full narrative answer | `kb_ask` after optional search |

Avoid: calling `kb_search` 10 times with tiny paraphrases when `kb_query` or analytics already answers.

---

## 3. Decision tree (which tool?)

```
User question
│
├─ Health / corpus size? ──────────────► kb_health / kb_stats
│
├─ Natural language, unclear type? ────► kb_query(q)
│     (period, counts, SI, checklist, capacity, search)
│
├─ Explicit period list
│     "지난 주", "올해", "2026-01-01~03-31"
│     ──────────────────────────────────► kb_list_tickets
│
├─ Explicit aggregates
│     "연도별", "유형별", "월별 건수", "SCP 비중"
│     ──────────────────────────────────► kb_analytics
│                                         kb_entity_share
│                                         kb_title_tokens
│
├─ Keyword / topical document find
│     "Multi-AZ 가용성", "Redis timeout"
│     ──────────────────────────────────► kb_search
│                                         then kb_get_document
│
├─ RAG prose answer with citations ────► kb_ask
│
├─ Similar incident / past case ───────► kb_similar_incident
│
├─ PISA checklist ─────────────────────► kb_list_checkitems
│                                         kb_get_checkitem
│
├─ Known ticket id CITECTS-#### ───────► kb_ticket
│
├─ Capacity / 공수 estimate ───────────► kb_capacity_estimate
│
└─ Unsure which tool ──────────────────► kb_tools_help
```

---

## 4. MCP tool reference (complete)

Base MCP: FastMCP over **streamable-http** (`http://localhost:8577/mcp`) or **stdio**.

### 4.1 `kb_query` — unified planner (recommended entry)

| | |
|--|--|
| **When** | User NL; time + topic; “있나?”; type breakdown; similar cases mixed with search |
| **Args** | `q` (string), `top_k` (default 10) |
| **API** | `POST /v1/query` `{"q", "include_search": true, "top_k"}` |

**Intents you may see in the response:**

| intent | Meaning | Follow-up |
|--------|---------|-----------|
| `time_scoped_list` | Tickets in a date range | Open top tickets with `kb_ticket` / path |
| `analytics` | Counts by year/month/component/issue_type/… | Cite buckets; sample paths |
| `hybrid_search` | Ranked documents | `kb_get_document` on path |
| `similar_incident` | SI cases | Ticket bodies if needed |
| `checklist` | PISA items | `kb_get_checkitem` |
| `capacity` | Staffing/cost rules | Numbers only from result |
| `exhaustive` / `prevention` | Broader / prevention packs | Paths as given |
| `entity_aggregate` | Entity-oriented buckets | Same as analytics samples |

**Example `q` values that work well:**

- `지난 주 지원건`
- `올해 SCP 관련 유형 분류`
- `2026년 지원 건수 연도별` / `연도별 지원 건수`
- `2026년 SCP v2 Multi-AZ 가용성 테스트가 있는가?`
- `모니모 Redis 타임아웃 유사 장애`
- `Linux OOM 체크리스트`

### 4.2 `kb_search` / `wiki_search`

| | |
|--|--|
| **When** | Topical hybrid search with optional filters |
| **API** | Default `POST /v1/search` (`use_v1=true`) |

| Arg | Type | Description |
|-----|------|-------------|
| `query` | str | Search string |
| `section` | str | → `source_type`: `support_history`, `tech_repo`, `tuning_ai`, `checkitems`, `confluence_docs`, … |
| `area` | str | → domain filter (`os`, `dbms`, `network`, `cloud`, `storage`, …) |
| `category` | str | wiki-compat only when `use_v1=false` |
| `limit` | int | 1–50 |
| `environment` | str | e.g. `csp` |
| `work_type` | str | e.g. 기술지원 / 장애지원 |
| `multi_query` | bool | Expand synonyms/phrases (default true) |
| `use_v1` | bool | false → `GET /api/wiki/search` |

**Tips:**

- Existence questions: prefer full NL in `query` (and/or `kb_query`); avoid only `SCP` or only `Multi-AZ`.  
- After hits: always use printed `path` for `kb_get_document`.

### 4.3 `kb_get_document` / `wiki_get_document`

| Arg | Description |
|-----|-------------|
| `path` | From search: `support_history/CITECTS-2386.md`, or short `CITECTS-2386` |

Returns markdown body + access meta. **Primary way to quote full text.**

### 4.4 `kb_list_tickets` — period listing

| Arg | Description |
|-----|-------------|
| `relative` | See §5 time expressions |
| `date_from` / `date_to` | ISO `YYYY-MM-DD` |
| `date_field` | `Created` (default), `Resolved`, `Updated` |
| `source_type` | default `support_history` |
| `limit` / `offset` / `order` | pagination |

**API:** `GET /v1/tickets?...`

Use this instead of inventing lists from memory. Combine with `kb_ticket` for full body.

### 4.5 `kb_analytics`

| Arg | Description |
|-----|-------------|
| `group_by` | `year` \| `month` \| `component` \| `issue_type` \| `status` \| `assignee` \| `total` |
| `relative` / `date_from` / `date_to` / `date_field` | time scope |
| `source_type` | default `support_history` |
| `component` | Jira component filter |
| `entity` | title ILIKE (e.g. `SCP`, `모니모`) |
| `top_k` | max buckets |

**API:** `GET /v1/analytics/tickets`  
**Never** invent counts—only report returned buckets.

### 4.6 `kb_entity_share`

Share of tickets matching `entity` in a period.  
**API:** `GET /v1/analytics/entity_share`

### 4.7 `kb_title_tokens`

Title token frequency (optional `component`).  
**API:** `GET /v1/analytics/title_tokens`

### 4.8 `kb_similar_incident`

| Arg | Description |
|-----|-------------|
| `symptom` | Free-text symptom (required) |
| `environment` / `product` / `service` | optional context |
| `top_k` | 1–10 |

**API:** `POST /v1/similar-incident`  
Report applicability labels; load ticket body for resolution details.

### 4.9 Checklist

- `kb_list_checkitems(q=, area=, category_1=, limit=)` → `GET /v1/checkitems`  
- `kb_get_checkitem(code=)` → `GET /v1/checkitems/{code}`  

`area` examples: `Linux`, `Oracle`, `Windows`.  
`code` examples: `PISAOLNX_01.04.05`.

### 4.10 `kb_capacity_estimate`

Rule-based capacity/pricing (no LLM).  
**API:** `POST /v1/capacity/estimate`  
Args: `period_days`, `basis` (e.g. `1안`), `include_pricing`.

### 4.11 `kb_ticket`

Full ticket markdown.  
**API:** `GET /v1/tickets/{external_id}?source_type=support_history`

### 4.12 Insights

- `kb_list_insights` / `wiki_list_synthesis`  
- `kb_get_insight` / `wiki_get_synthesis`  

### 4.13 `kb_ask` / `wiki_ask`

RAG answer (SSE under the hood).  
Args: `query`, `template` (default `general`), `mode` (`fast`|`deep`).  
Use when the user wants a **written answer with citations**, not only a list.  
Still verify important claims with `kb_get_document` if the model output looks thin.

### 4.14 Ops

- `kb_health` — API health  
- `kb_stats` — document counts by source  
- `kb_tools_help` — short tool menu (also useful mid-conversation)

---

## 5. Time expressions (`relative`)

Supported via `parse_relative_range` (Korean phrases). Common values:

| relative | Meaning (approx.) |
|----------|-------------------|
| `지난 주` / `최근 7일` | last week / last 7 days |
| `이번 달` / `지난 달` | this / last month |
| `올해` / `작년` | this / last calendar year |
| `최근 30일` | last 30 days |

Also works: ISO `date_from` + `date_to` on `kb_list_tickets` / `kb_analytics`.

If `relative` is unrecognized, API returns 400—retry with ISO dates or different phrasing, or use `kb_query` with full Korean sentence.

---

## 6. Source types & path conventions

| source_type / section | Content | path pattern |
|----------------------|---------|--------------|
| `support_history` | Jira-like tickets | `support_history/CITECTS-2386.md` |
| `tech_repo` | Confluence tech pages | `tech_repo/{pageId}.md` |
| `tuning_ai` | Tuning / SQL notes | `tuning_ai/...` |
| `checkitem` / section `checkitems` | PISA items | use `kb_get_checkitem` or path form |
| `confluence_docs` | Other confluence | `confluence_docs/...` |
| insights / synthesis | Approved insights | via insight tools |

**Ticket keys:** always `CITECTS-<number>` (case-insensitive in search; normalize to `CITECTS-####` when calling `kb_ticket`).

---

## 7. REST quick reference (if not using MCP)

Base: `http://localhost:8573`  
Headers: `Accept: application/json`, optional `Authorization: Bearer …`

### 7.1 Search & RAG

```http
POST /v1/search
Content-Type: application/json

{
  "q": "SCP v2 Multi-AZ 가용성 테스트",
  "top_k": 10,
  "filters": {
    "source_type": "support_history",
    "status": "active"
  },
  "multi_query": true
}
```

```http
POST /v1/chat
{"q": "…", "mode": "fast", "top_k": 8}
```

```http
POST /v1/query
{"q": "지난 주 지원건", "include_search": true, "top_k": 10}
```

```http
POST /v1/similar-incident
{"symptom": "Redis timeout after deploy", "top_k": 3}
```

### 7.2 Tickets & time

```http
GET /v1/tickets?relative=지난%20주&limit=30&date_field=Created
GET /v1/tickets?date_from=2026-01-01&date_to=2026-03-31
GET /v1/tickets/CITECTS-2386?source_type=support_history
```

### 7.3 Analytics

```http
GET /v1/analytics/tickets?group_by=year
GET /v1/analytics/tickets?group_by=issue_type&relative=올해&entity=SCP
GET /v1/analytics/entity_share?entity=SCP&relative=올해
GET /v1/analytics/title_tokens?component=장애지원&top_k=20
```

### 7.4 Checkitems & capacity

```http
GET /v1/checkitems?q=OOM&area=Linux&limit=30
GET /v1/checkitems/PISAOLNX_01.04.05
POST /v1/capacity/estimate
{"period_days": 7, "basis": "1안", "include_pricing": true}
```

### 7.5 wiki-qa compatible (`/api/*`)

```http
GET /api/health
GET /api/wiki-stats
GET /api/wiki/search?q=Redis&section=support_history&limit=10
GET /api/wiki/file?path=support_history/CITECTS-2502.md
POST /api/query
{"q": "…", "stream": true}
```

Full field tables: [EXTERNAL_API.md](./EXTERNAL_API.md).

---

## 8. Worked scenarios (multi-step)

### Scenario A — “2026년 SCP v2 Multi-AZ 가용성 테스트가 있는가?”

1. `kb_query("2026년 SCP v2 Multi-AZ 가용성 테스트가 있는가?")`  
   **or** `kb_search(query=..., section="support_history")`  
2. Expect strong hit **CITECTS-2386** (그룹26-5 성능/가용성 테스트).  
3. `kb_get_document(path="support_history/CITECTS-2386.md")`  
4. Answer: yes/no + schedule + target (SCP v2 Multi-AZ) + link/path.  
5. Do **not** stop at old Multi-AZ network tickets (e.g. CITECTS-282) if 2386 ranks and matches.

### Scenario B — “지난 주 지원 목록 요약”

1. `kb_list_tickets(relative="지난 주", limit=50)`  
2. Optionally group by component in your reasoning (or `kb_analytics` with same relative).  
3. For 2–3 important tickets: `kb_ticket("CITECTS-…")`.  
4. Summarize with ids and dates; offer deep dive.

### Scenario C — “올해 SCP 이슈 유형 비중”

1. `kb_analytics(group_by="issue_type", relative="올해", entity="SCP")`  
2. Report bucket keys and counts only from response.  
3. Optional samples: open path via `kb_get_document`.

### Scenario D — “Redis timeout 과거 유사 장애와 조치”

1. `kb_similar_incident(symptom="Redis timeout …", product="모니모")`  
2. For top case: `kb_ticket(external_id)`  
3. Structure answer: symptom match → root cause → resolution → applicability.

### Scenario E — “Linux OOM 관련 점검 항목”

1. `kb_list_checkitems(q="OOM", area="Linux")`  
2. `kb_get_checkitem(code="…")` for full structured sections (including 참고).  
3. Present checklist-style steps from structured fields.

### Scenario F — RAG narrative

1. Optional: `kb_search` to pre-check corpus.  
2. `kb_ask(query="…", mode="fast")`  
3. If answer abstains or looks weak: fall back to search + document read and answer yourself with citations.

---

## 9. Anti-patterns (avoid)

| Anti-pattern | Why | Do instead |
|--------------|-----|------------|
| Answer from model memory only | Hallucinated CITECTS ids | Always tool call |
| Count tickets with LLM | Wrong numbers | `kb_analytics` |
| Search only `SCP` or only `2026` | High-DF noise | Multi-token query / `kb_query` |
| List paths but never open body | Thin answers | `kb_get_document` / `kb_ticket` |
| Ignore `intent=` from `kb_query` | Wrong follow-up | Branch on intent |
| Use `kb_ask` for pure “list last week” | Overkill / weaker lists | `kb_list_tickets` |
| Assume English-only | Corpus is KO+EN | Keep Korean query text |

---

## 10. Output format suggested for user-facing answers

```markdown
## 결론
- …

## 근거
1. **CITECTS-####** — 제목  
   - 요약 (본문 기준 1–3문장)  
   - path: `support_history/….md`  
   - (선택) web_url

## 추가 확인
- 더 볼 티켓 / 기간 재조회 제안
```

If no evidence:

```markdown
## 결론
코퍼스에서 확인하지 못했습니다.

## 시도한 조회
- tools + queries used

## 제안
- 다른 키워드 / 기간 / source_type
```

---

## 11. Reliability notes (retrieval)

- Hybrid search uses **multi-query expansion** carefully: bare high-DF tokens (`SCP`, `Multi-AZ`, year alone) are down-weighted; prefer full phrases.  
- Planner may route “있나?” questions to hybrid search—still open the top document before asserting.  
- Vectors offline: ops must ship model + embeddings (`--pg-only` / full data); empty embeddings → FTS-only or weak vector path.  
- `AUTH_MODE=off` pilot: tools work without token; production may require Bearer.

---

## 12. MCP client setup (short)

**Streamable HTTP (Docker MCP on 8577):**

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

**stdio:** see `mcp-server/claude_desktop_stdio.example.json` with `CITEC_KB_BASE_URL=http://localhost:8573`.

After code deploy: `docker compose restart mcp` (server.py is bind-mounted).

---

## 13. Quick checklist for the agent (before final answer)

- [ ] Correct tool chosen (tree in §3)  
- [ ] At least one retrieval call succeeded  
- [ ] Existence/count/list claims match tool output  
- [ ] Citations include external_id and path or web_url  
- [ ] Full body loaded when quoting resolution/cause  
- [ ] Uncertainties stated if rank is weak or conflicting  

---

## 14. Versioning

This guide tracks MCP tools as of **kb_list_tickets / kb_analytics / kb_similar_incident / kb_list_checkitems / kb_capacity_estimate / kb_tools_help** and `kb_search` → `/v1/search`.  
If a tool name is missing in the live server, call `kb_tools_help` or fall back to `kb_query` + REST in [EXTERNAL_API.md](./EXTERNAL_API.md).
