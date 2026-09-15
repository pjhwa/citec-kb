# Confluence 증분 동기화 (`app.confluence.sync_cli`)

LOOKIN space의 `confluence_docs` 4개 카테고리와 TechRepo space의 `tech_repo` 5개
섹션을 폴링해서, 마지막 동기화 이후 새로 생기거나 수정된 페이지만 재추출 →
`run_ingest` → `embed_pending_chunks`까지 자동으로 실행하는 배치.

원 의뢰 프롬프트: `~/dev/citec-kb_confluence_incremental_sync_prompt.md` (개발
당시 dev 시스템은 Confluence에 접근할 수 없었음 — 아래 "라이브 Confluence 검증"
참고, 2026-09-15 운영 서버 실행으로 전부 완료됨).

## 사용법

```bash
# 1. 반드시 먼저: 파일 쓰기만, DB 반영 없음. 루트 하나만, 소량으로.
python -m app.confluence.sync_cli --dry-run --root-id 222532692 --max-pages 5

# → data/raw/confluence_docs/confluence_*.md 를 눈으로 확인.
#   본문/제목/URL/최종수정일이 그럴듯한지, 매크로 잔재(]]>, 고립 CSS 룰,
#   <ac:...> 태그 등)가 안 남았는지 확인.

# 2. 문제 없으면 전체 dry-run (bootstrap 규모 확인 — 최초 실행은 커서가 없어
#    전체 크롤링이라 TechRepo는 971건 규모가 될 수 있음)
python -m app.confluence.sync_cli --dry-run

# 3. 실제 반영 (run_ingest + embed_pending_chunks + last_sync_at 갱신)
python -m app.confluence.sync_cli
```

옵션:
- `--sources confluence_docs,tech_repo` (기본값, 콤마 리스트로 제한 가능)
- `--max-pages N` — 루트당 최대 처리 페이지 수
- `--root-id ID` — 단일 root pageId만 처리
- `--raw-dir` — 기본 `$RAW_DIR` 또는 `/data/raw`
- `-v/--verbose`

## 라이브 Confluence 검증 — 전부 완료 (2026-09-15, 운영 서버 실행 기준)

개발 시스템은 Confluence에 접근할 수 없어 애초엔 아래 항목들이 전부
미검증이었다. 운영 서버에서의 `--dry-run` 단계적 실행 + 전체 실행(1회,
confluence_docs 5,482건 + tech_repo 971건 성공 처리, 소요 약 1시간 50분 —
대부분은 embed 백필 45분 + 0.3req/s로 일부러 느리게 돈 크롤 시간)으로 전부
확인됐다:

1. **CQL `lastmodified` 날짜 포맷·타임존** (`"yyyy/MM/dd HH:mm"`, KST 변환) —
   ✅ 확인됨. 아직 실제로 `lastmodified >` 조건이 걸린 증분 실행(2회차 이후)은
   없었으므로, 정확한 포맷 문자열 자체가 CQL 파서를 통과하는지는 다음 실행에서
   최종 확인.
2. **`ancestor=` 조합 CQL 문법** — ✅ 확인됨. 9개 root 전부 정상 응답(200).
3. **대량 페이지 페이지네이션 안정성** — ✅ 확인됨. confluence_docs 최대
   root(`131290561`)에서 `start=0`부터 `start=3250`까지 66회 연속 정상 진행,
   tech_repo도 971건(의뢰서 추정치와 정확히 일치) 전부 정상 처리.
4. **`confluence_docs` 프론트매터 `폴더분류` 형식** — ✅ 확인됨. 실제 생성된
   파일(`confluence_1418690179.md`)에서 `공간명 : LOOKIN` / `폴더분류 : 오픈스택
   역량강화`(root `178797461` 매핑과 일치) 정상 출력 확인.

### 새로 발견된 것: 드문 페이지 단위 에러 (401 등)

5,483페이지 중 1건(`1731802470`)이 `401 Unauthorized`로 실패(0.02%) — 이후
발견된 문제는 코드 자체가 아니라 **에러 처리 정책**이었다: 최초 구현은 에러가
1건이라도 있으면 커서를 절대 전진시키지 않았는데, 그 결과 이 1건짜리 실패가
매번 confluence_docs 전체(5천여 페이지)를 처음부터 재크롤하게 만들었다(하루
1회 크론이면 영구 반복). **2026-09-15 박재화 확인 후 정책 변경**: 소스별 에러율이
`CONFLUENCE_MAX_ERROR_RATE`(기본 1%) 이하면 커서를 전진시키고, 실패한
page_id는 `error_detail`에 남겨서 추적 가능하게 한다(`app.confluence.sync.sync()`).
`--max-pages`/`--root-id`로 의도적으로 좁힌 실행은 여전히 무조건 커서
미전진.

## Rate limiting (사용자 요구사항 — 원 프롬프트에는 없음)

- 요청 간격을 `CONFLUENCE_RATE_LIMIT_RPS`(기본 0.3 req/s — 아래 참고)로 제한
  (`app.confluence.client.RateLimiter`).
- 크롤 구간은 `ConfluenceClient.bulk_client()`로 만든 단일 `AsyncClient`를
  재사용 — 페이지마다 새 TCP/TLS 핸드셰이크를 만들지 않는다. (기존
  `list_attachments`/`get_page_body` 등 실 운영 트래픽을 받는 메서드는
  그대로 두었다 — 건드리지 않음.)
- `AsyncHTTPTransport(retries=2)`는 연결 수준(DNS/TCP/TLS) 실패만 재시도하고
  HTTP 상태코드는 재시도하지 않는다 — 429/503은 `Retry-After` 헤더를 존중하는
  백오프(최대 5회, 캡 30초)로 별도 처리(`ConfluenceClient._get_with_retry`).

