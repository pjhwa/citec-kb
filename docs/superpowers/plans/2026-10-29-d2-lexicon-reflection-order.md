# D2 lexicon 변경 → 반영 순서 정리 (정보용 계획서)

**상태: 계획서만. 이번 B1은 lexicon을 건드리지 않았다.**

## 1. B1에서 실제로 한 일
검색 전용 동의어 모듈 `apps/api/app/retrieval/search_synonyms.py`에 그룹을 넣었다(런타임, 코드 배포로 즉시 반영). `data/seeds/lexicon/core.json`·`lexicon_terms`는 변경하지 않았으므로 `lexicon_version`(= `count:max(updated_at)`, `graph/sync_cli.py::_lexicon_version`)은 그대로이고 **그래프 재백필·재임베딩은 필요 없었다**. 운영 배포·측정 결과는 `artifacts/citec-kb-retrieval-fix-20261028/` 참고.

## 2. lexicon(DB `lexicon_terms`)을 바꿀 때 영향
| 변경 | 반영 시점 | 추가 작업 |
|---|---|---|
| `build_fts_variants`가 쓰는 `load_lexicon_map()` 동의어 | seed 적용 후 캐시 지문(count, max updated_at) 변경 시 즉시 | 없음 (단 쿼리 확장이 넓어져 홀드아웃 게이트 필요) |
| 그래프 `HAS_COMPONENT` 컴포넌트 매칭 | 다음 그래프 배치부터. 기존 엣지는 **그대로** | 전체 반영 = 그래프 전체 재백필(`scripts/graph_sync.sh`; 운영 승인). 증분만 하면 신규·변경 문서만 새 사전을 쓴다 |
| 임베딩 텍스트(청크 `header_context`/제목 보강) | 재임베딩 후 | 전체 재임베딩(대용량·시간·GPU/CPU), 별도 승인 |
| `kb_list_checkitems`의 `_expand_terms` 하드코딩 | 코드 배포 | 사전으로 옮기면 목록 API와 검색이 같은 사전을 쓴다 (아직 안 함) |

## 3. 최소 범위 반영 순서 (제안)
1. 새 동의어는 먼저 **검색 전용 모듈**에 넣고 게이트로 검증 → 코드 배포만으로 반영(현재 방식).
2. 그래프에서도 같은 용어를 컴포넌트로 인식해야 하는 것만 골라 lexicon seed에 추가(`seed_lexicon()` 기본 보존 모드; **`--replace` 금지**) — dry-run(`--dry-run`)으로 변경 계획 확인.
3. 그래프 증분 동기화 → 필요할 때만 전체 재백필(승인).
4. 임베딩 텍스트 변경은 효과가 입증된 뒤 마지막.
- 1~2자 한글 용어 추가 금지(과거 "행→hang" 오탐).
