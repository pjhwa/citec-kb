# CI-TEC Knowledge Platform

부서 지식(Jira 지원이력 · Confluence Tech-Repo · PISA 등) 통합 검색 · RAG · 유사장애 브리핑 플랫폼.

- **설계:** 폐쇄망 · Docker 경량 · GLM 5.2 (dev: OpenRouter, prod: Fabrix)
- **현재:** **P1 완료 · P2/P3 핵심 엔지니어링 강** — full planner · catalog route+answer 110/110  
- **계획:** `docs/IMPLEMENTATION_PLAN.md` **v1.25** (Insight promote+index · feedback · ops · worker)

## 빠른 시작 (개발 서버)

```bash
cd ~/dev/citec-kb

# API 키 (한 번)
cp ~/tmp/citec-wiki-qa/.env .env   # 이미 있으면 스킵
# 또는: cp .env.example .env  후 OPENROUTER_API_KEY 설정

# 지식 코퍼스: data/raw (등록 완료). 다른 경로를 쓸 때만:
# export RAW_HOST_DIR=/path/to/raw

docker compose up -d --build

# 또는 종료 → 재빌드 → 재기동 한 번에:
./rebuild.sh              # 도움말: ./rebuild.sh --help
# ./rebuild.sh --no-cache
# ./rebuild.sh api mcp
```

### 호스트 포트 (할당 대역 8572–8580)

| 호스트 포트 | 서비스 |
|-------------|--------|
| **8572** | web (UI) |
| **8573** | api |
| **8574** | postgres |
| **8575** | redis |
| **8576** | Keycloak (optional profile) |
| **8577** | **MCP** (Claude/Cursor 연동) |
| 8578–8580 | 예약 |

```bash
curl -s localhost:8573/v1/health | jq .
curl -s localhost:8573/v1/health/llm | jq .
# UI:            http://localhost:8572
# 설계·구현 문서: http://localhost:8572/docs/
# API Swagger:   http://localhost:8572/api/docs  또는  http://localhost:8573/docs
# MCP (Claude):  http://localhost:8577  · docs/MCP.md
# 외부 연동 REST: docs/EXTERNAL_API.md
```

### MCP (Claude Desktop / Claude Code)

```bash
docker compose up -d --build mcp
# Claude Desktop: mcp-server/claude_desktop_config.example.json
#   → url http://localhost:8577/mcp  (streamable-http)
# stdio: mcp-server/claude_desktop_stdio.example.json
CITEC_KB_BASE_URL=http://localhost:8573 python3 mcp-server/test_smoke.py
```

도구: `kb_search`, `kb_get_document`, `kb_ask`, `kb_query`, `kb_ticket`, `kb_list_insights`, …  
(`wiki_*` 별칭 = citec-wiki-qa MCP 호환)

### 설계·구현 문서 (웹)

| URL | 내용 |
|-----|------|
| http://localhost:8572/docs/ | 문서 포털 |
| http://localhost:8572/docs/design.html | 시스템 설계서 v2.3 |
| http://localhost:8572/docs/implementation-plan.html | 구현 계획 |
| http://localhost:8572/docs/query-catalog-analysis.html | 질문 100 분석 |

### 지식 문서 등록 · 인제스트 (PR-03)

원본 `~/dev/temp/raw` → `data/raw/` 복사 (총 ~5007 files). 메타: `data/raw_manifest.json`.

```bash
# 전량 적재 (idempotent)
docker compose exec api python -m app.ingest.cli --raw-dir /data/raw

# 또는 API
curl -X POST localhost:8573/v1/ingest/run -H 'Content-Type: application/json' -d '{}'
curl -s localhost:8573/v1/ingest/stats | jq .
curl -s localhost:8573/v1/ingest/jobs | jq .
```

### 검색 · 임베딩 · Checkitem (Phase 1)

```bash
# 하이브리드 검색 (API: e5 query embed + FTS+pgvector RRF; 모델 없으면 FTS degrade)
curl -s -X POST localhost:8573/v1/search -H 'Content-Type: application/json' \
  -d '{"q":"모니모 Redis","top_k":5}' | jq '{vector_used, trust_retrieval, total, top: .results[:2]}'

# PISA checkitem 테이블 (예: Linux FS)
curl -s 'localhost:8573/v1/checkitems?area=Linux&q=FS&limit=10' | jq .

# 배치 임베딩 (호스트 venv · sentence-transformers, idempotent)
export PYTHONPATH=apps/api
export DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_knowledge
.venv/bin/python -m app.embed.cli --batch-size 16 -v

# Gold retrieval 평가
.venv/bin/python -m app.eval.retrieval --out /tmp/retrieval_eval.json
```

UI 검색: http://localhost:8572/search.html  
Fast QA: http://localhost:8572/chat.html  
유사장애: http://localhost:8572/si.html  

```bash
# Issue frames (지원이력 구조화)
python -m app.frames.cli --force
curl -s localhost:8573/v1/frames/stats | jq .
# 유사장애
curl -s -X POST localhost:8573/v1/similar-incident -H 'Content-Type: application/json' \
  -d '{"symptom":"모니모 Redis 타임아웃","product":"Redis","top_k":3}' | jq '{brief, cases: [.cases[]|{external_id,applicability}]}'
```