### 실측: 429 발생 및 대응 (2026-09-15 운영 dry-run)

운영 서버에서 `--dry-run` 중 실제로 429를 한 번 받았다. 관찰된 사실:

- Confluence 응답 헤더: `X-RateLimit-Limit: 10`, `X-RateLimit-Interval-Seconds: 3`
  (약 3.33 req/s 버킷), 429 당시 `X-RateLimit-Remaining: 0`.
- 우리 기본 페이싱(당시 2 req/s)은 이론상 버킷 리필 속도보다 느린데도 고갈됐다 —
  같은 계정(`jooksan.park`)을 다른 도구(예: MY-OS)가 동시에 쓰고 있을 가능성이
  있다.
- **버그를 하나 발견/수정함**: 429 응답의 `Retry-After` 헤더 값이 문자 그대로
  `"0"`이었는데, 기존 코드는 이를 그대로 신뢰해 지연 없이 즉시 재시도했다(이번엔
  운 좋게 바로 다음 요청이 성공했지만, 버킷이 진짜 비어있었다면 429가 반복될
  수 있는 상황). `X-RateLimit-Limit`/`X-RateLimit-Interval-Seconds` 헤더로
  "토큰 1개 리필에 걸리는 시간"을 계산해 최소 대기 시간으로 쓰도록
  (`_rate_limit_refill_floor()`), 그리고 헤더가 전혀 없는 경우를 위한 절대
  최소값(`_MIN_RETRY_DELAY = 0.5초`)도 추가했다.
- **기본 RPS를 0.3으로 낮췄다** (2026-09-15, 박재화 확인). 이 Confluence 계정은
  담당자 본인의 대화형 브라우징, MY-OS 등 다른 자동화 도구와 공유되는 계정이라
  이 배치만의 페이싱으로는 429를 완전히 피할 수 없다. 크론 주기를 하루 1회
  (점심시간 12시)로 정했고 시간 여유가 충분하므로, 속도보다 "다른 도구에 폐 안
  끼치는 것"을 우선해 아주 느리게(약 3.3초에 1건) 돌도록 맞췄다. 소규모 테스트
  실행 때만 필요하면 `CONFLUENCE_RATE_LIMIT_RPS=1` 등으로 일시적으로 올려서
  써도 된다.

## 알려진 한계

- **삭제/비공개 전환 페이지는 탐지하지 않는다.** `lastmodified >` 조건은
  추가/수정만 잡는다. 별도의 주기적 전체 재조정(reconciliation) 배치가 필요함.
- `--dry-run`은 `Source.last_sync_at`을 갱신하지 않으므로 반복 실행해도 항상
  같은(또는 bootstrap) 범위를 재크롤링한다 — 검증용으로만 쓸 것.
- `--max-pages`/`--root-id`로 범위를 좁힌 실행은 **항상 `last_sync_at`을
  갱신하지 않는다** (부분 실행이 "성공한 전체 동기화"로 오인되어 나머지 페이지가
  영구적으로 누락되는 것을 방지). 페이지 처리 중 에러는 소스별 에러율이
  `CONFLUENCE_MAX_ERROR_RATE`(기본 1%) 이하면 갱신을 막지 않는다 — 위 "새로
  발견된 것" 참고. 결과 JSON의 `sources.<type>.cursor_advanced`/`error_rate`로
  실제 반영 여부를 확인할 것.

## 운영 배포 제안 (배포/크론 등록은 하지 않음 — 담당자 승인 필요)

- 크론 주기: **확정 — 하루 1회, 점심시간 12시** (박재화). `scripts/confluence_sync.sh`
  헤더에 예시 crontab 라인 있음.
- 신규 환경변수(선택, 기본값 있음): `CONFLUENCE_RATE_LIMIT_RPS`(기본 0.3),
  `CONFLUENCE_TIMEZONE`(기본 `Asia/Seoul`), `CONFLUENCE_MAX_ERROR_RATE`(기본 0.01).
- 기존 `CONFLUENCE_BASE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_PASSWORD`는
  이미 운영에 설정되어 있으므로 추가 불필요.
- PR 병합은 박재화 승인 후 진행.

## 코드/테스트 위치

- `apps/api/app/confluence/client.py` — `get_page_full`, `search_pages_incremental`,
  `build_incremental_cql`, `RateLimiter`, retry-on-429/503 (신규 추가분만;
  기존 메서드는 무변경).
- `apps/api/app/confluence/sync.py` — `storage_html_to_text`/`clean_body`(포팅),
  프론트매터 생성, 커서/타임존 처리, 크롤 오케스트레이션.
- `apps/api/app/confluence/sync_cli.py` — CLI.
- 단위테스트: `apps/api/tests/test_confluence_client_incremental.py`,
  `apps/api/tests/test_confluence_sync.py`, `apps/api/tests/test_confluence_crawl.py`
  (전부 httpx mock + 첨부 픽스처, 라이브 호출 없음). 픽스처:
  `apps/api/tests/fixtures/confluence_sync/`.
- DB 파이프라인 통합테스트: `apps/api/tests/test_confluence_sync_db.py` —
  기본적으로 skip됨(CI의 `DATABASE_URL`은 의도적으로 접속 불가 포트).
  scratch DB(`CONFLUENCE_SYNC_TEST_DATABASE_URL`)를 만들어야 실행됨 — 파일
  상단 docstring에 절차 있음. **절대 운영 `citec_knowledge` DB를 가리키지
  말 것.**
