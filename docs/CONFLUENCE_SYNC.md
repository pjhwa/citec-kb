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

**2026-10-20 추가 — 안전 규칙과 옵트인 건수 허용(`CONFLUENCE_MAX_ERROR_COUNT`).**
- **루트 단위 에러(검색 호출 실패/페이지네이션 정체, `page_id` 없음)는 오류율과
  무관하게 항상 커서를 막는다.** 그 루트의 변경분 전체를 놓쳤다는 뜻이라 전진하면
  그 구간 변경분이 영구 누락된다(조회가 `lastmodified > 커서`뿐이므로).
- `CONFLUENCE_MAX_ERROR_COUNT`(기본 **0** = 기존 동작): 1 이상이면 "페이지 에러
  건수 ≤ 이 값"도 커서 전진을 허용한다. **허용된 페이지는 다시 수정될 때까지
  재시도되지 않아 유실된다.** 일시 장애는 커서를 막아 두면 다음 실행이 같은 구간을
  재크롤해 자동 복구되므로, 특정 page_id가 반복해서 영구 실패하는 것이 확인됐을
  때만 켠다(2시간 주기처럼 구간이 작을 때 그 1건이 커서를 계속 막는 상황).
- 종료코드는 그대로다: 에러가 1건이라도 있으면 1 → cron `❌`로 항상 보인다.

### 버그 수정: 검색(search) 호출 실패가 전체 크롤을 중단시키던 문제

2026-09-16, 자매 기능(`confluence_map`) 라이브 dry-run에서 실제로 발생:
페이지네이션 루프 안에서 개별 페이지 조회(`get_page_full`)만 try/except로
감싸져 있었고, **root별 검색/목록 조회(`search_pages_incremental`) 호출
자체는 보호돼 있지 않았다.** 그 결과 검색 호출 하나가 401 등으로 실패하면
예외가 `_crawl_source()` 밖으로 그대로 전파돼 **그 실행에 포함된 나머지 모든
root(사실상 그 함수 호출 전체)가 죽었다** — 개별 페이지 실패는 격리됐지만
검색 호출 실패는 격리되지 않았던 비대칭. `app.confluence.map_sync`가 이
모듈을 그대로 본떠 만들어진 거라 여기(confluence_docs/tech_repo, 이미 운영
크론 등록됨)에도 동일한 결함이 있었다 — 지금까지 실제 크론 실행에서 검색
호출이 실패한 적이 없어서 드러나지 않았을 뿐이다. 이제 검색 호출도 root
단위로 try/except로 감싸 실패 시 그 root만 포기하고 다음 root로 진행하도록
고쳤다(`errors`에 `page_id: null`로 기록).

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

- **삭제/비공개 전환/이동 페이지는 일일 증분으로는 탐지하지 않는다.** `lastmodified >` 조건은
  추가/수정만 잡는다(페이지 이동은 수정 시각이 안 바뀔 수 있다 — 라이브 미검증). 주 1회
  전체 대조 배치(`docs_inventory`, 아래 "주간 전체 대조")가 이를 보완한다.
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

## 주간 전체 대조 (`app.confluence.docs_inventory_cli`, 2026-11-05)

`confluence_docs` / `tech_repo`의 일일 증분은 "위치만 바뀐" 페이지와 "추적 루트 밖으로 나간/삭제된"
페이지를 잡지 못한다(`confluence_map`에는 `map_inventory`가 있었고 이쪽에는 없었다). 이 배치가 그 틈을 닫는다.

**동작**
1. 모든 루트를 전부 나열한다(`since=None`, `expand=ancestors`, 50건/호출 → 약 170회 호출). 페이지 본문은 받지 않는다.
2. 활성 문서(`status=active`)의 `external_id` / `title` / `metadata.ancestor_ids`와 비교한다.
   - 새 페이지(루트 안으로 이동해 들어온 것 포함), 조상 체인이 다른 페이지(이동), 제목이 다른 페이지(이름 변경)만
     `get_page_full`로 다시 받아 raw 파일을 쓴다. 나머지는 건드리지 않는다.
   - 목록에 없는 활성 문서는 `archived`로 바꾸고, 원본 파일을 `<raw>/<source_type>/.archived/`로 **옮긴다**
     (삭제 아님). `run_ingest`가 raw 파일을 전부 다시 읽어 archived 문서를 되살리므로(원본이 보이면 복구하는
     기존 동작), 파일을 옮기지 않으면 다음 ingest에서 즉시 되살아난다.
3. 변경분만 `run_ingest` + `embed_pending_chunks` 한다. `Source.last_sync_at`은 건드리지 않는다.

**보관(archive) 안전장치** — 목록에서의 부재는 목록이 믿을 만할 때만 증거다.
- 루트 하나라도 검색 오류/페이지네이션 정체가 있으면 보관하지 않는다(`archive_skipped: listing_errors`).
- 보관 후보가 활성 문서의 5%(`--max-archive-ratio`)를 넘으면 보관하지 않는다(`ratio_exceeded`). 루트 목록 오설정이나
  Confluence 장애로 인덱스가 대량 보관되는 것을 막는다. 후보는 항상 `would_archive`에 나온다.
