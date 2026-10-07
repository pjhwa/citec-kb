# citec-kb 폐쇄망 배포 가이드

wiki-qa `out.sh` / `in.sh` 와 같은 **분리 번들 + 버전 추적 + 변경분만 배포** 패턴입니다.  
citec-kb 는 **multi-service + Postgres(pgvector)** 이므로, wiki-qa 의 `db/vec.db` 와 달리 **인덱스는 PG 볼륨**에 있습니다.

운용 서버가 사내 GitHub(`code.sdsdev.co.kr`)에 접속 가능해진 뒤부터는, **코드만 바뀌는 배포**(Dockerfile·requirements·pyproject·package.json·docker-compose.yml 변경이 없는 경우)는 `scripts/git_pull_deploy.sh` 로 `out.sh`/`in.sh` 번들 왕복 없이 바로 적용할 수 있습니다. 이미지 재빌드가 필요한 변경(Dockerfile/의존성/compose 구조)은 여전히 `out.sh`/`in.sh`를 사용합니다.

| 스크립트 | 실행 위치 | 역할 |
|----------|-----------|------|
| `scripts/git_pull_deploy.sh` | 운용 | **코드만** 배포 (git pull → 재시작, 이미지 재빌드 없음) — 신규 |
| `scripts/out.sh` | 개발 | 패키징 (코드/이미지/데이터/모델 번들) |
| `scripts/in.sh` | 운용 | 번들 배포 적용 (이미지 재빌드 필요 시, 최초 구축, 데이터/모델 배포) |

---

## 코드만 배포: `git_pull_deploy.sh` (신규, 운용 전용)

**전제:** `docker-compose.yml`의 `api`/`worker`/`web`/`mcp` 서비스는 `apps/api/app`, `apps/api/alembic`,
`apps/worker/app`, `apps/web/public`, `mcp-server/server.py`를 호스트 bind-mount 합니다 — 즉 **Python/HTML/JS/alembic
마이그레이션/MCP 로직 변경은 이미지 재빌드 없이 `git pull` + `docker compose restart` 만으로 반영**됩니다.
alembic 마이그레이션은 `api` 컨테이너 entrypoint(`scripts/api-entrypoint.sh`)가 기동할 때마다 자동으로
`alembic upgrade head`를 실행하므로, 재시작만으로 스키마도 함께 따라옵니다.

**이 스크립트가 다루지 않는 것:** Dockerfile / `requirements*.txt` / `pyproject.toml` / `package*.json` /
`docker-compose.yml` 변경 — 즉 이미지를 다시 빌드해야 하는 변경. 이런 변경은 지금처럼 `out.sh` + `in.sh`를
사용합니다. `git_pull_deploy.sh`는 pull 대상 diff에 이런 파일이 섞여 있으면 기본적으로 **중단**하고
(`--force-image-diff`로만 무시 가능) 안내 메시지를 출력합니다.

```bash
cd ~/citec-kb

scripts/git_pull_deploy.sh --help
scripts/git_pull_deploy.sh --dry-run     # fetch 후 적용될 커밋/위험 파일만 확인, pull/재시작 없음
scripts/git_pull_deploy.sh -y            # 기본: api worker web mcp 재시작

# 특정 서비스만
scripts/git_pull_deploy.sh --services "api worker" -y

# 재시작은 나중에 수동으로 (pull 만)
scripts/git_pull_deploy.sh --no-restart -y
```

**안전장치**

- `origin`이 사내 GitHub(`code.sdsdev.co.kr`)를 가리키는지 확인 후 아니면 중단
- 작업 트리가 dirty(커밋 안 된 변경 존재)하면 중단 — 임의로 버리지 않음
- `git pull --ff-only`만 사용 — 로컬이 원격과 갈라졌으면 중단 (수동 확인 필요)
- 재시작 후 헬스체크(`/v1/health`, web, mcp 포트, worker 컨테이너 상태) 실패 시 **배포 직전 커밋으로 자동
  롤백 + 재시작**. 롤백 후에도 실패하면 자동 복구를 멈추고 수동 개입을 요청 (`--no-rollback`으로 자동
  롤백 자체를 끌 수도 있음)
- 동시 실행 방지 (flock)

**최초 1회 — 저장소 연결:**

