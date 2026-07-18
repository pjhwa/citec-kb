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

# raw 지식 경로 (기본: 형제 경로 ../temp/raw — 없으면 환경변수로 지정)
export RAW_HOST_DIR=/home/citec/dev/temp/raw

docker compose up -d --build
```

| URL | 설명 |
|-----|------|
| http://localhost:8080 | Web (헬스 UI) |
| http://localhost:8000/docs | API OpenAPI |
| http://localhost:8000/v1/health | 헬스 |
| http://localhost:8000/v1/health/llm | OpenRouter/GLM 라이브 프로브 |
| localhost:5433 | Postgres (host) |
| localhost:6380 | Redis (host; 6379 충돌 회피) |

```bash
curl -s localhost:8000/v1/health | jq .
curl -s localhost:8000/v1/health/llm | jq .
```

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

## 로드맵

`docs/IMPLEMENTATION_PLAN.md` — PR-02 schema부터 이어짐.

## 보안

- `.env` 는 gitignore. 키를 커밋하지 말 것.
- 운영 폐쇄망에서는 OpenRouter 대신 Fabrix/`COMPANY_*` 사용.
