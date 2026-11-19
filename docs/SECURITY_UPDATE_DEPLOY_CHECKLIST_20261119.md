# 보안 패치 운영 배포 체크리스트 (2026-11-19, 번들 v57)

PR #64 (nginx 1.30.7 외 전 구성요소 갱신) 운영 반영용. 이미지·requirements가 바뀌었으므로
`git_pull_deploy.sh`로는 **반영 불가** — `out.sh`/`in.sh` 번들 경로를 사용한다.

## 변경 요약

| 구성요소 | 이전 | 이후 |
|---|---|---|
| nginx | 1.27.5 | 1.30.7 |
| PostgreSQL / pgvector 확장 | 16.14 / 0.8.5 | 16.16 / 0.8.7 |
| Redis | 7.4.9 | 7.4.12 |
| Neo4j | 5.26.31 | 5.26.33 |
| fastapi / starlette | 0.115.12 / 0.46.2 | 0.165.0 / 1.10.0 (**starlette 메이저 상승**) |
| python-multipart / PyJWT / python-dotenv | 0.0.24 / 2.10.1 / 1.1.0 | 0.0.34 / 2.15.1 / 1.2.4 |

| Keycloak (선택, `--profile keycloak`) | 26.0.8 | 26.8.2 (치명적 SAML 서명 우회 포함 CVE 33건 수정) |

Keycloak은 dev/test용 IdP(`start-dev`, 볼륨 없음). 컨테이너 재생성 시 런타임에 만든 사용자·설정은 사라지고 `realm-citec.json` import 내용만 남는다. 운영에서 Keycloak을 쓰는 경우 별도 `citec-kb-docker-keycloak-v57.tar.gz` 반입·`docker load` 필요(운영이 실제 IdP를 쓰면 이 번들은 불필요).

## 반입 파일 (dev `~/tmp`, 체크섬은 `citec-kb-v57.sha256`)

| 파일 | 용도 |
|---|---|
| `citec-kb-code-v57.tar.gz` | 코드·compose·in.sh |
| `citec-kb-docker-v57.tar.gz` | api worker nginx redis pgvector **neo4j** |
| `citec-kb-docker-mcp-v57.tar.gz` | mcp |

## 배포 순서

1. **사전 백업 (필수)**: `docker exec <postgres> pg_dump -U citec -Fc citec_knowledge > backup_pre_v57.dump`
   (Neo4j는 patch 상승이라 in-place 호환. 필요 시 정지 후 `neo4j-admin database dump`)
2. 파일 반입 후 `sha256sum -c citec-kb-v57.sha256`
3. `scripts/in.sh` 로 code + docker + docker-mcp 적용 (compose가 새 이미지로 재생성)
4. **pgvector 확장 갱신 (in.sh가 자동 수행하지 않음)** — 서비스별 DB 전체:
   ```bash
   for d in $(docker exec <postgres> psql -U citec -Atc "select datname from pg_database where not datistemplate and datname<>'postgres'"); do
     docker exec <postgres> psql -U citec -d $d -c "ALTER EXTENSION vector UPDATE;"
   done
   ```
   patch 상승이라 reindex 불필요. 갱신 전까지는 0.8.5로 계속 동작.
5. 컨테이너 기동 순서: postgres/redis/neo4j healthy → api → worker/web/mcp

## 검증

- [ ] `docker ps` 전 컨테이너 healthy, 이미지 태그가 위 표와 일치
- [ ] `docker exec <web> nginx -v` → 1.30.7, `<postgres> select version()` → 16.16, `select extversion from pg_extension where extname='vector'` → 0.8.7
- [ ] `curl :8572/v1/health` ok (documents_count·alembic_revision이 배포 전과 동일)
- [ ] web `/`, `/docs/`, `/api/docs`, `/openapi.json` 200
- [ ] 검색 1건 (벡터 포함 `vector=True`), `kb_graph_status` consistent
- [ ] 파일 업로드(`/api/upload`) 실제 1건 — python-multipart 0.0.34 확인 (dev에서는 파싱만 확인, 실업로드 미검증)
- [ ] AUTH_MODE 사용 시 JWT 로그인/토큰 검증 — PyJWT 2.15.1 확인 (dev는 AUTH_MODE=off로 검증, **운영 인증 경로 미검증**)
- [ ] api/worker/mcp 로그에 error·traceback 없음

## 롤백

- 이미지: 이전 번들(v54) `docker load` 후 compose 재생성
- DB: 사전 백업 dump로 복원. 확장 0.8.7은 다운그레이드 경로가 없으므로 5번은 검증 완료 후 수행 권장
- 코드: `git_pull_deploy.sh` 또는 v54 code 번들

## 주의

- starlette 0.x→1.x 메이저 상승: dev에서 단위테스트 1075 pass, 라우트·검색·그래프 스모크는 확인했으나 운영 전용 설정(AUTH_MODE, 외부 LLM) 경로는 운영에서 처음 실행됨.
- dev에서 `test_backup_manager` 1건이 호스트 pg_dump 부재로 실패(기존 이슈, 변경 무관; CI에서는 통과).
