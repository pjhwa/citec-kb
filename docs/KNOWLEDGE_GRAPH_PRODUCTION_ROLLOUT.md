# 지식그래프(Neo4j) 운영 반영 절차

PR #15(`feat(graph): knowledge graph backfill (Neo4j) — schema, extractors,
pipeline, CLI`, 21개 커밋, `worktree-graph-sync-backfill` → `main`)를
운영(`jooksan.park@citectools-vm-001:/DATA/jooksan.park/citec-kb/`)에
반영하는 전체 절차다. 아래 순서를 **그대로**, 건너뛰지 말고 따른다.

## 0. 전제 확인 (작업 전 1회)

- **운영은 폐쇄망(air-gap)이다** (`.env.example` 상단 명시: `Prod (air-gap)`).
  인터넷 접근이 되는 사내 GitHub(`code.sdsdev.co.kr`)에는 붙을 수 있지만,
  공개 인터넷(Docker Hub, Neo4j 플러그인 레지스트리 등)에는 붙지 못한다.
  → 이 사실이 2단계·6단계에서 두 가지 특별 처리를 요구한다.
- **이번 변경은 "코드만" 배포가 아니다.** `requirements.txt`에 `neo4j==5.26.0`이
  추가됐고 `docker-compose.yml`에 신규 서비스 `neo4j`가 추가됐다. `docs/DEPLOY.md`의
  `scripts/git_pull_deploy.sh`는 diff에 `requirements.txt`/`docker-compose.yml`
  변경이 섞이면 스스로 중단하도록 되어 있다 — 즉 이번 배포는 **반드시
  `scripts/out.sh` + `scripts/in.sh` (이미지 재빌드) 경로**를 써야 한다.
  (`docs/DEPLOY.md` "권장 워크플로" 표의 "api/worker Dockerfile·pip" 행과 동일 케이스.)
- **`neo4j:5.26-community` 이미지는 `scripts/out.sh`의 `CORE_IMAGES`에 아직
  없다** (`scripts/out.sh:92`: `api worker nginx redis pgvector` 5개뿐).
  이 문서의 2·4단계가 이 이미지를 **수동으로** 함께 옮긴다. (`out.sh`/`in.sh`
  자체를 고치는 건 이 PR의 범위가 아니므로 여기서는 1회성 수동 절차로 처리한다.
  반복 배포가 예상되면 추후 `out.sh`의 `CORE_IMAGES`에 `neo4j:5.26-community`를
  추가하는 별도 작업을 고려할 것.)
- **`NEO4J_PLUGINS: '["graph-data-science"]'`(`docker-compose.yml:36`)는 컨테이너
  기동 시 공개 인터넷에서 GDS 플러그인을 내려받는다 — 폐쇄망 운영에서는
  반드시 실패한다.** 현재 `app.graph` 코드(이 PR 범위)는 GDS의 어떤 프로시저도
  호출하지 않는다 (`SIMILAR_TO` 임계값 비교는 Python 코드에서 수행, Cypher
  단계에서 GDS 불필요). 따라서 **운영 최초 반영 시 이 환경변수를 비워 둔다**
  (5단계에서 구체적으로 다룸). GDS가 실제로 필요한 시점(2단계: Leiden
  커뮤니티 탐지 등)이 오면 그때 플러그인 jar를 사내 미러/수동 전송으로
  별도 반입한다.
- PR 상태 확인 (머지 직전 1회 더):
  ```bash
  gh pr view 15 --json state,mergeable
  # 기대: {"mergeable":"MERGEABLE","state":"OPEN"}
  ```

---

## 1. PR 머지 (개발 환경, `main`)

```bash
cd ~/dev/citec-kb   # main 체크아웃 (worktree 아님)
git fetch origin
git checkout main
git pull --ff-only origin main

