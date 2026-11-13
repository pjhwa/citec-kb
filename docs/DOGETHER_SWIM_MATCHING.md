# DOgether 장애사례 ↔ SWIM 장애 내용 매칭 (번호 없는 글)

번호가 적히지 않은 `dogether_preventive` [장애사례] 글(운영 기준 약 535건)을 본문의 장애 시각과 내용으로 SWIM 장애
(`incident_reports`)에 맞춘다. 번호로 연결된 글(`swim_ids`)은 대상이 아니다. 횡전개·장애예방가이드 글도 직접 매칭하지 않는다.

> **상태: 자동 연결(auto)은 꺼져 있다.** 평가 기준(auto 정밀도 95% 이상)을 확인하기 전에는 켜지 않는다.
> 기준값 T1/M과 가중치는 운영 데이터 평가 결과로 정한다. 코드의 가중치(날짜 0.45, 유사도 0.35, 구성요소 0.15, 분야 0.05)는 초안이다.

## 저장

`dogether_swim_matches` (게시글 metadata에는 쓰지 않는다 — 수집기가 같은 글을 다시 올리면 metadata가 새로 만들어져 덮인다).

| 열 | 내용 |
|---|---|
| post_document_id, incident_document_id | `documents.id`. 둘의 조합은 유일. 문서가 삭제되면 행도 함께 삭제(CASCADE) |
| method | `content`(매처) / `manual`(사람이 확정·거절한 행) |
| score, rank, signals | 점수, 같은 날짜 묶음 안 순위, 신호 상세(날짜·유사도·구성요소·분야·시차) |
| status | `auto` / `review` / `confirmed` / `rejected` |
| matcher_version | 매처 버전 (현재 `1`) |

`dogether_swim_match_runs`: 글별 마지막 처리 기록(content_hash, matcher_version). 후보가 0건인 글도 남아 증분 실행이 다시 보지 않는다.

수집기가 글을 다시 올려도 문서 `id`는 같으므로(내용만 갱신) 매칭 행은 유지된다 (`test_matches_survive_collector_reupload`).

## 판정

| status | 조건 | 그래프 |
|---|---|---|
| auto | 날짜가 있고, 1위 점수 ≥ T1, 2위와의 차이 ≥ M, `auto_enabled=true` | `REFERENCES` INFERRED |
| review | auto는 아니지만 1위 점수 ≥ T2. 같은 날짜 묶음의 상위 `review_top_n`건 저장 | 연결 안 함 |
| confirmed | 사람이 확인 (`method=manual`) | `REFERENCES` EXTRACTED |
| rejected | 사람이 틀렸다고 표시 (`method=manual`) | 연결 안 함, 다시 제안하지 않음 |

- 날짜가 없는 글은 auto가 되지 않는다(후보가 많다). 날짜가 둘 이상이면 날짜마다 따로 판정한다.
- **재실행 안전:** 매처가 바꾸는 행은 `method=content` 이면서 `status in (auto, review)`인 것뿐이다. `confirmed`·`rejected`·`manual` 행은 절대 바꾸거나 지우지 않는다.
- 같은 장애에 번호 연결이 있으면 번호 연결이 이긴다(그래프·역조회 모두).

## 설정 `config/dogether_swim.json`

| 키 | 기본 | 설명 |
|---|---|---|
| auto_enabled | false | false면 auto 후보도 review로만 저장 |
| T1, M | null | null이면 auto 판정이 나오지 않는다. `--eval` 스윕으로 정한다 |
| T2 | 0.0 | review 최소 점수 |
| weights | 0.45/0.35/0.15/0.05 | 초안 |
| date_tolerance_days | 1 | 추출 날짜 ±N일 안의 장애가 후보 |
| window_before_days / window_after_days | 210 / 7 | 날짜가 없을 때 게시일 기준 후보 기간 (관측 시차 12~199일) |
| review_top_n | 3 | 묶음당 저장할 review 후보 수 |
| hub_component_df_max | 0.3 | 후보의 이 비율 넘게 나오는 범용 구성요소는 신호에서 제외 |

설정은 git으로 바꾼 뒤 배포한다(컨테이너에서는 읽기 전용).

## 실행 `python -m app.matching.dogether_swim`

| 옵션 | 동작 |
|---|---|
| `--eval` | 번호 있는 글의 `swim_ids`를 숨기고 매처를 돌려 1위 적중률, 정답 쌍 7건, T1×M 스윕(auto 수·정밀도)을 JSON으로 출력. **쓰기 없음** |
| `--dry-run` | 새·바뀐 글을 매칭해 분포(auto/review/후보 없음/날짜 없음)만 출력. 쓰기 없음 |
| `--apply` | 새·바뀐 글을 매칭해 저장 (`--all`: 이미 처리한 글도 다시, `--limit N`) |
| `--export-review CSV` | review·auto 행을 CSV로 (`decision` 열은 비어 있음) |
| `--import-review CSV` | `decision`이 `confirmed`/`rejected`인 행만 반영 (`method=manual`로 저장) |

