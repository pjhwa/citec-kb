# CI-TEC Knowledge Platform

부서 지식(Jira 지원이력 · Confluence Tech-Repo · PISA 등) 통합 검색 · RAG · 유사장애 브리핑 플랫폼.

- **설계:** 폐쇄망 · Docker 경량 · GLM 5.2 (dev: OpenRouter, prod: Fabrix)
- **현재:** **PR-01** monorepo skeleton + compose + health + LLM probe

## 빠른 시작 (개발 서버)

```bash
cd ~/dev/cite-c-knowledge

# API 키 (한 번)
cp ~/tmp/citec-wiki-qa/.env .env   # 이미 있으면 스킵
# 또는: cp .env.example .env  후 OPENROUTER_API_KEY 설정

# 지식 코퍼스: data/raw (등록 완료). 다른 경로를 쓸 때만:
# export RAW_HOST_DIR=/path/to/raw

docker compose up -d --build
```

### 호스트 포트 (할당 대역 8572–8580)

| 호스트 포트 | 서비스 |
|-------------|--------|
| **8572** | web (UI) |
| **8573** | api |
| **8574** | postgres |
| **8575** | redis |
| 8576–8580 | 예약 (향후) |

```bash
curl -s localhost:8573/v1/health | jq .
curl -s localhost:8573/v1/health/llm | jq .
# UI: http://localhost:8572
# Docs: http://localhost:8573/docs
```

### 지식 문서 등록

원본 `~/dev/temp/raw` 코퍼스를 `data/raw/` 로 복사·등록함 (총 ~5007 files).  
목록 메타: `data/raw_manifest.json`. 본문 파일은 gitignore (`data/raw/`).

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

스키마: `sources`, `documents`, `document_sections`, `chunks`, `embeddings`(pgvector 1024),
`ingest_jobs`, `checkitems`, `entities`, `issue_frames`, `capacity_rules`, …
헬스: `checks.postgres.alembic_revision`.

## 로드맵

`docs/IMPLEMENTATION_PLAN.md` — 다음 PR-03 ingest.

## 보안

- `.env` 는 gitignore. 키를 커밋하지 말 것.
- 운영 폐쇄망에서는 OpenRouter 대신 Fabrix/`COMPANY_*` 사용.
