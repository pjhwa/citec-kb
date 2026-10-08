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
| `source_type` | `support_history`, `incident_reports`, `tech_repo`, `checkitem`, … |

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
├─ CI-TEC 도메인별 반복 장애 / 커버리지 갭
│     "어느 도메인에서 반복 장애가 많은가", "failure_bucket 정리 안 된 도메인"
│     ──────────────────────────────────► kb_citec_domain_catalog (먼저)
│                                         kb_citec_recurring_patterns
│                                         kb_citec_failure_bucket_coverage
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
| `section` | str | → `source_type`: `support_history`, `incident_reports`, `tech_repo`, `tuning_ai`, `checkitems`, `confluence_docs`, `dept_archive`, … |
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
| `environment` / `product` / `service` | optional context. `environment` is a ranking weight, not a filter: a matching case is moved up, a differing one down, and a case with no recorded environment (most SWIM records) is kept as is |
| `top_k` | 1–10 |
| `source_types` | optional subset of `support_history`, `incident_reports` (SWIM). Default searches both |

**API:** `POST /v1/similar-incident`  
Each case reports its `source_type`. Report applicability labels; load ticket body for resolution details.

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

### 4.14b CI-TEC domain / recurring-pattern tools

See §6.3 for the full taxonomy and when to use each:

- `kb_citec_domain_catalog()` — no args; call first to get exact domain names / severity tiers.
- `kb_citec_recurring_patterns(group_by=, domains=, severity_tiers=, dept_contains=, customer_contains=, since_days=, min_count=)` → `GET /v1/citec-dashboard/recurring-patterns`
- `kb_citec_failure_bucket_coverage(since_days=, min_count=)` → `GET /v1/citec-dashboard/failure-bucket-coverage`

---

### 4.15 Failure buckets (다중 플러그인 진단 지식 — network/cluster/windows 등)

`packet-capture-rca` 하나만이 아니라 `pacemaker-tools`, `windows-tools` 등 여러 진단 플러그인이
같은 레지스트리에 확정된 실패 패턴을 등재·재사용한다. 도메인은 `fb_domain` 값(예: `network`,
`cluster`, `windows`)으로 구분되며 목록은 [failure-bucket-domains.md](../references/failure-bucket-domains.md)
가 정본이다. 플러그인 개발자용 상세 지침은 [FAILURE_BUCKET_PLUGIN_GUIDE.md](./FAILURE_BUCKET_PLUGIN_GUIDE.md).

- `kb_register_failure_bucket(bucket_name=, symptom=, discriminating_signals=, root_cause=, recommended_action=, fb_domain=, evidence_ref=, counter_signals=, protocol=, environment=, source_plugin=)` — 새 실패 패턴 등록, 즉시 검색 노출. `fb_domain`/`evidence_ref`는 필수(빈 값이면 400) — `evidence_ref`는 자유 서술이 아니라 원자료를 가리키는 구체적 포인터(`CITECTS-2481`, `capture:....pcapng#frame=4821` 등)여야 한다(알려진 접두어와 다르면 응답에 `evidence_ref_warning`). 응답에 `possible_duplicate_of`가 오면 기존 버킷과 사실상 같은 패턴일 수 있다는 뜻 — 확인 후 필요하면 등록 대신 `kb_refine_failure_bucket`으로 정정한다
- `kb_match_failure_bucket(observed_signals=, symptom=, fb_domain=, protocol=, environment=)` — 관찰 신호로 후보 순위화 (구조화 매칭, 하이브리드 검색 아님). 매칭 스코어의 신호 개수 상한(K=4) 때문에 신호가 많다고 불리해지지 않는다. `environment`를 채우면 다른 환경으로 태깅된 버킷은 후보에서 제외(태그 없는 버킷은 계속 후보에 남음)
- `kb_refine_failure_bucket(bucket_id=, add_signal=, add_counter_signal=, environment=, confirm=)` — 확인/반박 시 신뢰도 자동 재계산 (self-improving). `environment`는 미지정 시 기존 값 유지, 지정 시 덮어씀 — 등록 시점에 몰랐던 환경을 나중에 소급 태깅할 때 사용
- `kb_list_failure_buckets(fb_domain=, protocol=, environment=)` / `kb_get_failure_bucket(bucket_id=)`

`environment`(csp\|msp\|onprem\|hybrid)는 `fb_domain`/`protocol`과 직교하는 별도 축이다 — 이
패턴이 특정 배포 환경에서만 성립한다고 원자료로 확인됐을 때만 채운다(근거 없는 값 금지). 값은
[corpus-taxonomy.md](../references/corpus-taxonomy.md)가 코퍼스 전역에서 이미 쓰는 4개 값과 동일.