gh pr checks 15       # CI 그린 확인 (실패 있으면 머지하지 말고 원인 조치)
gh pr merge 15 --merge # squash/rebase 아님 — 21개 작업 커밋 이력을 보존
```

머지 후:

```bash
git pull --ff-only origin main
git log --oneline -3     # 머지 커밋이 HEAD인지 확인
```

**머지 후 워크트리 정리** (PR용으로 만든 `worktree-graph-sync-backfill`은
더 이상 필요 없음):

```bash
git worktree list
git worktree remove .claude/worktrees/graph-sync-backfill   # 경로는 실제 리스트 기준으로
git worktree prune
git branch -d worktree-graph-sync-backfill
```

---

## 2. 개발: 배포 번들 패키징

main의 `~/dev/citec-kb`에서 (1단계 이후 상태 그대로):

```bash
cd ~/dev/citec-kb

# code + docker(api/worker/nginx/redis/pgvector 이미지 재빌드) 번들
scripts/out.sh --code --docker
```

출력 확인:

```bash
ls -la ~/tmp/citec-kb-code-v*.tar.gz ~/tmp/citec-kb-docker-v*.tar.gz
```

**neo4j 이미지는 `out.sh`가 다루지 않으므로 수동으로 동봉한다** (0단계 근거):

```bash
docker pull neo4j:5.26-community
docker save neo4j:5.26-community | gzip > ~/tmp/citec-kb-neo4j-image.tar.gz
```

> `docker-compose.yml`은 `neo4j` 서비스를 `image: neo4j:5.26-community`로
> 직접 참조한다 (citec 자체 빌드 이미지가 아님) — 그래서 `docker save`로
> 그대로 떠서 옮기면 된다. 운영에 `docker load` 하면 태그가 그대로 보존되어
> `docker compose up -d`가 바로 찾는다.

---

## 3. 전송

```bash
scp ~/tmp/citec-kb-code-v*.tar.gz \
    ~/tmp/citec-kb-docker-v*.tar.gz \
    ~/tmp/citec-kb-neo4j-image.tar.gz \
    jooksan.park@citectools-vm-001:~/tmp/
```

(`docs/DEPLOY.md`의 전송 관례와 동일하게 사람이 직접 `scp`한다.)

---

## 4. 운영: 이미지/코드 적용

```bash
ssh jooksan.park@citectools-vm-001
cd /DATA/jooksan.park/citec-kb

# neo4j 이미지 먼저 로드 (in.sh 가 다루지 않으므로 수동)
gunzip -c ~/tmp/citec-kb-neo4j-image.tar.gz | sudo docker load
docker images | grep neo4j   # neo4j   5.26-community   ... 확인

# code + docker 번들 적용 (계획만 먼저 확인)
scripts/in.sh --code --docker -n
# 계획이 기대한 그대로면 (compose 변경 포함 — neo4j 서비스 신규) 실제 적용:
scripts/in.sh --code --docker -y
```

이 단계는 아직 **컨테이너를 새 compose 구조로 올리지 않는다** (`in.sh`는
파일/이미지 반영과 `.env`/`config/models.json` 보존까지만 — 실제 `up`은
5단계에서 환경변수 설정 후 진행).

---

## 5. 운영 `.env` 수정 (Neo4j 변수 추가)

```bash
cd /DATA/jooksan.park/citec-kb
grep -n NEO4J .env   # 아직 없으면 아래 추가
```

`.env`에 다음 두 줄을 추가한다 (비밀번호는 운영 정책에 맞게 **반드시
`citecgraph` 기본값에서 변경**):

```bash
NEO4J_URI=bolt://neo4j:7687
NEO4J_PASSWORD=<운영용 강한 비밀번호로 교체>
```

**GDS 플러그인 비활성화 (0단계 근거 — 폐쇄망에서 공개 인터넷 접근 불가,
현재 코드도 GDS를 쓰지 않음).** `docker-compose.yml:36`의
`NEO4J_PLUGINS: '["graph-data-science"]'`를 운영에서만 비우기 위해,
`docker-compose.yml`을 직접 고치지 않고(이미 `in.sh`로 반영된 운영 파일을
건드리면 다음 배포 때 diff가 꼬임) **override 파일**로 처리한다:

```bash
cat > docker-compose.override.yml <<'EOF'
services:
  neo4j:
    environment:
      NEO4J_PLUGINS: '[]'