- 운용 서버에 `~/citec-kb`가 **전혀 없다면**: 그냥 `git clone`으로 새로 만듭니다.
- 운용 서버에 `~/citec-kb`가 **이미 있다면**(지금까지 `out.sh`/`in.sh` 번들로만 배포해서 `.git` 이력이
  없고, DB·컨테이너가 이미 운영 중인 경우) — `git_pull_deploy.sh --attach-git --remote-url <URL>`을
  사용합니다:
  ```bash
  cd ~/citec-kb
  scripts/git_pull_deploy.sh --attach-git \
    --remote-url git@code.sdsdev.co.kr:jooksan/citec-kb.git
  ```
  `git init` → `remote add` → `fetch` 후 **현재 디렉토리 파일과 `origin/main`의 차이(파일 목록·통계)를
  화면에 보여주고**, 정확히 `ATTACH`를 입력해야만 실제로 덮어씁니다(그 외 입력 시 `.git`을 다시 지우고
  원상복구). `-y`로도 이 확인은 건너뛸 수 없습니다 — 일회성·고위험 작업이라 항상 diff를 직접 보고
  진행하도록 강제합니다. `.env`/`data/`/`models/`/`logs/`는 `.gitignore` 대상이라 영향받지 않습니다.
  **이 단계는 컨테이너를 재시작하지 않습니다** — 반영하려면 이후 `--attach-git` 없이 스크립트를 다시
  실행(또는 수동 재시작)해야 합니다.

  운영 배포 시점과 사내 GitHub `main` 사이에 변경이 많이 쌓여 있었다면, 보여지는 diff도 그만큼 커집니다
  — 진행 전 `scripts/backup_postgres.sh`로 DB를 먼저 백업하고, 트래픽이 적은 시간대에 하는 것을
  권장합니다.

배포 이력은 `~/bin/.citec_kb_git_deployed`에 마지막으로 적용된 커밋 SHA가 기록됩니다.

---

## 운영 데이터를 개발로 가져오기: `prod_pull_export.sh` / `prod_pull_apply.sh` (신규)

**목적이 다른 두 도구를 구분할 것:**

| 스크립트 | 방향 | 성격 |
|---|---|---|
| `scripts/sync_manifest.sh`/`sync_export.sh`/`sync_apply.sh` | 운영→개발 | **증분**. 신규/변경분만, 삭제는 무시(개발 전용 데이터가 그대로 남음). `documents`/`document_sections`/`chunks`/`checkitems`/`issue_frames`/`failure_buckets` 6테이블 + `data/raw`만 |
| `scripts/prod_pull_export.sh`/`prod_pull_apply.sh` | 운영→개발 | **전체 동일화**. Postgres 전체(pg_dump, `embeddings`/`sources`/`insights` 등 전부 포함) + `data/raw` 전체를 통째로 가져와 개발을 운영과 완전히 동일하게 맞춤. **개발 전용 DB 행·파일은 삭제됨** |

테스트를 위해 "운영과 완전히 동일한 상태"가 필요할 때는 후자를 씁니다.

```bash
# 운영에서 실행 — 전체 DB + data/raw 번들 생성
scripts/prod_pull_export.sh
# data/raw 는 개발에 이미 동일 코퍼스가 있어 필요 없다면:
scripts/prod_pull_export.sh --no-raw

# 전송 (out.sh/in.sh와 동일 패턴 — 사람이 scp)
scp user@ops:~/tmp/citec-kb-prod-full-*.tar.gz ~/tmp/

# 개발에서 실행 — 먼저 내용만 확인
scripts/prod_pull_apply.sh --dry-run ~/tmp/citec-kb-prod-full-*.tar.gz
# 실제 적용
scripts/prod_pull_apply.sh -y ~/tmp/citec-kb-prod-full-*.tar.gz
```

**`prod_pull_apply.sh`가 하는 일:**
1. (기본) 적용 전 현재 개발 DB를 `data/backups/pre-prod-pull-<TS>.sql.gz`로 백업 (`--no-backup`으로 생략 가능 — 복구용 안전망이므로 웬만하면 켜 둘 것)
2. `DROP SCHEMA public CASCADE` 후 운영 덤프로 전체 복원 (`in.sh`의 `--restore-pg`와 동일한 절차)
3. `data/raw`를 `rsync -a --delete`로 완전히 미러링 (`--no-raw`로 생략 가능 — 개발 전용 raw 파일은 이때 삭제됨)
4. `api`/`worker` 재시작 (`--no-restart`로 생략 가능; alembic은 재시작 시 자동 적용)

반복적으로(테스트 데이터 새로고침 목적) 쓸 수 있게 `-y`만으로 확인을 생략할 수 있지만, 백업은 별도 플래그 없이는 항상 실행됩니다.

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
| `models/` (`MODELS_HOST_DIR=./models`) | api | HF 캐시 (**이 레포 전용**, 타 프로젝트 경로 금지) |

**이미지 재빌드가 필요한 것:** requirements / Dockerfile / torch 등.

### 데이터 vs 검색 인덱스 (wiki-qa 와의 차이)

| | wiki-qa | citec-kb |
|--|---------|----------|
| 지식 파일 | `wiki/` + `raw/` | `data/raw` |
| 임베딩/FTS | `db/vec.db` (data 번들에 포함) | **Postgres `pgdata` 볼륨** |
| 최초 검색 가능 상태 | data 배포만으로 OK | **PG 덤프 복원** 또는 **ingest + embed** |