```bash
docker compose exec -T api python -m app.matching.dogether_swim --eval
docker compose exec -T api python -m app.matching.dogether_swim --dry-run
docker compose exec -T api python -m app.matching.dogether_swim --apply
```

## 평가 절차 (auto를 켜기 전에 반드시)

1. `--eval`을 실행한다. `posts_without_extracted_date_ids`에 나온 번호 있는 글을 열어 놓친 날짜 형식을 `app/matching/dates.py`에 더한다.
2. `sweep` 표에서 정밀도 95% 이상인 (T1, M)을 고른다. 표본이 작으므로 `auto` 건수가 너무 적은 조합은 피한다.
3. `gold_pairs`에서 19195 → 26091461373이 후보 안(rank 있음)에 들어오는지 확인한다.
4. 결과 표(평가 대상 수, 1위 적중, auto 수, auto 정밀도, review 수)를 이 폴더에 남긴다.
5. 기준에 못 미치면 `auto_enabled=false`로 두고 review만 운영한다. 켤 때는 T1/M 값과 함께 별도 변경으로 올린다.

## 그래프·조회

- `extract_swim_links(…, content_matches=…)`가 `auto`·`confirmed`를 읽어 `REFERENCES`를 낸다 (순수 함수, DB 조회는 `pipeline.sync_document`가 한다).
- 재동기화: 매칭이 있는 DOgether 글에만 `swim_matches`가 해시 입력에 들어간다. **다른 문서의 해시는 바뀌지 않아 전체 재동기화가 일어나지 않는다.** 매칭이 추가·삭제·상태 변경되면 그 글만 다음 `graph_sync`에서 다시 처리된다. 즉시 반영: `scripts/graph_sync.sh --source-ids dogether_preventive:dogether-<게시ID>`.
- 역조회 `related_prevention_posts[].match`: `swim_ids`(본문 번호) · `candidate`(번호 후보) · `manual`(사람 확정, `score`) · `content`(내용 추정, `score`). 한 글이 여러 경로로 걸리면 앞선 값만 남긴다. review·rejected는 나오지 않는다.
- MCP `kb_get_document`(장애)·`kb_similar_incident`는 `content`를 "내용으로 추정한 연결 — 번호 연결 아님, 확인 필요"로, `manual`을 "사람이 확정한 연결"로 구분해 표시한다.

## 운영 배포 (이 변경에는 **마이그레이션이 있다**)

`20261113_0025`가 테이블 2개를 추가한다(추가만 하며 기존 테이블은 건드리지 않는다). `git_pull_deploy.sh`로 배포하면 api 컨테이너 기동 시 `alembic upgrade head`가 자동으로 실행된다.

1. 배포 전 DB 백업: `scripts/backup_postgres.sh`
2. `scripts/git_pull_deploy.sh --dry-run` → `scripts/git_pull_deploy.sh -y`
3. 확인: `docker compose exec -T api alembic current` → `20261113_0025`, `curl -s http://127.0.0.1:8573/v1/health`
4. 기존 기능 영향 없음 확인: `GET /v1/tickets/26091661376?source_type=incident_reports`의 `related_prevention_posts`가 이전과 같다(`swim_ids` 1건).
5. `--eval` 실행 → 평가 절차 진행 → `--dry-run`으로 분포 확인 → `--apply`(auto 꺼진 상태, review만 저장).
6. `--export-review`로 검토 목록을 받아 사람이 `decision`을 채워 `--import-review`.
7. 평가를 통과하고 설정을 바꿔 배포한 뒤 `--apply --all`로 다시 판정하고 `graph_sync.sh`를 실행한다.

롤백: 코드는 직전 커밋으로 되돌린다. 테이블은 남아도 다른 기능에 영향이 없다. 지우려면 `alembic downgrade 20261109_0024`.

수집기가 매일 18:00에 올린다면 증분 매칭은 그 뒤(예: 19:00)에 `--apply`를 cron으로 돌린다. **cron은 아직 등록하지 않았다** — 평가 통과 전에는 등록하지 않는다.

## 알려진 한계

- 의미 유사도는 게시글의 활성 청크 전체와 장애의 활성 청크 전체 사이 최대 코사인이다. 현상·원인·조치 청크만 고르는 구분은 아직 하지 않았다(청크 구조를 운영 데이터로 확인한 뒤 결정).
- 지침서의 소요시간 신호는 뺐다. 장애 문서 15,366건 중 `서비스 중단시간` 필드가 있는 것이 103건뿐이다.
- 고객사 이름은 신호로 쓰지 않는다(번호 있는 글 68건 중 1건에만 나옴).
- 변경 감지는 게시글의 `content_hash`로 한다. 장애 쪽이 나중에 들어오거나 바뀌어도 이미 처리된 글은 다시 보지 않는다. 필요하면 `--apply --all`.
- 구성요소(lexicon) 겹침의 hub 제외는 후보 집합 안의 빈도로 판단한다. 그래프의 `is_hub` 플래그는 쓰지 않는다.
