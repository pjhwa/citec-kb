# DOgether·LOOKIN ↔ SWIM 내용 매칭 운영 체크리스트 (2026-11-14)

대상 변경: PR #60(DOgether 내용 매칭, 마이그레이션 `20261113_0025`), PR #61(날짜 추출 보강), PR #62(LOOKIN 기술지원 매칭 +
개발·운영 동기화, 마이그레이션 `20261114_0026`). main `5c79f1c` 이상.

- 스키마 변경은 **새 테이블 4개 추가뿐**이다(`dogether_swim_matches`/`_runs`, `lookin_swim_matches`/`_runs`). 기존 테이블·그래프·API 응답은 바뀌지 않는다.
- 마이그레이션은 api 컨테이너 기동 시 자동 적용된다(`scripts/api-entrypoint.sh`) → `git_pull_deploy.sh`(pull + 재시작)로 충분하다.
- **auto 연결은 꺼져 있다.** 이 체크리스트는 review 후보를 만들고 사람이 검토하는 데까지다. 기준값(T1/M)과 auto 켜기는 별도 결정이다.

> 경로(`~/citec-kb` 등)와 DB 이름은 운영 환경에서 확인한다. 명령은 개발 환경 실행 결과를 근거로 정리했으며 운영에서 실행해 검증하지 않았다.
> 기준 수치(개발): DOgether 번호 없는 글 535건 → review 1,551행 / LOOKIN 기술지원 510건 → review 1,097행. `--dry-run` 3분 안팎.

## 0. 사전 확인

- [ ] 사내 GitHub `main`에 `5c79f1c` 이상이 반영돼 있다 (개발 origin은 `github.com/pjhwa/citec-kb`)
- [ ] PR #60의 마이그레이션(`20261113_0025`)을 이미 적용했는지 확인했다 (안 했어도 이번에 함께 적용되며 둘 다 새 테이블만 만든다)

## 1. 백업·배포

```bash
cd ~/citec-kb
scripts/backup_postgres.sh               # 권장
scripts/git_pull_deploy.sh --dry-run     # 적용 커밋·위험 파일 확인
scripts/git_pull_deploy.sh -y            # api worker web mcp 재시작
```

- [ ] 백업이 끝났다
- [ ] `--dry-run`에 "이미지 재빌드 필요 파일" 경고가 없다 (있으면 다른 커밋이 섞인 것 — 중단)
- [ ] 스크립트 사후 점검이 통과했다 (`alembic current`가 head, 헬스체크 정상. 실패 시 자동 롤백)

## 2. 배포 검증

```bash
docker compose exec -T api alembic current        # 20261114_0026 (head)
docker compose exec -T postgres psql -U citec -d citec_knowledge -c "\dt *swim*"
docker compose logs --tail=50 api | grep -i -E "error|traceback"
```

- [ ] `alembic current`가 `20261114_0026 (head)`
- [ ] 테이블 4개가 보인다: `dogether_swim_matches`, `dogether_swim_match_runs`, `lookin_swim_matches`, `lookin_swim_match_runs`
- [ ] 로그에 오류 없음

## 3. 운영 현황 내보내기·개발과 비교

```bash
docker compose exec -T api python -m app.matching.sync_matches --export /tmp/prod_export.jsonl
docker compose cp api:/tmp/prod_export.jsonl ./prod_export.jsonl
# 개발 서버의 /tmp/dev_export.jsonl 과 같은 곳에 모은 뒤 (DB를 읽지 않고 파일만 비교)
python -m app.matching.sync_matches --diff dev_export.jsonl prod_export.jsonl
```

export에는 문서 목록(source_type, external_id, content_hash)과 매칭 행만 들어 있고 본문은 없다.

- [ ] `incident_reports`의 `only_B`(운영에만 있는 SWIM)가 있다 — 개발이 2026-10-06까지라 최근 장애가 잡히는 것이 정상
- [ ] `dogether_preventive`의 `only_A`/`only_B`가 0이거나 아주 작다 (개발은 운영 raw 861개를 넣은 것이므로 그 뒤 새 글만 차이)
- [ ] `content_differs`가 크지 않다 (크면 개발 데이터가 낡은 것 — 운영에서 직접 계산)

## 4. 운영에서 평가·미리보기 (쓰기 없음)

```bash
docker compose exec -T api python -m app.matching.dogether_swim --eval    > dg_eval_prod.json
docker compose exec -T api python -m app.matching.dogether_swim --dry-run > dg_dry_prod.json
docker compose exec -T api python -m app.matching.lookin_swim   --dry-run > lk_dry_prod.json
```

