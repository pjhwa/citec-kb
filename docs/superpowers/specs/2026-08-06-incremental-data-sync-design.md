# 운영→개발 Incremental DB Sync 설계

- 작성일: 2026-08-06
- 배경: 운영서버 전체 데이터를 개발서버로 옮기고 싶다는 요청에서 출발. `scripts/out.sh --data
  --pg-dump` + `scripts/in.sh --data --restore-pg`로 전체 덤프 이관은 이미 가능하지만, 반복적으로
  최신 상태를 동기화하려면 매번 전체 DB를 다시 옮기는 건 낭비가 크다. 이 설계는 그 반복 동기화를
  "신규/변경분만" 옮기는 incremental sync 경로를 다룬다.
- 범위: 삭제된 문서는 무시(이번 범위에서 다루지 않음). 임베딩은 옮기지 않고 개발서버에서
  재생성한다. DB만 다룬다 — 첨부파일(`data/raw`) incremental sync는 별도 설계로 미룬다.
- 폐쇄망 전제: 운영↔개발 간 직접 DB 커넥션이 없다. 기존 `out.sh`/`in.sh`와 동일하게 파일(tar.gz)을
  수동/스크립트로 반입·반출하는 흐름을 유지한다.

## 1. 접근 방식

`documents`에는 `content_hash`/`updated_at`이 있지만, `checkitems`에는 변경 감지용 컬럼이 없다
(가끔 수정되는데도). 테이블마다 다른 컬럼에 의존하지 않도록, 6개 테이블
(`documents`, `document_sections`, `chunks`, `checkitems`, `issue_frames`, `failure_buckets`)
전부에 대해 **행 내용을 즉석에서 해시**해서 비교하는 방식을 쓴다. 스키마 변경 없음.

```sql
SELECT id, md5(t::text) AS h FROM <table> t;
```

## 2. 스크립트 구성

`scripts/out.sh`/`in.sh`와 별도로 3개 스크립트를 신설한다.

### 2.1 `scripts/sync_manifest.sh` (개발에서 실행)

- 6개 테이블 각각에 대해 `(id, hash)` 목록을 조회
- `citec-kb-manifest-<TS>.tsv.gz` 로 저장 (테이블명 컬럼 포함, 한 파일에 6테이블 다 담음)
- 사람이 scp 또는 반입 절차로 운영에 전달

### 2.2 `scripts/sync_export.sh --manifest <파일>` (운영에서 실행)

- 받은 개발 매니페스트를 파싱
- 운영 쪽에서 동일 쿼리로 각 테이블 `(id, hash)`를 조회
- 테이블별로 "id가 개발 매니페스트에 없거나 hash가 다른" id 목록 산출
- 각 id 목록에 대해 `psql \copy (SELECT * FROM <table> WHERE id = ANY(:ids)) TO stdout WITH csv`
  로 export
- 6개 테이블의 CSV + 요약 매니페스트(`manifest.txt`: 테이블별 변경 건수, 타임스탬프, 소스 호스트)를
  `citec-kb-incr-<TS>.tar.gz`로 묶음
- 삭제된 문서(개발엔 있는데 운영엔 없는 id)는 감지하지 않고 무시

### 2.3 `scripts/sync_apply.sh <incr 번들>` (개발에서 실행)

- 번들을 풀어 테이블별 CSV를 임시 스테이징 테이블에 `\copy ... FROM stdin`으로 로드
- 단일 트랜잭션 안에서 아래 순서로 적용 (FK 의존성 순서):
  `documents → document_sections → chunks → checkitems → issue_frames → failure_buckets`
- `documents`: `INSERT ... ON CONFLICT (id) DO UPDATE`. 기존 행이 있고 실제로 바뀐 경우
  (파이프라인 `_upsert_document`와 동일한 컨벤션):
  - 기존 `chunks.is_active = false` 처리
  - 기존 `document_sections` 삭제
  - 새로 받은 `document_sections`/`chunks`를 삽입 (여기엔 embedding 없음 — 정상)
- `checkitems`/`issue_frames`/`failure_buckets`: 각자 PK로 `ON CONFLICT DO UPDATE`
- 트랜잭션 커밋 후 적용 전/후 건수를 로그로 남김 (기존 `out.sh`의 PG 덤프 건수 검증 패턴과 동일)
- 마지막 단계로 `docker compose exec api python -m app.embed.cli` 실행 —
  `embed_pending_chunks`는 이미 "모델 기준으로 embedding 없는 active chunk"만 골라 배치
  임베딩하므로, 새로 들어온 chunk만 자동으로 재임베딩되고 기존 chunk는 건드리지 않는다
  (`apps/api/app/embed/job.py::_fetch_pending_batch`, NOT EXISTS 안티조인).

## 3. 에러 처리

- `sync_apply.sh`는 전체를 단일 트랜잭션으로 묶어 실패 시 롤백 (부분 반영 없음)
- 번들에 소스 호스트/타임스탬프/테이블별 건수 매니페스트 포함 — 반입 전 육안 확인 가능
- `psql \copy` 실패, 테이블 CSV 누락 등은 스크립트가 즉시 종료(`set -euo pipefail`)하고 어떤 단계에서
  실패했는지 로그로 남김
- 재임베딩(`embed.cli`) 실패는 별도 단계이므로 DB 적용 자체의 롤백 대상이 아님 — 실패 시 재실행하면
  안전 (idempotent, NOT EXISTS 기반이라 이미 임베딩된 chunk는 건너뜀)

## 4. 테스트 계획

- 로컬 환경에서 `documents` 몇 건을 의도적으로 수정/신규 추가 → manifest → export → apply
  왕복시켜 diff 건수와 실제 반영 건수가 일치하는지 확인
- `checkitems` 행 하나를 수정 → 해시 기반으로 정확히 변경 감지되는지 확인 (컬럼 기반이었다면
  놓쳤을 케이스)
- `sync_apply.sh` 실행 후 `embed.cli` 결과의 `embedded` 건수가 새로 들어온 chunk 수와 일치하고
  `errors=0`인지 확인
- 변경 없는 문서(hash 동일)가 diff에서 제외되는지, 즉 불필요한 재임베딩이 일어나지 않는지 확인

## 5. 다음 단계 (이번 범위 밖)

- 첨부파일(`data/raw`) incremental sync — `raw_manifest.json` 기반 diff, `out.sh`/`in.sh`와
  별개로 필요 시 후속 설계
- 삭제된 문서 처리 (운영에서 삭제/archived된 문서를 개발에 반영할지 여부)