**API:** `POST /v1/failure-buckets`, `POST /v1/failure-buckets/{id}/refine`,
`POST /v1/failure-buckets/match`, `GET /v1/failure-buckets[/{id}]`

분석 중 이미 알려진 패턴인지 먼저 `kb_match_failure_bucket`으로 확인하고,
새 패턴이면 `kb_register_failure_bucket`으로 등록, 기존 패턴이 맞았거나 틀렸으면
`kb_refine_failure_bucket(confirm=True/False)`로 되먹임한다.

### 4.16 `kb_graph_explore` — 지식그래프 연관 탐색

`kb_match_failure_bucket`/`kb_similar_incident`로 1차 후보를 찾은 **다음 단계**로 쓴다 —
검색의 대체재가 아니라 "찾은 것의 주변을 더 깊이 파는" 용도다.

- `kb_graph_explore(anchor_type=, anchor_value=)` — `anchor_type`은 `failure_bucket`/
  `document`/`component`/`symptom_text` 중 하나. 2hop까지 순회하며, 범용 컴포넌트
  (`Network`/`Storage` 등 degree 수천 이상)는 항상 제외하고 응답에 `제외된 범용
  컴포넌트`로만 표시한다.
- 응답의 `as_of`는 그래프의 마지막 배치 동기화 날짜다 — **실시간이 아니다**(최대 ~1일
  지연 가능). 최종 사실 확인은 `kb_search`/`kb_get_document`로.
- `component`/`symptom_text` 입력은 기존 lexicon 사전으로 변형어("넷앱"→`NetApp`)까지
  해석한다.
- 응답 상단의 `해석된 앵커:`(component/document/failure_bucket)나 `매칭된 컴포넌트:`(symptom_text)로
  입력이 실제 무엇으로 해석됐는지 확인한다. `component`는 lexicon에 없으면 대소문자 무시
  매칭까지 시도한다. 결과가 많으면 카테고리별로 최대 50건까지만 보여주고 "…외 N건"으로
  생략됨을 표시한다.

**API:** `POST /v1/graph/explore`

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

| source_type / section | Content | path pattern | typical `evidence_grade` |
|----------------------|---------|--------------|---------------------------|
| `support_history` | Jira-like tickets | `support_history/CITECTS-2386.md` | A (closed/resolved) / B (open) |
| `incident_reports` | SWIM 장애보고(회사 전체, CI-TEC 11개 도메인은 그 부분집합) | `incident_reports/{fail_seq}.md` | A (조치완료/종료확정) / B (그 외) |
| `tech_repo` | Confluence tech pages, **full body** | `tech_repo/{pageId}.md` | A |
| `confluence_docs` | Other full-body Confluence space (LOOKIN) | `confluence_docs/{pageId}.md` | A |
| `confluence_map` | 2026-10 이후 **전체 본문 포함**(크롤/백필된 공간은), 그 전 데이터는 제목/경로만 — §6.1 참고 | `confluence_map/{pageId}.md` | **C — 본문 있어도 "근거"로 안 씀(정책)** |
| `tuning_ai` | Tuning / SQL notes | `tuning_ai/...` | A- |
| `checkitem` / section `checkitems` | PISA items | use `kb_get_checkitem` or path form | A |
| `dept_archive` | CI-TEC 부서 공유드라이브(R드라이브) 원본 파일 아카이브 | `dept_archive/file_<id>.md` | B |
| `failure_bucket` | 실패 버킷(장애 패턴) — API/MCP로 실시간 등재, `data/raw/` 스캔 대상 아님 | via failure bucket tools | `machine`(자동계산 confidence와 별개 필드) |
| insights / synthesis | Approved insights | via insight tools | n/a (status: draft/review/approved/rejected) |

**Ticket keys:** always `CITECTS-<number>` (case-insensitive in search; normalize to `CITECTS-####` when calling `kb_ticket`).

### 6.1 `evidence_grade` — how much to trust a hit, before you even open it

Every `documents` row carries `evidence_grade` (`A` / `A-` / `B` / `C` / `draft`).
It is **not a relevance score** — it is a statement about how directly this row
*is* the fact, vs. merely *points at* where the fact might live:

- **A / A-**: the row's `body_md` is the actual source content (closed ticket,
  resolved incident report, full Confluence page, PISA checkitem, tuning note).
  Safe to quote and cite directly.
- **B**: same as above but the underlying record is still open/unresolved
  (e.g. a support ticket not yet closed) — content is real but the conclusion
  may change later. Say so if the answer hinges on an outcome.
