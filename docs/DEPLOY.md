# citec-kb 폐쇄망 배포 가이드

개발 시스템에서 번들을 만들고, 사내(폐쇄망) 운용 서버에 적용합니다.  
wiki-qa `out.sh` / `in.sh` 패턴과 동일한 **분리 번들 + 버전 추적 + 변경분만 배포**.

| 스크립트 | 실행 위치 | 역할 |
|----------|-----------|------|
| `scripts/out.sh` | 개발 | 패키징 |
| `scripts/in.sh` | 운용 | 배포 적용 |

---

## 번들 구성 (최적화)

| 번들 | 파일명 | 언제 | 크기 감 |
|------|--------|------|---------|
| **code** | `citec-kb-code-vN.tar.gz` | 앱/웹/설정 수정 | 작음 (일상) |
| **docker** | `citec-kb-docker-vN.tar.gz` | Dockerfile·requirements 변경 | 큼 (드묾) |
| **docker-mcp** | `citec-kb-docker-mcp-vN.tar.gz` | mcp-server 만 변경 | 중 |
| **data** | `citec-kb-data-dN.tar.gz` | raw/seeds 갱신 | 코퍼스 따라 큼 |
| **model** | `citec-kb-model.tar.gz` | 임베딩 모델 최초/교체 | 큼 (1회) |

code 번들은 **호스트 마운트**(`apps/`, `mcp-server/` 등)로 반영 → 이미지 재빌드 없이 `compose up` 재시작만으로 적용됩니다.  
docker 번들은 베이스·Python 의존성 변경 시에만 필요합니다.

버전 카운터 (개발 `~/bin/`):

- `.citec_kb_code_version` / `.citec_kb_data_version`
- `.citec_kb_code_fingerprint` — 소스 변경 없으면 code 재패키지 생략

운용 추적 (`~/bin/`):

- `.citec_kb_code_deployed` / `_docker_deployed` / `_docker_mcp_deployed` / `_data_deployed` / `_model_deployed`

---

## 개발: 패키징

```bash
cd ~/dev/citec-kb   # 또는 실제 레포 경로

# 도움말
scripts/out.sh --help

# 일상 코드만
scripts/out.sh --code

# MCP 이미지만
scripts/out.sh --docker-mcp

# 의존성 변경
scripts/out.sh --code --docker --docker-mcp

# 지식 raw + seeds
scripts/out.sh --data

# 최초 전체 (강제 재생성)
scripts/out.sh --regen

# PG 덤프 포함 데이터
scripts/out.sh --data --pg-dump
```

기본 출력: `~/tmp/citec-kb-*.tar.gz`

---

## 전송

```bash
scp ~/tmp/citec-kb-code-v*.tar.gz \
    ~/tmp/citec-kb-docker-v*.tar.gz \
    ~/tmp/citec-kb-docker-mcp-v*.tar.gz \
    user@prod:~/
# 필요 시 data / model 도 함께
```

---

## 운용: 배포

```bash
# 최초: code 번들에 scripts/in.sh 포함 → 추출 후 사용
# 또는 기존 프로젝트에서:
cd ~/citec-kb

scripts/in.sh --help

# 자동 감지 · 변경분만 (확인 있음)
scripts/in.sh

# 코드만 비대화형
scripts/in.sh --code -y

# 동일 버전 강제
scripts/in.sh --code --force -y

# 계획만
scripts/in.sh -n
```

적용 순서: 컨테이너 중지 → 선택 번들 적용 → `docker compose up -d`  
`.env` / `config/models.json` 은 운용 값을 **보존**합니다.

---

## 권장 워크플로

| 변경 내용 | out | in |
|-----------|-----|-----|
| Python/HTML/JS 수정 | `--code` | `--code -y` |
| MCP 서버만 | `--docker-mcp` | `--docker-mcp -y` |
| requirements / Dockerfile | `--code --docker --docker-mcp` | 동일 `-y` |
| support_history 등 raw | `--data` | `--data -y` (+ 필요 시 재 ingest) |
| 임베딩 모델 | `--model` | `--model -y` |
| 최초 구축 | `--regen` 전부 | `in.sh -y` 전부 |

데이터 배포 후 인덱스 반영:

```bash
cd ~/citec-kb
docker compose exec api python -m app.ingest.cli --raw-dir /data/raw
# 임베딩 배치 (환경에 맞게)
```

---

## 포트 (운용)

| 포트 | 서비스 |
|------|--------|
| 8572 | web |
| 8573 | api |
| 8574 | postgres |
| 8575 | redis |
| 8577 | mcp |

헬스: `curl -s localhost:8573/v1/health | jq .`

---

## 주의

1. 번들 접두사 **`citec-kb-`** — wiki-qa의 `citec-` 와 구분  
2. 비밀키는 번들에 넣지 않음 (`.env` 제외, 운용 서버에서 관리)  
3. docker 번들은 크므로 네트워크 여유 확인  
4. `out.sh --code` 는 fingerprint 동일 시 재생성 생략 → `--force-code` / `--regen` 으로 강제  
