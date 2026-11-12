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
| `incident_reports` | `incident_reports` |
| `tech_repo` | `tech_repo` |
| `tuning_ai` / `sql_tuning` | `tuning_ai` |
| `vendor_docs` | `vendor_docs` |
| `decision` / `decisions` | `confirmed_decision` |  (`synthesis`/`insight`는 폐기 — 400)
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
  "returned_count": 1,
  "total_candidates": 1,
  "fts_ready": true,
  "backend": "citec-kb"
}
```

`total` 은 이번 페이지에 반환된 건수(deprecated 별칭), `total_candidates` 는 중복 제거 후 `top_k`
로 자르기 전 후보 수이며 코퍼스 전체 매칭 수가 아니다. 각 결과의 `duplicate_count` 는 사본
접기(`diversify_copies`)로 그 결과에 합쳐진 `confluence_map` 사본 수다.

검색 요청 필터(`filters` 또는 `/api/query` 본문): `exclude_page_ids`(그 페이지만),
`exclude_subtree_ids`(그 페이지+모든 하위 — 정답 누출 방지는 이쪽), `exclude_source_types`,
`include_irrelevant_maps`(미지정: `source_type=confluence_map` 일 때만 true, 명시 값 우선),
`diversify_copies`(기본 true). 자세한 의미는 `docs/MCP.md` 「검색 필터 파라미터」.

적용 엔드포인트: `POST /v1/search` · `POST /v1/query` items · `POST /v1/chat` citations ·
`GET /api/wiki/search` · analytics samples · `GET /v1/tickets` 목록/상세.

### 업로드 — wiki-qa `POST /api/upload` 호환

기술지원이력 / SWIM 장애보고서 / 테크리포 / DBMS튜닝 문서를 외부 시스템이 파일로 업로드하는
경로입니다. MCP에서는 `kb_upload_document(filename=, content=, source_type=)`로 이 엔드포인트를
그대로 감싸 호출합니다(쓰기 도구 — 결과가 코퍼스에 즉시 반영됨).

| Method | Path | 설명 |
|--------|------|------|
| POST | `/api/upload` | 파일 1개 업로드 → `job_id` 즉시 반환, 백그라운드 ingest |
| GET | `/api/ingest-status/{job_id}` | SSE로 ingest 진행상황 추적 |
| POST | `/api/upload-multiple` | 파일 여러 개(`files` 필드), 동일 `source_type` 적용 |

**`source_type` 지원 값 / 별칭** (citec-wiki-qa README 별칭 표 기반 + citec-kb 자체 확장):

| 값/별칭 | citec-kb 내부 source_type |
|---------|---------------------------|
| `support_history`, `support` | `support_history` |
| `incident_reports`, `incident` | `incident_reports` |
| `tech_repo`, `confluence_docs`, `confluence`, `techrepo`, `tech-repo` | `tech_repo` |
| `tuning_ai`, `sql_tuning`, `sql`, `issue_analysis`, `dbms_tuning`, `dbms-tuning`, `tuning-ai` | `tuning_ai` |
| `dept_archive`, `dept-archive` | `dept_archive` |
| `dogether_preventive`, `dogether-preventive`, `dogether`, `preventive`, `prevention` | `dogether_preventive` |

`incident_reports` 문서를 조회하는 `GET /v1/tickets/{id}`·`GET /api/wiki/file` 응답에는 `related_prevention_posts`(`external_id`, `title`, `게시유형`, `게시일시`, `source_uri`, `match`: `swim_ids`|`candidate`)가 붙는다 — 해당 SWIM 번호를 언급한 DOgether 장애예방 게시글이다.

허용 확장자: `.md`, `.txt` (전체 재구축 스캔도 두 확장자 모두 포함). 최대 파일 크기: **20MB**
(초과 시 `413 Request Entity Too Large`).

> **범위 제외 (이번 라운드)**: `vendor_docs`(`vendor`)와 `checkitems`(`.xls`/`.xlsx`)는 citec-kb에
> 대응 파서가 아직 없어 `501 Not Implemented`로 거부됩니다. 그 외 알 수 없는 `source_type` 값은
> 기존과 동일하게 `400 Bad Request`.

반영 시 문서 upsert(FTS) 직후 **임베딩까지 자동 생성**되어 시맨틱 검색에도 즉시 반영됩니다
(임베딩 실패는 job을 `failed`로 만들지 않고 `done` 이벤트의 `embed.error`로만 표시 — 문서 자체는
정상 반영됨).

**요청 예시**

```bash
curl -X POST http://<host>/api/upload \
     -F "file=@CITECTS-1234.md" \
     -F "source_type=support_history"

curl -X POST http://<host>/api/upload \
     -F "file=@swim_26090761356.md" \
     -F "source_type=incident_reports"

curl -X POST http://<host>/api/upload \
     -F "file=@148554390_kernel_params.txt" \
     -F "source_type=tech_repo"