- **C — `confluence_map` only, by design, regardless of body richness**:
  **Corrected 2026-10-08** — an earlier version of this doc said
  `confluence_map`'s `body_md` is only a title/breadcrumb pointer with no
  real content. That was true before 2026-10-01 but is **no longer
  accurate**: `app.confluence.map_sync._write_map_page` now also writes the
  page's full cleaned text (same cleaning as `confluence_docs`/`tech_repo`)
  for any space that has been crawled/backfilled since — which is most of
  them in production as of 2026-10-07. So a `confluence_map` hit usually
  *does* carry real page content you can read and quote.

  **`evidence_grade="C"` stays regardless** — not because there's nothing
  to read, but as a deliberate P0-A trust-contract decision
  (`_write_map_page`'s own comment): this is an automated structural crawl
  without `confluence_docs`/`tech_repo`'s dedicated sync/verification
  pipeline, so it must never silently outrank an actual A-grade document on
  the same topic. Practically: you *can* answer from a `confluence_map`
  hit's body now, but treat it as **unverified** the same way you would an
  open ticket — if the question is high-stakes, prefer a `tech_repo`/
  `confluence_docs` copy of the same page when one exists (§6.2), or open
  the live URL to confirm currency.

  **Telling old (pointer-only) rows from current (full-body) ones**: check
  whether `metadata["source_version"]`/`["source_modified_at"]` are present
  — both were added in the same 2026-10-01 round, so their absence means
  this row predates full-body capture and really is breadcrumb-only. A dev/
  staging environment's local snapshot can lag behind what's live in
  production here — if in doubt, treat a short/breadcrumb-shaped
  `confluence_map` body as unverified structure-only, not as evidence the
  page itself is short.
- **`draft`**: not yet reviewed (e.g. an unapproved insight) — do not present
  as settled fact.

**Rule of thumb:** before citing a hit as fact, check `source_type`. If it is
`confluence_map`, you may quote its body, but flag it as unverified/C-grade
unless you've also opened the live page or found an A-grade copy of the
same `page_id` elsewhere.

`kb_graph_explore`가 반환하는 문서 결과의 `evidence_grade`도 이 등급 체계를 그대로
재사용한다 — 그래프 자체는 구조(관계)만 저장하고, 신뢰도 판단 기준은 항상 이 절의
Postgres 값이 유일한 출처다.

### 6.2 Structured-copy overlap (`tech_repo` ⊇ some `confluence_map` pages) — `confluence_map` ranks last

A handful of pages exist under **both** `tech_repo` (or `confluence_docs`)
*and* `confluence_map` with the same Confluence `external_id` — both were
crawled from the same live page under two different root configs. When
search returns the same `page_id` under two source_types, **always prefer
the A-grade copy** (`tech_repo`/`confluence_docs`) and treat the
`confluence_map` copy as the lower-priority duplicate — even though both
may now contain the same full body text, `confluence_map` keeps
`evidence_grade="C"` by the policy in §6.1, so it never outranks the other
copy for citation purposes.

### 6.3 CI-TEC domain taxonomy & recurring-pattern tools

Separate from the generic `area`/`domain` search filter (§4.2), CI-TEC owns an
**11-domain lens** over `incident_reports` (SWIM) specifically — because SWIM
is a company-wide log and most of it (~59%, mostly generic Network noise) is
outside CI-TEC's actual 10 component domains + 1 cross-cutting one:

```
Linux · Windows · VMware · OpenStack · Kubernetes · Middleware ·
Network · Storage · Ceph · Database · 성능(cross-cutting)
```

An incident can carry more than one domain tag (e.g. "DB Hang" → Database +
성능). Always call `kb_citec_domain_catalog()` first if you need the exact
domain spelling or the SWIM severity-tier vocabulary (`major` / `minor` /
`failover_no_impact` / `customer_fault` / `vendor_fault` / `unknown`) — don't
guess the Korean/English spelling.

| Tool | When |
|------|------|
| `kb_citec_domain_catalog()` | Confirm exact domain names / severity tiers / valid `group_by` dimensions before calling either tool below |
| `kb_citec_recurring_patterns(group_by=, domains=, severity_tiers=, since_days=, min_count=)` | "어떤 도메인에서 반복 장애가 많은가", "최근 2년 Database major 몇 건" — real aggregation over `incident_reports`, never count with the LLM |
| `kb_citec_failure_bucket_coverage(since_days=, min_count=)` | "반복 확인된 장애인데 아직 failure_bucket(진단 패턴)에 등록 안 된 도메인이 뭔가" — a cleanup-gap report, not a search tool. Note: CI-TEC's 11 domains and failure_bucket's `fb_domain` vocabulary (network/cluster/windows/dbms/linux/virtualization/middleware/storage) are **different, overlapping** taxonomies owned by different systems — `Kubernetes`/`성능` have no `fb_domain` equivalent yet (`no_fb_domain_defined` ≠ 0% coverage, it means the bucket vocabulary doesn't cover that domain at all) |

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

### Scenario G — "이 Confluence 페이지에 뭐라고 적혀 있나?" (`confluence_map` hit, C-grade)

1. `kb_search(query="…", section="confluence_map")` → hit's body is usually the real page text now (§6.1), but carries `evidence_grade="C"`.
2. Check if the same `external_id`/page_id also appears under `tech_repo` or `confluence_docs` in the same search (or re-search without `section` filter) — if so, **prefer that A-grade copy** for the answer (§6.2); `confluence_map` ranks last when both exist.
3. If `confluence_map` is the only copy: you can answer from its body, but say the source is an unverified C-grade index entry rather than presenting it with the same confidence as an A-grade document. If the body looks like just a breadcrumb (no real paragraphs), it predates full-body capture — open the live `URL`/`source_uri` instead of guessing from the title.

### Scenario H — "어느 CI-TEC 도메인에서 반복 장애가 제일 많은가, 그 중 failure_bucket 정리 안 된 건?"

1. `kb_citec_domain_catalog()` — confirm the 11 domain names.
2. `kb_citec_recurring_patterns(group_by="domain", since_days=730, min_count=3)` — report counts straight from the response, don't re-tally.
3. `kb_citec_failure_bucket_coverage(since_days=730, min_count=3)` — cite only domains with `status="gap"` as "정리 필요"; `no_fb_domain_defined` means the vocabulary doesn't cover that domain yet, not 0% coverage — say so if asked.

### Scenario I — "이 failure_bucket과 관련된 다른 장애·문서가 더 있나?"

1. `kb_get_failure_bucket(bucket_id="FB-12")`로 버킷 확인 (`FB-12`는 예시 — 실제
   `bucket_id`는 UUID 형식).
2. `kb_graph_explore(anchor_type="failure_bucket", anchor_value="FB-12")`로 2hop 연관
   문서/컴포넌트/과거 장애 탐색.
3. 범용 컴포넌트가 "제외된 범용 컴포넌트"에 뜨면 — 그건 애초에 의미 없는 결과이니
   무시하고, 나머지 구체적 컴포넌트/문서로만 답변 구성.
4. `as_of`가 비어 있거나(아직 백필 전) 2일 이상 지났으면 응답에 그 사실을 한 줄
   명시(신선도 캐비엇).

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
| Cite a `confluence_map` hit as settled fact with no caveat | `evidence_grade="C"` by policy regardless of body richness (§6.1) — automated crawl, no dedicated verification pipeline | Prefer a `tech_repo`/`confluence_docs` copy of the same page if one exists (§6.2); otherwise answer but flag as unverified |
| Assume a short/breadcrumb-only `confluence_map` body means the page itself is short | It may just predate 2026-10-01 full-body capture (§6.1) | Check `metadata["source_version"]` presence, or open the live URL |
| Count/rank CI-TEC domain incidents by scanning search results yourself | Error-prone, and ignores multi-domain tagging | `kb_citec_recurring_patterns` |
| Guess CI-TEC domain spelling ("Open Stack", "케이-에이트-에스") | `kb_citec_recurring_patterns` filters do exact match | `kb_citec_domain_catalog()` first |

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

This guide tracks MCP tools as of **kb_list_tickets / kb_analytics / kb_similar_incident / kb_list_checkitems / kb_capacity_estimate / kb_citec_domain_catalog / kb_citec_recurring_patterns / kb_citec_failure_bucket_coverage / kb_tools_help** and `kb_search` → `/v1/search` (2026-10-07 revision: added §6.1 evidence_grade trust model, §6.2 structured-copy overlap, §6.3 CI-TEC domain tools. **2026-10-08 correction**: §6.1/§6.2 originally said `confluence_map` body_md is pointer-only with no real content — that was only true before the 2026-10-01 full-body capture round; most production `confluence_map` rows now carry the real page text, but `evidence_grade="C"` and the "rank below tech_repo/confluence_docs on the same page_id" rule are unchanged by policy, not by content availability).
If a tool name is missing in the live server, call `kb_tools_help` or fall back to `kb_query` + REST in [EXTERNAL_API.md](./EXTERNAL_API.md).
