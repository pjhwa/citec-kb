# citec-kb 폐쇄망 배포 가이드

wiki-qa `out.sh` / `in.sh` 와 같은 **분리 번들 + 버전 추적 + 변경분만 배포** 패턴입니다.  
citec-kb 는 **multi-service + Postgres(pgvector)** 이므로, wiki-qa 의 `db/vec.db` 와 달리 **인덱스는 PG 볼륨**에 있습니다.

| 스크립트 | 실행 위치 | 역할 |
|----------|-----------|------|
| `scripts/out.sh` | 개발 | 패키징 |
| `scripts/in.sh` | 운용 | 배포 적용 |

---

## 번들 구성

| 번들 | 파일명 | 언제 | 크기 |
|------|--------|------|------|
| **code** | `citec-kb-code-vN.tar.gz` | 앱/웹/설정/마이그레이션 수정 | 작음 (일상) |
| **docker** | `citec-kb-docker-vN.tar.gz` | Dockerfile·requirements 변경 | 큼 |
| **docker-mcp** | `citec-kb-docker-mcp-vN.tar.gz` | MCP pip deps 변경 | 중 |
| **docker-keycloak** | `citec-kb-docker-keycloak-vN.tar.gz` | OIDC 로컬 IdP (선택) | 중 |
| **data** | `citec-kb-data-dN.tar.gz` | raw/seeds (+ `--pg-dump`) | 코퍼스 따라 |
| **model** | `citec-kb-model.tar.gz` | 임베딩 모델 최초/교체 | 큼 (~1GB) |

### `docker compose ps` ↔ 번들

| Service | Image | 번들 | 기본 |
|---------|-------|------|------|
| postgres | `pgvector/pgvector:pg16` | docker | ✅ |
| redis | `redis:7-alpine` | docker | ✅ |
| web | `nginx:1.27-alpine` | docker | ✅ |
| api | `citec-kb-api:latest` | docker | ✅ |
| worker | `citec-kb-worker:latest` | docker | ✅ |
| mcp | `citec-kb-mcp:latest` | docker-mcp | ✅ |
| keycloak | `quay.io/keycloak/keycloak:26.0` | docker-keycloak | opt-in |

### 호스트 마운트 (code 번들만으로 반영)

| 경로 | 서비스 | 비고 |
|------|--------|------|
| `apps/api/app` | api | Python 앱 |
| `apps/api/alembic` (+ ini) | api | 스키마 마이그레이션 |
| `apps/worker/app` | worker | |
| `apps/web/public` + `nginx.conf` | web | 정적 UI |
| `mcp-server/server.py` | mcp | 로직 변경 (pip deps 는 docker-mcp) |
| `config/` | api/worker | `models.json` 은 in.sh 가 운용값 보존 |
| `data/`, `data/raw` | api/worker | 코퍼스 |
| `models/` (`MODELS_HOST_DIR`) | api | HF 캐시 |

**이미지 재빌드가 필요한 것:** requirements / Dockerfile / torch 등.

### 데이터 vs 검색 인덱스 (wiki-qa 와의 차이)

| | wiki-qa | citec-kb |
|--|---------|----------|
| 지식 파일 | `wiki/` + `raw/` | `data/raw` |
| 임베딩/FTS | `db/vec.db` (data 번들에 포함) | **Postgres `pgdata` 볼륨** |
| 최초 검색 가능 상태 | data 배포만으로 OK | **PG 덤프 복원** 또는 **ingest + embed** |

```bash
# A) 개발 DB 복제 (권장 — 검색 즉시 가능)
scripts/out.sh --data --pg-dump
# 운용:
scripts/in.sh --data --restore-pg -y

# B) 파일만 배포 후 재인덱싱
scripts/out.sh --data
scripts/in.sh --data -y
docker compose exec api python -m app.ingest.cli --raw-dir /data/raw
docker compose exec api python -m app.embed.cli
```

---

## 개발: 패키징

