# kb_recent_changes — Confluence 최근 변경/추가 조회 (설계)

작성: 2026-10-20 · 상태: 사용자 승인(설계 구두 승인) · 범위: Confluence 3종
(`confluence_map`, `confluence_docs`, `tech_repo`)

## 목적
KB에 반영된 Confluence 문서 중 "최근 수정된 글"과 "KB에 새로 들어온 글"을 MCP에서
조회한다. 현재는 DB(`documents`)를 직접 조회하는 방법뿐이다.

## 구조
MCP 도구 `kb_recent_changes` → `GET /v1/documents/recent-changes` →
`app.documents.recent_changes.list_recent_changes()` (MCP 서버는 DB에 직접 붙지 않고
API만 호출한다 — `kb_list_tickets`와 같은 패턴).

## 입력
| 파라미터 | 기본 | 설명 |
|---|---|---|
| `relative` | – | "최근 7일", "지난 주" 등. 기존 `parse_relative_range`(KST) 재사용 |
| `date_from`/`date_to` | – | ISO date. `relative`와 같이 주면 `relative` 우선. 둘 다 없으면 최근 7일 |
| `change_type` | `modified` | `modified`(Confluence 수정 시각) \| `added`(KB 최초 수집 시각) \| `all`(둘 중 하나라도 범위 내) |
| `source_types` | 3종 전부 | 콤마 구분. 3종 외 값은 400 |
| `space_key` | – | 대소문자 무시. 아래 "공간 키" 참고 |
| `title_contains` | – | 제목 부분 일치(대소문자 무시) |
| `include_folders` | false | `confluence_map`의 `유형=폴더`(이정표 컨테이너) 페이지 포함 여부 |
| `limit`/`offset` | 30/0 | limit 1~200 |

## 의미 (오해 방지)
- **modified**: `metadata->>'source_modified_at'`(Confluence `version.when`, ISO8601, 오프셋 포함).
  KB 반영은 최대 약 2시간(`confluence_sync`) / 6~9시간(`map_sync`) 지연될 수 있다.
- **added**: `documents.created_at` = KB가 처음 본 시각. Confluence 페이지 생성일이
  **아니다**(저장하지 않음). 초기 일괄 수집분은 같은 시각이라 최근 범위에서만 의미가 있다.
- `source_modified_at`이 없는 문서(10/01 백필 이전 수집분 등)는 `modified` 결과에서 빠진다.
  빠뜨림을 숨기지 않기 위해 응답에 `missing_modified_at`(범위 내 source_type 중
  값이 없는 활성 문서 수)을 담는다.
- `status='active'`만 대상. 정렬은 `modified`/`all`은 수정 시각 내림차순(없으면
  `created_at`), `added`는 `created_at` 내림차순.
- 기간 경계: `[date_from 00:00, date_to+1일 00:00)` KST.

## 메타데이터 키 (source_type별로 다름 — 코드로 확인)
| 항목 | confluence_map | confluence_docs | tech_repo |
|---|---|---|---|
| 공간 키 | `space_key` | `공간명` (키 값이 들어 있음) | `공간명` |
| 경로 | `경로` | `폴더분류` | `디렉토리` |
| 폴더 구분 | `유형='폴더'` | – | – |
| page id | `documents.external_id` (3종 공통) | | |
| URL | `documents.source_uri` (3종 공통) | | |

공간 키 필터는 `lower(coalesce(metadata->>'space_key', metadata->>'공간명')) = lower(:key)`.
(`confluence_map`의 `공간명`은 공간 표시 이름이라 키로 쓰지 않는다.)

## 출력 (문서당)
`title`, `source_type`, `space_key`, `path`, `page_id`, `url`, `source_version`,
`source_modified_at`, `kb_created_at`, `kb_updated_at`, `change`(`modified`/`added`/`both`).
응답 최상위: `total`, `limit`, `offset`, `range`(label/from/to), `change_type`,
`missing_modified_at`.

## 오류 처리
- 인식 못하는 `relative` → 400. `date_from > date_to` → 400. 잘못된 `change_type`/`source_types` → 400.
- `source_modified_at` 값 형식이 이상한 문서는 정규식 가드로 `NULL` 취급(쿼리 전체 실패 금지).
- 읽기 전용. DB 마이그레이션 없음(인덱스 미추가 — 운영에서 느리면 표현식 인덱스 추가).

## 테스트 (임시 DB, 개발 DB 미접촉)
수정/신규/all 판정, 기간 경계(KST), 시각 없는 문서와 `missing_modified_at`, 형식
깨진 시각 값, 비활성(archived) 제외, 폴더 제외/포함, 공간 키 필터(map vs docs/tech),
`title_contains`, 페이징·정렬, 잘못된 입력 400. MCP 도구는 모의 응답으로 포맷 검증.
핵심 조건은 변이 검증(조건을 빼면 테스트가 실패)으로 확인한다.

## 문서
`kb_tools_help()`와 `docs/AI_AGENT_GUIDE.md`에 도구 설명 추가(조회 대상·의미·한계).

## 범위 밖
Confluence 페이지 생성일 저장, 삭제/이동 감지, 변경 내용 diff, 다른 source_type.
