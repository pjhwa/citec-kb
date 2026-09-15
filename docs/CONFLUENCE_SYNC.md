# Confluence 증분 동기화 (`app.confluence.sync_cli`)

LOOKIN space의 `confluence_docs` 4개 카테고리와 TechRepo space의 `tech_repo` 5개
섹션을 폴링해서, 마지막 동기화 이후 새로 생기거나 수정된 페이지만 재추출 →
`run_ingest` → `embed_pending_chunks`까지 자동으로 실행하는 배치.

원 의뢰 프롬프트: `~/dev/citec-kb_confluence_incremental_sync_prompt.md` (개발
당시 dev 시스템은 Confluence에 접근할 수 없었음 — 아래 "라이브 미검증 항목"
참고).

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

## 라이브 Confluence로 한 번도 검증 못 한 부분

개발 시스템은 Confluence에 직접 접근할 수 없어, 아래 3가지는 **운영 서버에서의
최초 `--dry-run` 실행으로만 확인 가능**하다:

1. **CQL `lastmodified` 날짜 포맷.** `"yyyy/MM/dd HH:mm"` 형태로 구현했고
   (`app.confluence.sync.format_cursor`), Asia/Seoul(KST)로 변환해서 렌더링한다
   (`Source.last_sync_at`은 UTC로 저장되므로 — 이 변환을 빼먹으면 매 실행마다
   9시간 분량의 변경사항을 놓친다). 실제 Confluence가 이 포맷/타임존을 그대로
   받아들이는지는 미확인.
2. **`ancestor=` 조합 CQL 문법.** `ancestor={id} and type=page [and
   lastmodified > "..."] order by id asc` — 문법 오류 없이 동작하는지 미확인.
   0건이 나왔을 때 "정말 변경 없음"과 "쿼리가 틀림"을 구분할 수 있도록, 실행마다
   실제로 보낸 CQL 문자열 자체를 `INFO` 로그(`confluence search cql='...'
   start=... got=...`)에 그대로 남긴다.
3. **대량 페이지(TechRepo 971건 규모) 페이지네이션 안정성.** `start`/`limit`
   오프셋 기반 결정론적 페이지네이션(`order by id asc`)은 구현/단위테스트
   (mock 250건→3페이지, 다중 루트 크롤 경로 포함)했지만, 실제 규모·실제 응답
   지연에서 안정적인지는 미확인. 만약 서버가 `start`를 무시하고 매번 같은
   페이지를 반환하면(버전에 따라 v1 검색이 cursor 기반으로 바뀌었을 가능성),
   같은 id 집합이 반복되는 것을 감지해 해당 root만 중단하고 에러로 기록한다
   (무한 루프 방지) — 이 가드 자체도 실제 서버 응답으로는 미검증.

4. **(담당자 확인 필요) `confluence_docs` 프론트매터 포맷.** 의뢰서 기준
   `공간명 : LOOKIN` / `폴더분류 : <카테고리>`로 생성하도록 구현했다. 다만 현재
   리포지토리의 `data/raw/confluence_docs/`에 있는 4건(의뢰서가 말한 4,636건이
   아니라 4건뿐)은 실제로는 `디렉토리 : ...` + `공간명 : [CI-TEC]
   테크리포(Tech-Repository) Home` — 즉 tech_repo 형태로 되어 있다. 진짜
   confluence_docs 운영 데이터가 로컬에 없어 검증할 수 없었다. `제목 :`/`Page
   ID :`/`URL :`/`최종수정일 :` 필드는 공통이라 `iter_confluence_docs()`가 둘 다
   파싱은 하지만, `폴더분류` 필드명 자체가 맞는지는 최초 dry-run 결과를 담당자가
   직접 확인해야 한다.

이 네 가지를 확인하기 전까지는 `--dry-run`만 쓰고, 특히 처음에는
`--root-id`+`--max-pages`로 아주 작은 범위부터 시작할 것.

## Rate limiting (사용자 요구사항 — 원 프롬프트에는 없음)

- 요청 간격을 `CONFLUENCE_RATE_LIMIT_RPS`(기본 2 req/s)로 제한
  (`app.confluence.client.RateLimiter`).
- 크롤 구간은 `ConfluenceClient.bulk_client()`로 만든 단일 `AsyncClient`를
  재사용 — 페이지마다 새 TCP/TLS 핸드셰이크를 만들지 않는다. (기존
  `list_attachments`/`get_page_body` 등 실 운영 트래픽을 받는 메서드는
  그대로 두었다 — 건드리지 않음.)
- `AsyncHTTPTransport(retries=2)`는 연결 수준(DNS/TCP/TLS) 실패만 재시도하고
  HTTP 상태코드는 재시도하지 않는다 — 429/503은 `Retry-After` 헤더를 존중하는
  지수 백오프(최대 5회, 캡 30초)로 별도 처리(`ConfluenceClient._get_with_retry`).

## 알려진 한계

- **삭제/비공개 전환 페이지는 탐지하지 않는다.** `lastmodified >` 조건은
  추가/수정만 잡는다. 별도의 주기적 전체 재조정(reconciliation) 배치가 필요함.
- `--dry-run`은 `Source.last_sync_at`을 갱신하지 않으므로 반복 실행해도 항상
  같은(또는 bootstrap) 범위를 재크롤링한다 — 검증용으로만 쓸 것.
- `--max-pages`/`--root-id`로 범위를 좁힌 실행이나, 페이지 처리 중 에러가 하나라도
  발생한 실행은 **`last_sync_at`을 갱신하지 않는다** (부분 실행이 "성공한 전체
  동기화"로 오인되어 나머지 페이지가 영구적으로 누락되는 것을 방지). 결과 JSON의
  `sources.<type>.cursor_advanced`로 실제 반영 여부를 확인할 것.

## 운영 배포 제안 (배포/크론 등록은 하지 않음 — 담당자 승인 필요)

- 크론 주기: 앞의 3가지 라이브 미검증 항목이 확인된 뒤, 예를 들어 30분~1시간
  간격 제안 (CQL/페이지네이션이 안정적으로 확인되면 조정).
- 신규 환경변수(선택, 기본값 있음): `CONFLUENCE_RATE_LIMIT_RPS`,
  `CONFLUENCE_TIMEZONE` (기본 `Asia/Seoul`).
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
