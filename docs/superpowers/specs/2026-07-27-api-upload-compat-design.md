# `/api/upload` wiki-qa 호환 업로드 API — 설계

날짜: 2026-07-27
작성 배경: 외부 시스템(기술지원이력/테크리포/DBMS튜닝 발행 시스템)이 이미 citec-wiki-qa의
`POST /api/upload` 계약(`~/tmp/citec-wiki-qa/README.md` "REST API 업로드" 절)에 맞춰
파일을 업로드하고 있음. citec-kb로 전환하더라도 이 클라이언트들이 코드 변경 없이 계속
동작해야 하므로, citec-kb의 기존 `/api/*` 호환 레이어(`app/routers/external_compat.py`)에
동일 계약의 엔드포인트를 추가한다.

## 범위

**이번 라운드에 실제 처리하는 source_type** (citec-kb에 이미 native 어댑터가 있는 것만):

| wiki-qa 값/별칭 | citec-kb 내부 source_type | raw 저장 디렉토리 |
|---|---|---|
| `support_history`, `support` | `support_history` | `raw_dir/support_history/` |
| `incident_reports`, `incident` | `support_history` | `raw_dir/support_history/` |
| `tech_repo`, `confluence_docs`, `confluence`, `techrepo`, `tech-repo` | `tech_repo` | `raw_dir/tech_repo/` |
| `tuning_ai`, `sql_tuning`, `sql`, `issue_analysis`, `dbms_tuning`, `dbms-tuning`, `tuning-ai` | `tuning_ai` | `raw_dir/tuning_ai/` |

별칭 정규화는 citec-wiki-qa README의 별칭 표를 그대로 따른다(요청하신 문서를 그대로 소스).

**이번 라운드에서 명시적으로 제외** (알려진 값이지만 미구현 — 오타값과는 다르게 취급):

- `vendor_docs`, `vendor` — citec-kb에 vendor_docs 전용 파싱 어댑터가 없음
- `checkitems` 또는 확장자 `.xls`/`.xlsx` — citec-kb에 XLS 파서가 없음(checkitem 인제스트는
  이미 변환된 JSON만 읽음, `iter_checkitems_json`)

이 두 경우는 **`501 Not Implemented`**로 응답한다(`"이 호환 엔드포인트에서 아직 지원하지
않음"` 메시지 포함). wiki-qa 계약에 없는 완전히 알 수 없는 `source_type` 값은 기존과 동일하게
**`400 Bad Request`**로 거부한다(오타 방지).

허용 확장자: `.md`, `.txt` (그 외 확장자는 400; `.xls`/`.xlsx`는 위와 같이 501).

## 엔드포인트

모두 `app/routers/external_compat.py`에 추가. 기존 `/api/*` 호환 레이어와 동일하게
인증 게이트 없음(README: "공개 연동용").

### 1. `POST /api/upload`

`multipart/form-data`:

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `file` | File | ✓ | 업로드 파일 |
| `source_type` | Form | △ | 기본값 `support_history`. 위 표의 값/별칭만 허용 |

처리 순서:

1. `source_type` 별칭 정규화 → 내부 타입. 미지원(알려짐)이면 501, 미지원(알수없음)이면 400.
2. 확장자 검사. `.md`/`.txt` 아니면 400 (단 `.xls`/`.xlsx`는 501).
3. 파일명은 `Path(file.filename).name`으로 basename만 채택(경로 주입 방지).
4. `raw_dir/<내부타입 디렉토리>/<안전한 파일명>` 에 파일 바이트 기록(동일 파일명 덮어쓰기 허용,
   wiki-qa와 동일한 "재업로드 시 덮어쓰기" 동작).
5. `IngestJob` row 생성(`mode="upload"`, `status="pending"`) → `job_id` 확보.
6. `BackgroundTasks`에 단일 파일 파싱+반영 작업 등록.
7. 즉시 응답: `{"job_id": "...", "filename": "...", "status": "queued"}`
   (DB status는 `pending`이지만 API 응답은 wiki-qa 계약대로 `"queued"` 문자열 사용).

백그라운드 작업:

