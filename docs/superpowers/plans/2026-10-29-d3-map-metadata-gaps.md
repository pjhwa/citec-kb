# D3 confluence_map 메타 잔여 결측 분류·재크롤 계획 (실행 전 승인 필요)

**상태: 계획서만.** `docs/CITEC_KB_MAP_COVERAGE_GAP.md`(스페이스 커버리지 갭)와 별개 항목이다: 그 문서는 "크롤 범위 밖 폴더"를, 이 계획은 "크롤된 행의 메타 결측"을 다룬다. 해당 문서의 남은 결정(전체 스페이스 크롤 미채택, 자동 탐지 미구현)은 그대로 둔다.

## 1. 현황 (dev, 읽기 전용 2026-10-29; 운영 보고치 136/325/119와 규모가 비슷)
| 메타 | 결측(dev) | 운영 보고 |
|---|---|---|
| `ancestor_ids` | 120 | 136 |
| `source_version` | 312 | 325 |
| `tech_relevant` | 104 | 119 |
- 결측 분포: `ICLOUDUT` 47/47/38, `LOOKIN` 40/40/37, `TechRepo` 33/33/29 — 세 스페이스가 `ancestor_ids`·`source_version`을 **같은 문서 집합에서 함께** 결측. `DevOps001` 184건은 `source_version`만(이 스페이스 전체 10,661건 중), `GUID`/`CATT`/`STORAGE`는 2~3건.
- 결측 행의 메타 키는 `경로/구분/루트/유형/제목/공간명/최종수정일/Page ID/space_key/URL/filename` 위주 → **스킬 인덱스에서 이관된 구형 행(`migrate_confluence_map_from_skill_index.py`)처럼 보인다**: Confluence API 크롤 메타가 아직 채워지지 않은 행.

## 2. 분류 가설과 확인 방법 (읽기 전용)
- (a) 구형 이관 행이라 API 크롤로 아직 갱신되지 않음 → `scripts/map_backfill.sh --dry-run`(소스별 예상 시간) 후 `--source-ids confluence_map_<space>` 로 해결 가능.
- (b) 원본 페이지가 삭제/이동/권한 없음 → 재크롤해도 안 채워짐. 결측 문서 `Page ID`를 Confluence `getPageByID`로 샘플 20건 조회해 404/403 비율을 본다.
- (c) 크롤 범위 밖 서브트리 → 위 갭 문서의 `scripts/audit_map_root_coverage.py --depth 3+` 결과와 대조.
```sql
select metadata->>'space_key' sp, count(*) from documents
where source_type='confluence_map' and status='active' and metadata->>'source_version' is null
group by 1 order by 2 desc;
```

## 3. 실행 계획 (승인 후)
1. 위 샘플 분류(읽기 전용)로 (a)/(b)/(c) 비율 확정.
2. (a)만 대상: 스페이스별 순서로 `scripts/map_backfill.sh --source-ids … --foreground` (레이트리밋 기본 0.3 rps — 수백 건 규모, 수 분~수십 분 추정; 정확한 시간은 `--dry-run` 출력으로 확정). 실행 시간대는 map_sync 크론(하루 3회)과 겹치지 않게.
3. 완료 후 임베딩은 백필 스크립트가 처리. 그래프 동기화는 영향 문서만 증분.
4. 롤백: 크롤은 메타를 채우는 upsert라 되돌릴 필요가 없다. 문제가 생기면 `status` 가 바뀐 문서만 확인.
- 검증: 위 SQL의 결측 수 전/후, `GET /v1/ops/dashboard` `coverage_gaps`, `section=confluence_map` 검색 스모크.
- 영향: `source_version` 결측 → 최신성 판단(`map_synced_at`) 정확도, `ancestor_ids` 결측 → `exclude_subtree_ids` 가 이 문서들을 제외하지 못함.