- [ ] `dg_eval_prod.json`: 개발 결과(번호 있는 65건, 날짜 못 뽑은 글 5건, 정답 쌍 7건 모두 1위, M≥0.10에서 auto 15건 전부 정답)와 비교했다
- [ ] `dg_dry_prod.json`, `lk_dry_prod.json`의 분포(review / 후보 없음 / 날짜 없음)가 개발과 비슷하다
- [ ] 세 JSON을 개발 쪽에 전달했다 (기준값 판단용)

## 5. 저장 (새 테이블에만 씀, auto 꺼짐)

```bash
docker compose exec -T api python -m app.matching.dogether_swim --apply
docker compose exec -T api python -m app.matching.lookin_swim   --apply
```

다시 실행해도 안전하다. 새 글·바뀐 글만 처리하고 confirmed·rejected·manual 행은 건드리지 않는다.

```bash
docker compose exec -T postgres psql -U citec -d citec_knowledge -c \
 "select 'dogether' t, status, count(*) from dogether_swim_matches group by 2
  union all select 'lookin', status, count(*) from lookin_swim_matches group by 2;"
```

- [ ] 두 `--apply`가 오류 없이 끝났다
- [ ] 위 쿼리에 `review`만 보인다 (`auto`가 있으면 즉시 중단 — `config/*_swim.json`의 `auto_enabled`·T1·M 확인)
- [ ] 행 수가 개발 이상이다 (운영 SWIM이 더 많으므로)

## 6. 검토 파일 만들기·판정

```bash
docker compose exec -T api python -m app.matching.dogether_swim --export-review /tmp/dg_review.csv
docker compose exec -T api python -m app.matching.lookin_swim   --export-review /tmp/lk_review.csv
docker compose cp api:/tmp/dg_review.csv .
docker compose cp api:/tmp/lk_review.csv .
```

- [ ] 엑셀에서 `decision` 열에 `confirmed` 또는 `rejected`를 적었다 (빈 칸은 무시됨. 점수 차가 큰 것부터)
- [ ] 개발에서 뽑은 LOOKIN 표본 45건(`/tmp/lookin_review_sample.csv`)의 판정을 전달했다 (구간별 정밀도 → T2·M 결정)

## 7. 검토 결과 반영

```bash
docker compose cp dg_review.csv api:/tmp/dg_review.csv
docker compose exec -T api python -m app.matching.dogether_swim --import-review /tmp/dg_review.csv
docker compose cp lk_review.csv api:/tmp/lk_review.csv
docker compose exec -T api python -m app.matching.lookin_swim --import-review /tmp/lk_review.csv
```

- [ ] 두 `--import-review`의 `confirmed`/`rejected`/`skipped` 건수를 확인했다
- [ ] (DOgether `confirmed`를 그래프에 반영하려면) 대상 글만 `--source-ids`로 graph_sync를 돌렸다 — 전체 동기화는 몇 시간 걸리므로 금지
- 반영된 행은 `manual`이 되어 이후 매처 실행이 바꾸지 않는다. LOOKIN 매칭은 그래프·API에 연결되어 있지 않다.

## 8. 개발 → 운영으로 사람 결정 옮기기 (개발에서 확정한 것이 있을 때만)

개발에는 현재 사람 결정이 없다 — 없으면 건너뛴다.

```bash
docker compose exec -T api python -m app.matching.sync_matches --import /tmp/dev_export.jsonl --only-human          # 미리보기
docker compose exec -T api python -m app.matching.sync_matches --import /tmp/dev_export.jsonl --only-human --apply  # 반영
```

- [ ] 미리보기의 `skipped_missing_post`/`skipped_missing_incident`가 감당할 수준이다 (크면 4~5단계로 운영에서 직접 계산)
- [ ] 대상 환경의 confirmed·rejected·manual 행은 덮어쓰이지 않는다 (`kept_local_human`으로 집계됨)

## 9. 하지 말 것

- [ ] `config/dogether_swim.json`·`config/lookin_swim.json`의 `auto_enabled`를 켜지 않았다. T1/M도 근거 없이 넣지 않았다
- [ ] 기준값이 정해지기 전에는 cron에 증분 실행(`--apply`)을 등록하지 않았다

## 문제가 생기면

- 마이그레이션 오류: `docker compose exec -T api alembic downgrade 20261113_0025`로 이번 테이블 2개만 제거한다. 백업은 이전 상태다.
- 매처가 이상한 결과를 저장했을 때: 사람이 확정한 행(`manual`)이 있으면 먼저 `sync_matches --export`로 보관한 뒤, 새 테이블만 비운다.

```sql
delete from lookin_swim_matches   where method='content';   -- 사람 결정(manual)은 남김
delete from dogether_swim_matches where method='content';
delete from lookin_swim_match_runs;                          -- 다음 실행이 전부 다시 처리
delete from dogether_swim_match_runs;
```

## 확인 기록

(운영 실행 후 단계별 결과를 여기에 적는다.)
