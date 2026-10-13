# 2026-10-14 F05(R04) 부분 완료 보고

**범위: F05(R04, 사전 변경/lifecycle 재계산) 일부.** 사용자 지시로 "F05를 부분 완료로 문서화하고 F06 홀드아웃으로 넘어간다" — 남은 F05 범위는 아래 "미착수" 절에 이유와 함께 남긴다. 커밋 `a57beda`. 아직 운영에 배포되지 않음(F01~F04만 배포 완료, `a8fadda`).

## 결론

조사 결과 실제로 재현 가능한 결함 두 건을 고치고 테스트로 검증했다. 세 번째 의심 결함은 실측 결과 버그가 아님을 확인했다. 핸드오프 §3.4가 요구하는 "백필 필요한 변경은 별도 승인 대상"에 해당하는 더 큰 작업(의존성 revision + invalidation queue, namespace 분리)은 이번 라운드에서 손대지 않았다.

| 항목 | 상태 |
|---|---|
| anchor 대표/제외 불일치 (V09 일부) | ✅ 수정+검증 |
| archived→active 복원 비대칭 (V09 일부) | ✅ 수정+검증 |
| lexicon_version no-op 의심 버그 | 조사 결과 버그 아님 — 회귀 가드만 추가 |
| 의존성 revision + invalidation queue (V08) | 미착수 — 마이그레이션 필요, 별도 승인 대상 |
| namespace 분리 (V10) | 미착수 — 기존 참조 edge가 재백필 전까지 stale해지는 변경, 별도 승인 대상 |
| 비활성/archived 노출 정책 (opt-in) | 미착수 |
| 운영 201건 unresolved_references 분류 | 미착수 — dev는 재백필 안 된 상태라 샘플이 운영을 대표 못 함, 사용자가 운영에서 직접 돌려야 함 |

## 고친 것

### 1. `/v1/graph/explore` document anchor — resolved_id가 제외된 대표를 계속 가리킴

`exclude_source_types`가 대표 사본(예: tier 1 `tech_repo`)을 제외해도, 응답의 `anchor.resolved_id`는 여전히 그 대표를 가리켰다 — 실제 탐색(`anchors` 리스트)은 남은 사본(`confluence_map` 등)으로만 돌았는데, 호출자는 `resolved_id`만 보고 "그 문서로 탐색했다"고 오해할 수 있었다. `anchor.traversal_start_ids`(실제 탐색이 시작된 사본 목록)와 `anchor.representative_excluded`(대표가 제외됐는지 bool)를 추가해 분리 노출한다. `kb_graph_explore`(MCP)에도 같은 정보를 한 줄로 렌더링한다.

재현: `test_graph_router.py::test_explore_document_anchor_reports_representative_excluded_when_filtered` — 대표(tech_repo) 제외 시 `resolved_id`는 그대로, `representative_excluded=True`, `traversal_start_ids=[남은 사본]`. 대조군 `..._is_false_when_nothing_excluded`도 추가.

### 2. archived 문서가 본문 그대로 재크롤되면 영원히 archived로 고착

코드 전수조사(`Document.status`의 모든 writer를 찾는 전용 조사) 결과, 이 코드베이스엔 **"사람이 개별 문서를 수동 archive"하는 API/CLI 경로가 아예 없다** — archived는 전부 (a) 크롤 감지 자동 archive(`confluence/map_sync.py`), (b) 공간 단위 bulk CLI-only archive(`confluence/map_space_setup.py --apply`, HTTP 라우트 없음), (c) 인입 파이프라인의 insert/update 중 하나뿐이다.

`app.ingest.pipeline._upsert_document`의 content_hash 불변 skip 분기는 `status`를 전혀 안 건드렸다 — archived된 문서가 본문 변경 없이 재크롤되면(흔한 경우) 영원히 archived로 남았다. 바로 아래(hash 변경 또는 활성 chunk 0) 분기는 이미 무조건 `status="active"`로 복원한다 — 같은 "원본이 다시 보였다"는 신호에 두 분기가 다르게 반응하는 비대칭을 대칭으로 고쳤다(`superseded`/`draft_insight`는 애초에 코드 어디서도 안 쓰여 건드릴 필요가 없었음을 조사로 확인).

재현: `test_confluence_sync_db.py::test_reingest_with_unchanged_body_restores_archived_status` — bulk archive를 흉내낸 뒤 같은 본문으로 재크롤하면 `status`가 `active`로 복원되고 결과가 `"updated"`(과거엔 `"skipped"`에 status 변화 없음).

### 3. (버그 아님으로 확인) lexicon_version이 no-op seed마다 바뀌는지 의심

F05가 쓰려는 "lexicon revision 변경 시 재계산" 신호가 `lexicon_version = f"{count}:{max(updated_at)}"`인데, `seed_lexicon()`의 apply 루프가 변경이 없는 행도 `row.variants/priority/metadata_`를 무조건 재할당해서 `onupdate=func.now()`가 매번 `updated_at`을 갱신시키는지 의심했다. 실측 결과 **버그가 아니었다** — SQLAlchemy는 재할당 값이 이미 로드된 값과 같으면(JSONB dict도 내용이 같으면) dirty로 표시하지 않아 UPDATE 자체가 발생하지 않는다. 앞으로의 회귀를 막기 위해 `test_lexicon_seed_db.py::test_seed_lexicon_noop_does_not_bump_updated_at`을 가드로 남겼다.

## 검증

전체 apps/api 유닛 스위트(474개 중 472 passed — 무관한 사전 환경 문제 2건: `sentence_transformers` 미설치, mock IDP용 별도 포트 미기동) + 관련 DB 통합 테스트(`test_graph_router.py` 34건, `test_lexicon_seed_db.py` 10건, `test_confluence_sync_db.py` 13건 중 12건, `test_map_integrity_round_db.py` 4건) 전부 통과. 상세는 `environment.json`의 `test_environment` 절 참고.

## 미착수로 남긴 것 (이유)

- **의존성 revision + invalidation queue(V08)**: lexicon revision 변경, 후적재된 참조 대상, representative 교체 시 재계산을 트리거하려면 신규 테이블+마이그레이션이 필요하다. 핸드오프 §3.4: "운영 반영이 필요하면 영향 diff·backfill 범위·중단 기준·복구안을 구체화한 뒤 별도 승인 대상으로 제시하라" — 이 게이트 없이 코드를 쓰지 않았다.
- **namespace 분리(V10)**: `build_page_identity_index`가 지금은 source_type 기반 하드코딩 allowlist(3곳에 중복 구현됨: `identity.py`, `pipeline.py`, `explore.py`)로 우연히 분리돼 있을 뿐, 명시적 identity-domain(페이지 vs 티켓) 필드가 없다. 이걸 바꾸면 기존 참조 edge의 해석이 달라져 재백필 전까지 stale해진다 — 역시 별도 승인 대상.
- **비활성/archived 노출 정책**: 기본값 + opt-in 플래그를 아직 설계하지 않았다.
- **운영 201건 unresolved_references 분류**: dev는 이 기능이 생긴 뒤로 재백필을 한 적이 없어 샘플이 운영을 대표하지 못한다 — 읽기 전용 분류 쿼리를 사용자가 운영에서 직접 돌려야 한다(아직 작성 안 함).

## 다음 단계

사용자 지시에 따라 F06(R05, 관련성 랭킹) 홀드아웃 질문셋 제안으로 넘어간다 — 별도 문서로 제출.