curl -X POST http://<host>/api/upload \
     -F "file=@ISS-5678.md" \
     -F "source_type=tuning_ai"
```

**응답**

```json
{ "job_id": "a1b2c3d4-...", "filename": "148554390_kernel_params.txt", "status": "queued" }
```

**진행상황 추적**

```bash
curl -N http://<host>/api/ingest-status/a1b2c3d4-...
# data: {"type":"log","text":"📥 ingest 진행 중… (running)"}
# data: {"type":"done","status":"done","document_id":"...","action":"inserted","embed":{"embedded":1,"errors":0,...}}
```

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

`template`: `general` · `checkitems` · `support_history` · `incident_reports` · `tech_repo` · `tuning_ai` · `confirmed_decision` …

### Synthesis — 폐기됨 (2026-11-05)

`GET /api/synthesis`, `/api/synthesis/{slug}` 는 **HTTP 410**(폐기 안내)을 돌려준다. 한 릴리스 뒤 제거. 대체: `/v1/decisions` (사람 확정 결정).

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
| * | `/v1/insights*` | **폐기됨** — HTTP 410 안내 스텁(다음 릴리스에서 제거) |
| POST/GET | `/v1/decisions` | 사람 확정 결정 제안(author+) / 목록(`status`,`topic`,`domain`,`sort`,`include_superseded`) |
| GET | `/v1/decisions/{id}` | 카드 페이로드(선택지별 근거, 충돌, 고정 대비 현재 버전, 이력) |
| POST | `/v1/decisions/{id}/evidence` | 근거 추가(author+, 인용 원문 검증) |
| GET/POST | `/v1/decisions/{id}/nonce` · `/verdict` | 판정(senior+, UI nonce 필요, 인증 ON이면 `decided_by`는 `principal.name`) |
| POST | `/v1/decisions/{id}/supersede` · `/retire` · `/restore` | 대체 / 보류 / 복구 (senior+, 하드 삭제 없음) |
| GET | `/v1/decisions/metrics` | 판정 소요 중앙값, AI 추천 override 비율, NEI 비율 등 |
| POST/GET | `/v1/failure-buckets` | 실패 버킷 등록/목록 (self-improving 진단 지식) |
| GET | `/v1/external/catalog` | 이 문서의 기계 가독 카탈로그 |
| GET | `/v1/external/search` | 간단 GET 검색 |
| GET | `/v1/external/document?path=` | 문서 본문 |
| GET | `/v1/external/health` | `/api/health` 동일 |

### 실패 버킷 (`/v1/failure-buckets/*`)

| Method | Path | 설명 |
|--------|------|------|
| POST | `/v1/failure-buckets` | 신규 버킷 등록 (bucket_name, symptom, discriminating_signals, counter_signals, root_cause, recommended_action, **fb_domain**(필수), **evidence_ref**(필수), protocol, created_by) — 즉시 색인. 응답에 `possible_duplicate_of`(선택) 포함 가능 |
| GET | `/v1/failure-buckets` | 목록 (**fb_domain**, protocol, min_confidence, limit, offset 필터). **기본으로 활성(`status=active`)만** — `include_retired=true`면 retired·`-test`(`status=test`) 포함 |
| GET | `/v1/failure-buckets/{id}` | 단건 상세. retire된 건도 조회되며 `status`, `retired_at`, `retired_by`, `retire_reason` 포함 |
| POST | `/v1/failure-buckets/{id}/refine` | 신호 추가 + 확인(confirm=true)/반박(confirm=false) — 신뢰도 자동 재계산 |
| POST | `/v1/failure-buckets/match` | observed_signals[] + symptom + **fb_domain**(선택) → 후보 버킷 순위. 활성 버킷만 대상 |
| POST | `/v1/failure-buckets/{id}/retire` | 숨김(삭제 아님). 본문 `{reason}` 8자 이상. 롤 `senior`/`admin`. 멱등 |
| POST | `/v1/failure-buckets/{id}/restore` | retire 복구(또는 `-test` 승격). 롤 `senior`/`admin`. 멱등 |
| POST | `/v1/failure-buckets/retire-batch` | `{created_by_prefix(3자+), reason, dry_run=true}` — 시험 방 일괄 정리. 롤 `admin` |

등록 응답에는 `status`, `registrant_kind`, `already_registered`와 (해당 시) `possible_duplicate_of`, `created_by_warning`, `evidence_ref_warning`, `evidence_ref_note`가 붙는다. `created_by`가 `-test`로 끝나면 `status="test"`로 저장되어 목록·매칭·검색에서 제외된다. 쓰기 호출은 `AUTH_MODE`가 켜지면 `X-API-Key`가 필요하다(현재 off — [AUTH_ENABLEMENT.md](./AUTH_ENABLEMENT.md)). ToolBox 도구 연동: [FAILURE_BUCKET_TOOLBOX_INTEGRATION.md](./FAILURE_BUCKET_TOOLBOX_INTEGRATION.md).

> `source_type=failure_bucket`은 `data/raw/` 파일 스캔 대상이 아니라 API/MCP로 실시간 등재되는 카테고리입니다.
> `fb_domain`은 플러그인/진단 영역 파셋(`network`/`cluster`/`windows`, …) 이며 코퍼스 전역
> `domain`(`kb_search(area=)`가 쓰는 7개 고정 어휘)과는 별도 축입니다 — 값 목록은
> [failure-bucket-domains.md](../references/failure-bucket-domains.md) 참고.

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
| `kb_list_insights` / `wiki_list_synthesis` | 폐기됨 (안내 스텁) |
| `kb_get_insight` / `wiki_get_synthesis` | 폐기됨 (안내 스텁) |
| `kb_list_decisions` / `kb_get_decision` / `kb_propose_decision` / `kb_add_decision_evidence` | `/v1/decisions*` |
| `kb_ask` / `wiki_ask` | `POST /api/query` SSE |
| `kb_query` | `POST /v1/query` |
| `kb_ticket` | `GET /v1/tickets/{id}` |
| `kb_register_failure_bucket` | `POST /v1/failure-buckets` |
| `kb_refine_failure_bucket` | `POST /v1/failure-buckets/{id}/refine` |
| `kb_match_failure_bucket` | `POST /v1/failure-buckets/match` |
| `kb_list_failure_buckets` | `GET /v1/failure-buckets` |
| `kb_get_failure_bucket` | `GET /v1/failure-buckets/{id}` |
| `kb_retire_failure_bucket` | `POST /v1/failure-buckets/{id}/retire` |
| `kb_restore_failure_bucket` | `POST /v1/failure-buckets/{id}/restore` |

Claude Desktop 예시: `mcp-server/claude_desktop_config.example.json`

---

## 3-1. 호출자 식별과 사용 현황 (admin → 사용 현황 → "API 호출")

MCP(AI CLI)는 `mcp_access_log`로, 그 밖의 `/v1/*` 호출은 `api_access_log`로 기록하고 admin 화면에서 **웹 / 내부 시스템 / 외부 시스템**으로 나눠 본다.

| 구분 | 판정 |
|---|---|
| 웹 | 브라우저 요청 — `Sec-Fetch-Site` 헤더가 있거나 `Mozilla` UA + `Referer`가 있음 |
| 내부 시스템 | 호출 IP가 `API_INTERNAL_CIDRS`(기본: 루프백, `169.254/16`, `172.16/12` = 도커 브리지) 안 — worker, 서버 위 스크립트, 컨테이너 |
| 외부 시스템 | 그 밖의 IP — 다른 서버가 호출하면 여기로 잡힌다 |

- **다른 시스템에서 오는 호출도 구분된다.** 이름까지 보려면 호출 측이 헤더를 보낸다: `X-Citec-Client: <시스템명>` (예: `toolbox-sosanalyzer`). 없으면 인증된 토큰 이름(`AUTH_MODE`가 켜진 경우), 그것도 없으면 User-Agent 제품명(`curl`, `python-requests` 등)으로 표시한다. `mcp-server`는 MCP 전용 값이라 이 로그에서는 제외된다.
- **IP는 참고용이다.** nginx가 넣는 `X-Real-IP`(없으면 `X-Forwarded-For`, 그것도 없으면 접속 IP)를 쓴다. api 포트(8573)에 직접 붙는 클라이언트는 이 헤더를 스스로 보낼 수 있으므로 보안 판단 근거로 쓰지 않는다. 사내 다른 서버(10.x, 192.168.x)는 모두 "외부"로 분류된다. 기준을 바꾸려면 `API_INTERNAL_CIDRS`(쉼표 구분 CIDR)를 설정한다.
- **기록하지 않는 것:** MCP(별도 테이블), admin 화면 자신의 폴링(Referer `/admin.html`), `/v1/health`·`/v1/ops/*`·`/v1/auth/*`·`/v1/graph/status`, OPTIONS 사전요청. 경로는 라우트 템플릿(`/v1/failure-buckets/{bucket_id}`)으로 저장하고, 매칭되지 않는 404 경로는 앞 3개 구간만 남긴다. 요청 본문·쿼리스트링·토큰은 저장하지 않는다.
- **성능·보관:** 요청은 큐에 넣고 백그라운드 스레드가 2초 단위로 배치 insert 한다(요청이 DB를 기다리지 않음, 큐가 가득 차면 버리고 `dropped`로 센다). `API_ACCESS_LOG_RETENTION_DAYS`(기본 30)보다 오래된 행은 매시간 삭제한다. `API_ACCESS_LOG=off`로 끌 수 있다.
- 기록은 이 기능을 배포한 시점부터 쌓인다(과거 호출은 복원되지 않는다). 마이그레이션 `20261105_0020`(새 테이블만 추가)이 필요하다.

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