EOF
```

`docker compose`는 `docker-compose.yml` + `docker-compose.override.yml`을
자동으로 함께 읽으므로(파일명 자동 감지), 이후 모든 `docker compose` 명령에
별도 `-f` 지정 없이 적용된다. **이 override 파일은 `.gitignore`에 추가해
배포 번들 적용 시 덮어써지지 않게 한다**:

```bash
echo "docker-compose.override.yml" >> .gitignore
```

---

## 6. 운영: 기동 및 스키마 확인

```bash
cd /DATA/jooksan.park/citec-kb
docker compose up -d
docker compose ps    # neo4j 포함 전부 Up/healthy 확인 (neo4j healthcheck 10회 재시도, 최대 ~100초)
```

`api` 컨테이너 entrypoint가 기동 시 자동으로 `alembic upgrade head`를
실행한다(`docs/DEPLOY.md` 명시) — `graph_sync_state` 테이블이 이 시점에
자동 생성된다. 수동 `alembic` 실행은 필요 없다. 확인만:

```bash
docker compose exec -T postgres psql -U citec -d citec_knowledge \
  -c "\d graph_sync_state"   # 테이블 존재 확인
```

Neo4j 유니크 제약은 `app.graph.pipeline.build_neo4j_client()`가 최초 호출
시 `ensure_constraints()`로 자동 생성한다(코드에 이미 포함, 7단계 dry-run이
첫 호출을 트리거). 7단계 이후 아래로 확인:

```bash
docker compose exec -T neo4j cypher-shell -u neo4j -p "<NEO4J_PASSWORD 값>" \
  "SHOW CONSTRAINTS;"
# Document.id / FailureBucket.id / Component.canonical_name / BusinessEntity.id
# 4개가 보여야 함
```

API 헬스체크:

```bash
curl -s localhost:8573/v1/health
```

---

## 7. 운영: 최초 백필

**순서를 건너뛰지 말 것** — dry-run → 소규모 실측 → 전체.

```bash
cd /DATA/jooksan.park/citec-kb

# 1) dry-run: 대상 건수만 확인, 쓰기 없음
scripts/graph_sync.sh --dry-run
# 운영 문서/failure_bucket 건수가 합리적인 범위인지 눈으로 확인
# (개발 미러 기준으로는 114,930건/8건이었음 — 운영은 더 최신일 수 있음)

# 2) 소규모 실측 (예: 임의 500건, source-ids 모르면 생략하고 바로 3번으로
#    가도 되지만, 첫 실행이므로 중간 점검을 권장)
#    → sync_cli 에 건수 제한 플래그가 없으므로, 이 단계는 선택사항.
#    대신 3번을 screen/tmux 안에서 실행해 중간에 Ctrl-C로 멈춰 확인 가능
#    (멈춰도 안전 — hash 기반 멱등성으로 재실행 시 처리분은 스킵됨).

# 3) 전체 백필 — screen/tmux 필수 (SSH 끊김 대비), -v 절대 금지
#    (-v 는 Neo4j bolt 프로토콜 DEBUG 로그를 초당 수백 KB씩 쌓음 — 개발
#    검증 때 실제로 겪은 문제, REPORT.md 참조)
screen -S graph-backfill
scripts/graph_sync.sh
# Ctrl-A D 로 detach, 나중에: screen -r graph-backfill
```

**진행 확인** (다른 세션에서, 백필 도는 동안):

```bash
docker compose exec -T neo4j cypher-shell -u neo4j -p "<NEO4J_PASSWORD 값>" \
  "MATCH (d:Document) RETURN count(d);"