```bash
cd ~/dev/citec-kb   # 레포 루트

scripts/out.sh --help

# 일상 코드
scripts/out.sh --code

# MCP 로직만 (server.py 마운트) → code 로 충분
# MCP pip/requirements 변경 시:
scripts/out.sh --docker-mcp

# Dockerfile / requirements
scripts/out.sh --code --docker --docker-mcp

# 지식 + DB 스냅샷
scripts/out.sh --data --pg-dump

# 임베딩 모델
scripts/out.sh --model

# 최초 전체
scripts/out.sh --regen
# (DB 포함 시) scripts/out.sh --regen --pg-dump
```

출력: `~/tmp/citec-kb-*.tar.gz`

버전 추적 (개발 `~/bin/`):

- `.citec_kb_code_version` / `.citec_kb_data_version`
- `.citec_kb_model_name`

---

## 전송

```bash
scp ~/tmp/citec-kb-code-v*.tar.gz \
    ~/tmp/citec-kb-docker-v*.tar.gz \
    ~/tmp/citec-kb-docker-mcp-v*.tar.gz \
    ~/tmp/citec-kb-data-d*.tar.gz \
    ~/tmp/citec-kb-model.tar.gz \
    user@prod:~/
```

---

## 운용: 배포

```bash
cd ~/citec-kb   # 최초 code 추출 후, 또는 기존 프로젝트

scripts/in.sh --help
scripts/in.sh              # 계획 + 확인
scripts/in.sh -y           # 변경분만
scripts/in.sh --code -y
scripts/in.sh --data --restore-pg -y
```

적용 순서: 컨테이너 중지 → 번들 적용 → `docker compose up -d`  
`.env` / `config/models.json` 은 운용 값을 **보존**합니다.

운용 추적 (`~/bin/`):

- `.citec_kb_code_deployed` / `_docker_deployed` / `_docker_mcp_deployed`
- `_docker_keycloak_deployed` / `_data_deployed` / `_model_deployed`

---

## 권장 워크플로

| 변경 내용 | out | in |
|-----------|-----|-----|
| Python/HTML/JS/alembic/MCP 로직 | `--code` | `--code -y` |
| MCP requirements | `--docker-mcp` | `--docker-mcp -y` |
| api/worker Dockerfile·pip | `--code --docker --docker-mcp` | 동일 `-y` |
| raw 코퍼스 (+ 검색 복제) | `--data --pg-dump` | `--data --restore-pg -y` |
| raw 만 (재인덱싱 예정) | `--data` | `--data -y` + ingest/embed |
| 임베딩 모델 | `--model` | `--model -y` |
| Keycloak | `--docker-keycloak` | `--docker-keycloak -y` |
| 최초 구축 | `--regen` (+ `--pg-dump`) | `in.sh -y` (+ `--restore-pg`) |

포트: **web 8572 · api 8573 · postgres 8574 · redis 8575 · keycloak 8576 · mcp 8577**

---

## 운용 `.env` (비밀키는 번들 밖)

```bash
cp .env.example .env
# Fabrix / OpenRouter 키 설정
# 폐쇄망 기본값 (in.sh 가 없으면 추가):
MODELS_HOST_DIR=/home/<user>/citec-kb/models
TRANSFORMERS_OFFLINE=1
HF_HUB_OFFLINE=1
EMBEDDING_MODEL=intfloat/multilingual-e5-base
```

---

## 체크리스트 (최초 폐쇄망)

1. [ ] code + docker + docker-mcp + model 전송·적용  
2. [ ] data (`--pg-dump` 권장) 적용  
3. [ ] `.env` LLM 키  
4. [ ] `curl -s localhost:8573/v1/health`  
5. [ ] 웹 http://localhost:8572 검색 스모크  
6. [ ] (선택) keycloak profile  

---

## 설계 메모

1. 접두사 **`citec-kb-`** — wiki-qa `citec-` 와 구분  
2. `api`/`worker` 는 compose 에 **명시적 `image:`** 태그 (폐쇄망 load 후 build 금지)  
3. model 번들은 **실파일 복사** (symlink tar 금지)  
4. 비밀키·`.env` 는 번들 제외, 운용 보존  
5. `rebuild.sh` 는 개발용 — 운용에서는 `run_stack.sh` / `in.sh` 사용  
