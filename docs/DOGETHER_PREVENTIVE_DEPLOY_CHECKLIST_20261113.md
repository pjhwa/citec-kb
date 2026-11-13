# DOgether 장애예방(`dogether_preventive`) 운영 배포 체크리스트 (2026-11-13)

대상 변경: PR #56 (머지 커밋 `1f8bfcd`). 코드만 바뀌며 Dockerfile·requirements·compose·alembic 변경은 없다.
따라서 `scripts/git_pull_deploy.sh`(git pull + 재시작)로 배포한다. DB 스키마 변경 없음.

> 경로(`~/citec-kb` / `/DATA/jooksan.park/citec-kb`)와 인증 방식(키 헤더)은 운영 환경에서 확인한다.
> 아래 명령은 문서를 근거로 정리한 것이며 운영에서 실행해 검증하지 않았다.

## 0. 사전 확인 (개발 PC)

- [ ] 사내 GitHub(`code.sdsdev.co.kr`) `main`에 머지 커밋 `1f8bfcd`가 반영돼 있다 (개발 쪽 origin은 `github.com/pjhwa/citec-kb`이므로 사내 저장소 반영 방법 별도 확인)
- [ ] 수집기가 쓸 업로드 인증 키가 운영에 준비돼 있다 (인증이 켜진 경우)

## 1. 운영 서버 배포

```bash
cd ~/citec-kb
scripts/backup_postgres.sh               # 권장
scripts/git_pull_deploy.sh --dry-run     # 적용 커밋·위험 파일 확인
scripts/git_pull_deploy.sh -y            # api worker web mcp 재시작
```

- [ ] `--dry-run`에 "이미지 재빌드 필요 파일" 경고가 없다 (있으면 다른 커밋이 섞인 것 — 중단)
- [ ] 스크립트가 헬스체크까지 통과했다 (실패 시 자동 롤백됨)

## 2. 배포 검증

```bash
curl -s http://127.0.0.1:8573/v1/health | head -c 300
docker compose ps
docker compose logs --tail=50 api | grep -i -E "error|traceback"
docker compose exec -T api python -c "from app.routers.external_compat import _resolve_upload_source_type as r; print(r('dogether_preventive'))"
```

- [ ] `/v1/health` 정상
- [ ] api/worker/web/mcp 모두 Up, 로그에 오류 없음
- [ ] 별칭 확인 출력이 `dogether_preventive`

## 3. 수집기 실행 (파트장, 로컬 PC)

- [ ] `npm run inspect` — 연결 확인
- [ ] `npm run dry-run` — 861건 변환 확인
- [ ] `npm start -- --full` — 전체 업로드 (종료 코드 3이면 2단계로 돌아가 서버 반영 확인)
- [ ] 한 번 더 실행해 변경 없는 글이 `skipped`로 나온다
- [ ] `scripts/register-task.ps1` — 매일 11:30 실행 등록

## 4. 적재 확인 (운영 서버)

```bash
docker compose exec -T postgres psql -U citec -d citec_knowledge -c \
 "select status, evidence_grade, count(*) from documents where source_type='dogether_preventive' group by 1,2;"
docker compose exec -T postgres psql -U citec -d citec_knowledge -c \
 "select count(*) from documents where source_type='dogether_preventive' and metadata ? 'swim_ids';"
```

- [ ] `active`가 861건 전후, 등급 A(장애사례·가이드·횡전개)와 B(기타)로 나뉜다
- [ ] `swim_ids`가 있는 글이 67건 안팎

## 5. 그래프 동기화 (Neo4j 엣지)

기존 SWIM 장애 문서(68건)가 이미 있으므로 게시글 적재 후 동기화하면 된다.

```bash
cd /DATA/jooksan.park/citec-kb
scripts/graph_sync.sh --dry-run
screen -S graph-sync
scripts/graph_sync.sh                    # -v 금지 (Neo4j 디버그 로그 폭증)
```

- [ ] 결과 `failed: 0`
- [ ] 엣지 확인:

```bash
docker compose exec -T neo4j cypher-shell -u neo4j -p "<NEO4J_PASSWORD>" \
 "MATCH (p:Document {id:'dogether_preventive:dogether-19267'})-[r:REFERENCES]->(i) RETURN i.id, r.tag;"
# 기대: incident_reports:26091661376, EXTRACTED
```

## 6. 기능 최종 확인

```bash
curl -s "http://127.0.0.1:8573/v1/tickets/26091661376?source_type=incident_reports" \
 | python3 -c "import sys,json; print(json.load(sys.stdin)['related_prevention_posts'])"
```

- [ ] `related_prevention_posts`가 1건 이상
- [ ] `kb_search(section="dogether", query="Undertow 쿠키")`가 글을 찾는다
- [ ] `kb_get_document`로 SWIM 장애 문서를 열면 끝에 "관련 DOgether 장애예방 게시글"이 붙는다
- [ ] `kb_similar_incident` 결과의 `incident_reports` 항목에 목록이 붙는다 (MCP 출력은 개발에서 실행 검증하지 못함)

## 7. 사후 점검

- [ ] 첫 정기 실행(11:30 이후) 로그 확인
- [ ] 역조회 쿼리가 100ms를 넘으면 GIN 인덱스 추가 (861건이면 보통 불필요)
- [ ] 이후 SWIM 장애 문서가 게시글보다 늦게 들어와 엣지가 빠지면 해당 글을 `graph_sync.sh --source-ids <document_id>`로 재동기화 (게시글 내용이 같으면 hash skip될 수 있음)

## 롤백

- 배포 직후 헬스체크 실패는 `git_pull_deploy.sh`가 직전 커밋으로 자동 롤백한다.
- 수동: `~/bin/.citec_kb_git_deployed`의 직전 SHA로 `git checkout` 후 `docker compose restart api worker web mcp`.
- 적재된 게시글은 롤백해도 DB에 남지만 다른 기능에는 영향이 없다.

## 미결 (후속)

- 삭제·숨김 글의 KB 비활성화 정책, SWIM 번호 없는 글(794건) 매칭, `SI_SOURCE_TYPES` 포함 여부는 이번 범위가 아니다.