```bash
# A) 개발 DB 복제 (권장 — 검색 즉시 가능)
scripts/out.sh --data --pg-dump          # raw + dump
# 또는 인덱스만 (raw 이미 운영에 있을 때):
scripts/out.sh --code --pg-only

# 운용 — 반드시 --restore-pg (스키마 DROP 후 api 기동 전 복원)
scripts/in.sh --code --data --restore-pg -y

# B) 파일만 배포 후 재인덱싱
scripts/out.sh --data
scripts/in.sh --data -y
docker compose exec api python -m app.ingest.cli --raw-dir /data/raw
docker compose exec api python -m app.embed.cli
```

### 운영 깨진 PG restore 복구 (code + dump 만)

원인: api(alembic) 기동 **후** dump를 얹으면 CREATE/FK ERROR 폭주.

```bash
# 개발
scripts/out.sh --code --pg-only
scp ~/tmp/citec-kb-code-v*.tar.gz ~/tmp/citec-kb-data-d*.tar.gz user@prod:~/

# 운영
cd ~/citec-kb
# 권장: SSH 끊김 대비 screen/tmux
screen -S kb-restore   # 또는 tmux
scripts/in.sh --code --data --restore-pg -y
# 내부: postgres only → DROP SCHEMA → quiet restore(수 분~수십 분) → 건수 검증 → 전체 up
```

**대용량 덤프(~200MB gzip / embeddings 5만+) 복원은 10~40분 걸릴 수 있습니다.**  
터미널이 조용해도 정상이며, 30초마다 heartbeat 가 출력됩니다.  
다른 세션에서: `sudo docker logs -f citec-kb-postgres-1` (checkpoint / lock 메시지).

복원 중 SSH가 끊기면 프로세스만 죽을 수 있음 → `screen`/`tmux` 사용 권장.  
중간 실패 시 스키마가 반쯤만 있을 수 있으므로 **같은 명령으로 재실행**하면 됩니다.

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

| 변경 내용 | 방법 |
|-----------|------|
| Python/HTML/JS/alembic/MCP 로직 (사내 GitHub push 후) | `git_pull_deploy.sh -y` (권장, 이미지 재빌드 없음) |
| Python/HTML/JS/alembic/MCP 로직 (git 접속 불가·최초 구축) | out `--code` → in `--code -y` |
| MCP requirements | out `--docker-mcp` → in `--docker-mcp -y` |
| api/worker Dockerfile·pip | out `--code --docker --docker-mcp` → in 동일 `-y` |
| raw 코퍼스 (+ 검색 복제) | out `--data --pg-dump` → in `--data --restore-pg -y` |
| raw 만 (재인덱싱 예정) | out `--data` → in `--data -y` + ingest/embed |
| 임베딩 모델 | out `--model` → in `--model -y` |
| Keycloak | out `--docker-keycloak` → in `--docker-keycloak -y` |
| 최초 구축 | out `--regen` (+ `--pg-dump`) → in `-y` (+ `--restore-pg`) |

포트: **web 8572 · api 8573 · postgres 8574 · redis 8575 · keycloak 8576 · mcp 8577**

---

## 장애 진단 — 파일 로그 (`citec-kb/logs/`)

`api`/`worker` 컨테이너는 콘솔(`docker compose logs`)뿐 아니라 호스트의 `citec-kb/logs/`에도
로테이션 파일 로그를 남깁니다 (10MB × 5개 보관, `LOG_DIR` 환경변수로 경로 변경 가능).
컨테이너를 오래 붙잡고 있지 않아도, `docker compose logs`가 롤오버되어 과거 기록이 사라졌어도
이 디렉토리에서 바로 확인할 수 있습니다.

```bash
tail -f logs/api.log       # api 실시간
tail -f logs/worker.log    # worker 실시간
grep -i error logs/api.log | tail -50
```

`docker-compose.yml`에 볼륨 마운트가 없던 버전(v18 이전 code 번들)에서는 이 디렉토리가 생기지
않습니다 — `--code -y`로 최신 code 번들을 적용하면 다음 `docker compose up -d api worker`부터
자동으로 생성됩니다.

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

### 임베딩 모델 (프로젝트 전용)

- **위치:** 항상 `citec-kb/models/` (compose 기본 `MODELS_HOST_DIR=./models`)
- **금지:** `citec-wiki-qa` 등 다른 레포 경로를 `MODELS_HOST_DIR` 로 지정·심볼릭 링크
- **개발 준비:** 다른 곳에 있는 HF 캐시는 **복사** 후 사용

```bash
# 개발 레포에서 (예시)
mkdir -p models
rsync -a /path/to/hf-cache/hub/ models/hub/
# 패키징
scripts/out.sh --model
```

`out.sh --model` 은 `SOURCE_PATH/models` 만 읽습니다. 레포 밖·`citec-wiki-qa` 경로는 거부합니다.


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
5. `rebuild.sh` 는 개발용 — 운용에서는 `run_stack.sh` / `in.sh` / `git_pull_deploy.sh` 사용  
6. `git_pull_deploy.sh` 는 **코드만** 대상 — `.env`/`data/`/`models/`는 gitignore 라 pull 로 건드리지 않음  
