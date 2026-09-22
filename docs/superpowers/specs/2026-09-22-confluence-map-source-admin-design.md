# Confluence 맵 동기화 공간 관리자 추가/제거 — 설계

날짜: 2026-09-22
작성자: Jerry Park + Claude

## 배경

`apps/api/app/confluence/map_sync.py`의 `MAP_SOURCE_DEFS`는 confluence_map 동기화 대상
12개 공간(space_key, root page, explicit_pages)을 Python 코드에 하드코딩하고 있다.
새 공간을 추가하거나 기존 공간을 동기화 대상에서 빼려면 코드를 수정하고 재배포해야 한다.
`admin.html`의 "Confluence 맵 동기화" 카드는 현재 이 목록을 읽기 전용으로만 보여준다
(`GET /v1/confluence-map/status`).

이 설계는 admin.html에서 관리자가 공간을 추가/비활성화할 수 있도록 만든다.

## 목표

- 관리자가 admin.html에서 새 공간(space_key + root 1개 또는 explicit page 1개)을 등록할 수 있다.
- 관리자가 기존 공간을 비활성화/재활성화(토글)할 수 있다.
- 비활성화된 공간은 다음 `confluence_map_sync` 실행 대상에서 제외되지만, 이미 인제스트된
  문서(검색 인덱스)는 그대로 유지된다 — 별도 정리 작업 없음.

## 비목표

- 공간당 여러 root/explicit page를 한 번에 등록하는 UI (여러 root가 필요한 복잡한 공간은
  여전히 DB/코드 직접 수정으로 처리)
- 비활성화 시 기존 문서 삭제/아카이브
- 개별 root/explicit page 단위 제거 (공간 전체 단위로만 활성/비활성 전환)

## 저장소 구조

기존 `sources` 테이블(`Source` 모델, `type="confluence_map"`)을 그대로 재사용한다.
스키마 변경은 없다 — `config`(JSONB), `status`(문자열) 컬럼이 이미 존재한다.

- `config`에 저장하는 키를 `space_key`, `space_name`, `roots`(dict[page_id, label]),
  `explicit_pages`(dict[page_id, label]), `checkpoint`(기존, 런타임 진행상태)로 확정한다.
- `status`를 `"active"` / `"disabled"` 전환에 사용한다 (현재는 항상 `"active"`로만 쓰임).
- 코드 상수 `MAP_SOURCE_DEFS`는 제거하고, 다음 함수로 대체한다:

  ```python
  def get_source_defs(active_only: bool = False) -> dict[str, dict[str, Any]]:
      """DB의 confluence_map 소스 행을 MAP_SOURCE_DEFS와 동일한 모양의 dict로 반환."""
  ```

  반환 shape: `{source_id: {"space_key", "space_name", "roots", "explicit_pages"(선택)}}`
  — 기존 `MAP_SOURCE_DEFS[source_id]`를 참조하던 모든 코드가 그대로 동작하도록 맞춘다.

- 기존 12개 하드코딩 공간은 **1회성 Alembic 데이터 마이그레이션**으로 DB에 upsert한다
  (idempotent — `ON CONFLICT (id) DO NOTHING`, 이미 실행돼서 checkpoint/last_sync_at이
  쌓인 행은 덮어쓰지 않음).

## 백엔드 API (`apps/api/app/routers/confluence_map.py`)

모두 기존과 동일하게 `require_roles("admin")`로 보호.

### `GET /v1/confluence-map/status` (기존, 확장)

각 source에 `space_name`, `status`를 추가로 포함하도록 응답을 확장한다.

### `POST /v1/confluence-map/sources` (신규)

```json
{
  "space_key": "NEWSPACE",
  "space_name": "새 팀 공간",
  "page_id": "123456789",
  "label": "전체 공간 (New Space Home)",
  "is_explicit_page": false
}
```

- `source_id`를 `confluence_map_{space_key.lower()}`로 자동 생성.
- 이미 같은 `source_id`가 있으면 409 반환 (덮어쓰지 않음 — 기존 공간 수정은 이 설계 범위 밖).
- `is_explicit_page=false`면 `config.roots = {page_id: label}`,
  `true`면 `config.explicit_pages = {page_id: label}`로 저장. `status="active"`로 생성.

### `PATCH /v1/confluence-map/sources/{source_id}` (신규)

```json
{ "status": "active" }
```
or `{ "status": "disabled" }`. 존재하지 않는 source_id는 404.

### 동기화 로직 변경

`sync_map()`, `run_map_inventory()`, `apps/api/app/confluence/map_sync_cli.py`는
`MAP_SOURCE_DEFS` 대신 `get_source_defs(active_only=True)`를 사용하도록 변경한다.
`_ensure_source_row()`(첫 실행 시 지연 생성 로직)는 더 이상 필요 없다 — 레지스트리 행이
`POST /sources` 또는 마이그레이션으로 미리 존재해야 `sync_map()`이 그 소스를 돈다.

## admin.html UI

"Confluence 맵 동기화" 카드 변경:

- 기존 테이블(`source_id`, `space`, `last_sync_at`, `checkpoint`)에 `space_name`, `상태`
  (active/disabled 배지), `토글` 버튼 컬럼 추가. 토글 클릭 시 `PATCH /sources/{id}` 호출 후
  `refreshMapSync()` 재호출.
- 카드 하단에 "새 공간 추가" 인라인 폼(5개 입력: space_key, space_name, root page ID,
  label, "explicit page" 체크박스) + 등록 버튼 → `POST /v1/confluence-map/sources`.
  성공 시 폼 초기화 + 목록 새로고침, 실패(409 등) 시 에러 메시지 표시.
- 안내 문구("LOOKIN/TechRepo/.../EMCloud 9개 공간 순차 동기화…")는 하드코딩된 공간
  나열 대신 "N개 활성 공간 순차 동기화…" 형태로 동적 생성.

## 테스트

- `test_confluence_map_sync.py`: `MAP_SOURCE_DEFS` 상수 직접 참조 대신, DB에 확인용
  source 행을 seed하는 fixture를 도입해 `get_source_defs()` 동작을 검증.
- `test_confluence_map_inventory_db.py`: 동일하게 DB seed 기반으로 전환.
- 신규: `POST /sources`(생성/409 중복), `PATCH /sources/{id}`(상태 전환/404),
  `get_source_defs(active_only=True)`가 disabled 소스를 제외하는지 검증하는 테스트 추가.
- 신규: 1회성 Alembic 데이터 마이그레이션이 idempotent한지(재실행해도 기존 checkpoint를
  덮어쓰지 않는지) 검증.

## 마이그레이션 순서 (구현 계획에서 구체화)

1. Alembic 데이터 마이그레이션: 기존 12개 `MAP_SOURCE_DEFS` 항목을 `sources` 테이블에 upsert.
2. `get_source_defs()` 추가, `MAP_SOURCE_DEFS` 참조부 전체를 이 함수 호출로 교체.
3. `POST /sources`, `PATCH /sources/{id}` 엔드포인트 추가.
4. `MAP_SOURCE_DEFS` 상수 및 `_ensure_source_row()` 제거.
5. admin.html UI 추가.
6. 테스트 마이그레이션.