```bash
# Fast / Deep RAG (근거 + Trust)
curl -s -X POST localhost:8573/v1/chat -H 'Content-Type: application/json' \
  -d '{"q":"모니모 Redis 타임아웃 조치","mode":"fast","top_k":6}' | jq '{abstained, trust, answer: .answer[:200], citations: [.citations[].id]}'
# SSE stream
curl -sN -X POST localhost:8573/v1/chat -H 'Content-Type: application/json' \
  -d '{"q":"CITECTS-2502 요약","mode":"fast","stream":true}'
# Groundedness sample (20문, LLM 호출 다수)
PYTHONPATH=apps/api .venv/bin/python -m app.eval.groundedness_main --gold data/gold/qa_groundedness_20.json
```

```bash
# 기간·목록 (Jira Created 메타데이터 — hybrid 검색 아님)
curl -s -X POST localhost:8573/v1/query/route -H 'Content-Type: application/json' \
  -d '{"q":"지난 주 지원건","limit":20}' | jq '{intent, range_label, total: .result.total, ids: [.result.items[].external_id]}'
curl -s 'localhost:8573/v1/tickets?relative=지난+주&limit=20' | jq '{range_label, total, date_from, date_to}'
# 집계 (LLM 계수 없음)
curl -s 'localhost:8573/v1/analytics/tickets?group_by=year' | jq '{total, llm_used, buckets: .buckets[:5]}'
curl -s -X POST localhost:8573/v1/query/route -H 'Content-Type: application/json' \
  -d '{"q":"연도별 지원 건수"}' | jq '{intent, total: .result.total, buckets: .result.buckets[:3]}'
# 공수·대수 (FAQ 1안 Rules — LLM 숫자 없음)
curl -s -X POST localhost:8573/v1/capacity/estimate -H 'Content-Type: application/json' \
  -d '{"period_days":14,"fields":["Linux","DBMS"]}' | jq '{scale, scale_note, totals, fields}'
curl -s -X POST localhost:8573/v1/query/route -H 'Content-Type: application/json' \
  -d '{"q":"2주 분야별 대수"}' | jq '{intent, scale: .result.scale, mm: .result.totals.mm}'
# Full planner
curl -s -X POST localhost:8573/v1/query -H 'Content-Type: application/json' \
  -d '{"q":"리눅스 PISA 체크리스트 항목"}' | jq '{intent, total: .result.total}'
# UI: /tickets.html · /analytics.html · /capacity.html
# Route gold: PYTHONPATH=apps/api .venv/bin/python -m app.eval.route_eval --gold data/gold/time_list_analytics_10.json
# Catalog routing: PYTHONPATH=apps/api .venv/bin/python -m app.eval.catalog_route --gold data/gold/query_catalog_100.json
# Catalog answer:  PYTHONPATH=apps/api .venv/bin/python -m app.eval.catalog_answer --gold data/gold/query_catalog_100.json --out /tmp/catalog_answer.json
```

> **기간·목록·집계·공수·체크리스트·유사장애**는 planner가 경로를 고릅니다 (`POST /v1/query`).  
> Catalog answer eval은 multi-query hybrid(원질+any+sample id)를 사용합니다. exhaustive/prevention·prod multi-query 이식은 후속.

## 서비스 (5)

| Service | 역할 |
|---------|------|
| `web` | nginx + static UI, `/v1` 프록시 |
| `api` | FastAPI |
| `worker` | 잡 워커 스텁 (heartbeat) |
| `postgres` | pgvector/pg16 |
| `redis` | 큐/캐시 |

LLM은 compose **밖** (OpenRouter 또는 사내 Fabrix).

## 환경 변수

`.env.example` 참고. 개발 필수:

- `OPENROUTER_API_KEY`
- `COMPANY_MODEL_ID=glm-5.2` → `config/models.json` 의 `openrouter_id` (`z-ai/glm-5.2`)

## 레포 구조

```
apps/api apps/worker apps/web
packages/domain
config/models.json
data/gold data/seeds
docs/
docker-compose.yml
```

## DB 마이그레이션 (PR-02)

API 컨테이너 기동 시 `alembic upgrade head` 자동 실행.

```bash
# 호스트에서 (venv + deps 필요)
export DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_knowledge
./scripts/migrate.sh

# 또는
docker compose exec api alembic current
docker compose exec api alembic upgrade head
```

스키마: `sources`, `documents`, `document_sections`, `chunks`, `embeddings`(pgvector **768**, e5-base),
`ingest_jobs`, `checkitems`, `entities`, `issue_frames`, `capacity_rules`, …
헬스: `checks.postgres.alembic_revision`.

## 로드맵

`docs/IMPLEMENTATION_PLAN.md` **v1.25** — Insight approve/promote/index · feedback · worker jobs · load smoke · ops.

## 보안

- `.env` 는 gitignore. 키를 커밋하지 말 것.
- 운영 폐쇄망에서는 OpenRouter 대신 Fabrix/`COMPANY_*` 사용.

## CI

Push to `main` runs unit tests via `.github/workflows/ci.yml` (lightweight deps, no torch).

## Keycloak (optional)

```bash
docker compose --profile keycloak up -d keycloak
.venv/bin/python scripts/keycloak_oidc_e2e.py
```

See `docs/OIDC_IDP_SETUP.md`.