```

**완료 후 결과가 기대와 맞는지 확인**:

```bash
scripts/graph_sync.sh --dry-run
# synced 건수 == 직전 --dry-run의 documents 건수와 일치해야 함 (failed: 0)
```

개발 검증(`artifacts/citec-kb-knowledge-graph-backfill-20261008/REPORT.md`)
기준 114,930건에 약 1시간 40분 걸렸다 — 운영 문서 수가 비슷하면 비슷한
시간을 예상한다. **중단돼도 데이터 손상 없음** — hash 기반 skip으로 재실행
시 이미 처리된 문서는 자동 스킵된다(개발 검증에서 실제로 중단→재개로
증명됨).

---

## 8. cron 등록 (증분 반영 자동화)

**먼저 현재 운영 crontab을 확인해 실제 비어있는 시간대를 고른다** (이
문서 작성 시점 기준으로 알고 있는 시간대와 실제 운영 crontab이 다를 수
있으므로, 아래 제안 시각을 쓰기 전에 반드시 재확인):

```bash
crontab -l
```

`ingest_and_embed.sh`(증분 수집) 이후, 다른 동기화 job들과 겹치지 않는
빈 시간대를 고른다 — graph_sync는 Postgres의 최신 데이터를 읽으므로 그날의
`ingest_and_embed`/`confluence_sync`/`map_sync`가 끝난 뒤에 돌아야 의미가
있다. 비어있는 시간대가 보이면 (예: 14:00) 아래 패턴으로 등록:

```bash
crontab -e
```

다음 줄을 추가 (기존 job들과 동일한 `flock` + 파일 로그 패턴):

```cron
0 14 * * * /usr/bin/flock -n /tmp/citec-kb-graph-sync.lock /DATA/jooksan.park/citec-kb/scripts/graph_sync.sh >> /DATA/jooksan.park/citec-kb/logs/cron_graph_sync.log 2>&1
```

**주의: `-v` 플래그를 cron 라인에 절대 넣지 말 것** (7단계 로그 폭주 사유
동일 적용).

등록 후 다음날 로그로 1회차 정상 동작 확인:

```bash
tail -50 /DATA/jooksan.park/citec-kb/logs/cron_graph_sync.log
```

---

## 9. 검증 체크리스트 (반영 완료 기준)

- [ ] `gh pr view 15 --json state` → `MERGED`
- [ ] 운영 `docker compose ps` — 전 서비스(neo4j 포함) `Up`/`healthy`
- [ ] `docker compose exec -T postgres psql ... "\d graph_sync_state"` 성공
- [ ] `SHOW CONSTRAINTS` — 4개 제약 존재
- [ ] `scripts/graph_sync.sh --dry-run` 결과 `failed: 0`, synced 건수가
      직전 전체 백필과 일치
- [ ] `curl -s localhost:8573/v1/health` 정상 (그래프 추가가 기존
      검색/API에 영향 없음을 재확인)
- [ ] `crontab -l`에 `graph_sync.sh` 라인 존재, 다음날 로그 1회 정상 실행
      확인
- [ ] `docker-compose.override.yml`이 `.gitignore`에 등록돼 다음 `in.sh`
      적용 시 덮어써지지 않음

---

## 10. 문제 발생 시 되돌리기

이번 변경은 **기존 테이블/기존 서비스를 전혀 건드리지 않는 순수 추가**다
(`graph_sync_state` 신규 테이블 1개, `neo4j` 신규 서비스 1개). 기존
PostgreSQL FTS+pgvector 검색/API는 그래프 작업과 완전히 분리돼 있으므로,
문제가 생기면 그래프 쪽만 끄면 된다:

```bash
# cron 비활성화
crontab -e   # graph_sync.sh 줄 주석 처리 또는 삭제

# neo4j 서비스만 중지 (api/worker/web/postgres 등은 영향 없음)
docker compose stop neo4j

# 완전히 걷어내고 싶다면 (데이터까지 삭제 — 되돌릴 수 없음, 신중히)
docker compose down neo4j
docker volume rm citec-kb_neo4jdata   # 실제 볼륨명은 docker volume ls 로 확인
```

`graph_sync_state` 테이블은 다른 테이블을 참조하지 않는 독립 테이블이라
그냥 두어도 기존 기능에 영향이 없다 — alembic 다운그레이드가 필요한
상황은 이번 변경 범위에서는 발생하지 않는다.