- `app.ingest.adapters`에 새 함수 `parse_support_history_file(path) -> DocumentDraft`,
  `parse_tech_repo_file(path) -> DocumentDraft`, `parse_tuning_ai_file(path) -> DocumentDraft`를
  추가. 기존 `iter_support_history`/`iter_tech_repo`/`iter_tuning_ai`는 디렉토리를 순회하며
  각 파일마다 이 함수를 호출하도록 리팩터(로직 중복 없음, 디렉토리 전체 재스캔 없이 신규 파일
  1건만 처리 가능해짐).
- 파싱된 `DocumentDraft` → `app.ingest.pipeline.upsert_document_from_draft(draft)` 호출
  (기존 `content_hash` 비교로 동일 콘텐츠 재업로드는 자동 스킵 — 멱등).
- 성공 시 `IngestJob.status="success"`, `stats={"document_id":…, "action":…}`.
- 실패 시 `IngestJob.status="failed"`, `error=str(exc)`.
- `finished_at` 갱신.

### 2. `GET /api/ingest-status/{job_id}`

SSE (`text/event-stream`). `IngestJob` row를 1초 간격으로 폴링하며:

- `status=pending|running`: `data: {"type":"log","text":"📥 ingest 진행 중…"}\n\n`
- `status=success`: `data: {"type":"done","status":"done","document_id":…}\n\n` 후 스트림 종료
- `status=failed`: `data: {"type":"error","text":"...","error":"..."}\n\n` 후 스트림 종료
- job_id 존재하지 않으면 404
- 안전장치: 최대 120초(120회 폴링) 후에도 미종료면 마지막 log 이벤트를 보내고 스트림 종료
  (클라이언트가 재시도/재폴링하도록 유도, 무한 대기 방지)

### 3. `POST /api/upload-multiple`

`multipart/form-data`, 필드명 `files`(리스트), `source_type`(옵션, 전체 파일에 동일 적용).
각 파일에 대해 `POST /api/upload`와 동일한 검증·저장·큐잉을 반복 수행.
응답: `{"jobs": [{"job_id":…, "filename":…, "status":"queued"}, …]}`
개별 파일이 검증 실패해도 나머지 파일은 계속 처리(부분 실패 허용) — 실패한 항목은
`{"filename":…, "status":"rejected", "error":…}` 형태로 같은 배열에 포함.

## 문서 갱신

- `docs/EXTERNAL_API.md`: "검색·문서" 절 아래에 "업로드" 절 신설. 위 3개 엔드포인트,
  source_type 표, 범위 제외 사항(vendor_docs/checkitems-XLS) 명시.
- `app/routers/external_compat.py`의 `GET /v1/external/catalog`의 `wiki_qa_compat` 딕셔너리에
  세 엔드포인트 추가.

## 테스트 (DB 불필요, 순수 단위 테스트만 — CI가 DB/Redis 없이 도는 기존 구조와 일치)

`apps/api/tests/test_external_compat.py`에 추가:

- source_type 별칭 정규화 함수의 모든 별칭 → 내부 타입 매핑
- 알 수 없는 값 → 400 판정 신호(예외/센티널) 확인
- 알려졌지만 미구현(`vendor_docs`, `checkitems`) → 501 판정 신호 확인
- 파일명 sanitize 함수: `../../etc/passwd`, `a/b/c.md` 등 경로 주입 케이스가 basename만
  남는지 확인

`apps/api/tests/test_adapters_single_file.py` (신규):

- `parse_support_history_file`/`parse_tech_repo_file`/`parse_tuning_ai_file` 각각 임시 파일로
  호출해 `DocumentDraft`의 `source_type`/`external_id`/`title`/`content_hash`가 채워지는지 확인
- 리팩터 후에도 `iter_support_history` 등 기존 디렉토리 순회 함수가 동일 결과를 내는지
  (리그레션 방지) 기존 테스트가 있다면 유지, 없다면 최소 1건 추가

## 명시적으로 다루지 않는 것 (Out of scope)

- `vendor_docs` 실제 파싱 어댑터 신설 — 별도 작업
- XLS 파서 신설 (`checkitems` 업로드) — 별도 작업
- `/api/upload*`에 대한 인증 게이트 추가 — 기존 `/api/*` 호환 레이어와 동일하게 무인증 유지
- Redis 기반 job queue(`app/jobs/queue.py`) 연동 — 기존 `IngestJob` DB 테이블 + `BackgroundTasks`
  패턴을 재사용(native `POST /v1/ingest/run`의 `async_mode`와 동일 패턴)