- `--no-archive`로 보관을 끌 수 있다. 되돌리기: 해당 문서 `status`를 `active`로 되돌리고 `.archived/`의 파일을
  원위치로 옮긴 뒤 ingest. 또는 페이지가 목록에 다시 나타나면 다음 실행이 새 페이지로 다시 받는다.
- 이 DB에 있는 `confluence_docs`/`tech_repo` 문서 중 현재 루트 목록 밖에서 온 것(과거 다른 경로로 적재)은 후보가 된다.
  **첫 실행은 반드시 `--dry-run`으로 `would_archive`를 눈으로 확인한다.**

**미확인(운영 첫 dry-run에서 확인)**
- 검색 API가 `expand=ancestors`를 실제로 지원하는지. 결과에 `ancestors`가 없으면 그 페이지는 이동 여부를 알 수
  없어 건너뛰고(`skipped_no_ancestors`로 보고), `--allow-full-fetch`를 줄 때만 전부 다시 받는다(사실상 전체 크롤, 수 시간).
- 페이지 이동이 `lastmodified`를 바꾸는지(바꾸면 일일 증분도 이동을 잡는다).

**운영**
```bash
scripts/docs_inventory.sh --dry-run            # 결과 JSON: fetch_by_reason, would_archive, listing_errors, skipped_no_ancestors
scripts/docs_inventory.sh                      # 실제 반영 (종료코드 1 = 목록/페이지 오류 있음)
# cron (일일 sync와 같은 flock 파일 → 서로 겹치지 않음), 주 1회 토요일 18:00 (map_inventory 가 20:00 에 시작하므로 겹치지 않게; 운영 공용 lock 이름은 citec-kb-confluence-shared.lock):
# 0 18 * * 6 /usr/bin/flock -n /tmp/citec-kb-confluence-shared.lock /path/to/citec-kb/scripts/docs_inventory.sh >> /path/to/citec-kb/logs/cron_docs_inventory.log 2>&1
```
옵션: `--sources confluence_docs,tech_repo`(기본 둘 다), `--no-archive`, `--max-archive-ratio R`, `--allow-full-fetch`.
코드: `apps/api/app/confluence/docs_inventory.py`(`plan_reconcile`은 순수 함수), 테스트 `tests/test_confluence_docs_inventory.py`,
`tests/test_confluence_docs_inventory_db.py`(scratch DB).

**알려진 별개 문제(이번에 고치지 않음)**: `confluence_map`의 `run_map_inventory`와 일일 `sync`는 archived 처리 후에도 원본
raw 파일을 그대로 두므로, 다음 `run_ingest`가 archived 문서를 되살릴 수 있다. 위와 같은 방식(파일 옮기기)이 필요한지 확인할 것.

### 장시간 인벤토리 운영 규칙 (2026-11-05, 11/2 사고 후속)

- 사고: 2026-11-01 20:00 KST에 시작한 `map_inventory`(3개 공간, 한 번에 15~19시간)가 11/2 14:32 KST 배포의
  `docker compose restart api`에 죽었다. 결과 JSON은 마지막에만 출력되고 로테이션 상태도 끝에서만 전진해서 아무것도
  남지 않았고, 다음 주에 같은 3개 공간을 처음부터 다시 돌게 되어 있었다. (04:00 자동 재시작은 잠금을 보고 정상적으로 건너뜀.)
- **소스별 진행:** `map_inventory_cli`는 소스 하나가 끝날 때마다 `source_done {...}` 한 줄을 로그(stderr)에 남기고
  로테이션 상태(`.inventory_rotation_state.json`)를 즉시 전진시킨다. 중간에 죽어도 끝난 소스는 다시 하지 않는다.
  잠금 때문에 건너뛴 소스가 있으면 그 지점에서 전진을 멈춘다. `docs_inventory`도 소스별 `source_done` 줄을 남긴다.
  진행 확인: `grep source_done logs/cron_map_inventory.log | tail`.
- **배포 보호:** `scripts/git_pull_deploy.sh`는 api를 재시작하기 전에 `scripts/api_busy_check.sh`로 장시간 배치
  (`map/docs inventory`, `map_backfill`, ingest/embed, 동의어 마이닝, 관리자 map sync 잠금)를 확인하고, 있으면 `-y`여도
  **중단**한다(pull 전에 먼저 확인하므로 중단 시 아무것도 바뀌지 않음). 강제로 하려면 `--force-restart`. 코드만 먼저
  반영하고 배치 종료 후 재시작하려면 `--no-restart` 후 `--restart-only`.
- 야간 `restart_api_if_idle.sh`도 같은 `api_busy_check.sh`를 쓴다(감지 목록 한 곳). 새 장시간 CLI를 추가하면
  `api_busy_check.sh`의 패턴에 넣을 것.
