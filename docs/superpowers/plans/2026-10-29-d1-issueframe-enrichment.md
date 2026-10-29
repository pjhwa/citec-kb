# D1 지원이력 IssueFrame 보강 계획 (실행 전 승인 필요)

**상태: 계획서만. 실행하지 않는다.** 재생성은 사용자 승인 후 운영 서버에서 사용자가 실행한다.

## 1. 현황 (dev DB, 읽기 전용 측정 2026-10-29)
| 항목 | 값 |
|---|---|
| support_history 활성 문서 / frame | 2,378 / 2,371 (frame 없음 7) |
| root_cause 있음 / resolution 있음 / 둘 다 | 236 / 221 / **154–152 (약 6.5%)** — 프롬프트 운영 수치 6.1%와 같은 수준 |
| incident_reports(SWIM) 둘 다 | 9,947 / 15,358 (64.8%) |
| 본문에 "원인" 단어가 있는 문서 | 758 (이 중 root_cause 비어 있음 **523**) |
| 본문에 "조치/해결/해소/복구" 단어가 있는 문서 | 953 (이 중 resolution 비어 있음 **734**) |
| `raw_extract` | `{"method":"rules_v2","sections_found":[],"used_llm_summary":false}` — 섹션을 하나도 못 찾는 문서가 표본 다수 |
| extractor_version | 전부 `2026-09-30.1` (17,727행), body_hash NULL 2행 |

**분류 가설(검증 필요):** (a) 추출기 한계 — 본문에 "원인/조치" 단어가 있는데 frame이 비어 있는 문서(약 523/734건 상한). 단어 존재 ≠ 원인 서술이므로 실제 비율은 더 낮다. (b) 원문에 원인·조치가 없는 문서(진행 중/문의성 티켓). 원문에 없는 것은 만들지 않는다.

## 2. 선행 조사 (읽기 전용, 승인 불필요)
1. 운영에서 `GET /v1/ops/dashboard` 의 `coverage_gaps`(support_history `missing_frame`)와 아래 SQL로 충족률을 재측정한다.
```sql
select count(*) n,
       count(*) filter (where coalesce(root_cause,'')<>'' and coalesce(resolution,'')<>'') both_fr
from issue_frames f join documents d on d.id=f.document_id
where d.source_type='support_history' and d.status='active';
```
2. 무작위 60건(`order by md5(external_id)`)을 사람이 읽고 `원인·조치 서술 있음 / 없음 / 부분`으로 분류한다. 이 표본의 "있음 & frame 비어 있음" 비율이 추출기 개선의 상한이다.
3. 비어 있는 문서의 본문 섹션 제목 패턴(`원인`, `조치 내용`, `Root Cause`, `Resolution`, `해결 방법` 등)을 집계해 `frames/extract.py` 규칙의 누락 패턴을 찾는다.

## 3. 변경안 (코드는 승인 후 별도 PR)
- `frames/extract.py`: 2에서 찾은 헤딩/라벨 패턴을 규칙에 추가. 질문·티켓 ID 하드코딩 금지, 원문에 없는 문장 생성 금지(`used_llm_summary=false` 유지). `EXTRACTOR_VERSION` 상승.
- 평가: 위 사람 표본 60건의 recall/precision 전/후, 원문에 없는 필드가 채워진 건수 0 확인.

## 4. 재생성 실행 계획 (승인 후)
- 대상: `support_history` 활성 문서 약 2.4천 건(`extract_frames(force=False)`는 body_hash·extractor_version 불일치 건만 다시 만든다 → 버전 상승 시 전체가 대상).
- 소요: 규칙 기반이라 CPU 수 분 추정(fetch body_md ~2.4천 건). 임베딩·그래프 영향: frame 변경은 `kb_similar_incident` 입력이며 청크 임베딩에는 영향 없음. 그래프(`HAS_COMPONENT` 등)는 frame의 components를 쓰면 **그래프 증분 동기화 대상**이 될 수 있다 → 실행 전 `graph_sync_state` 확인.
- 순서: ① 운영 DB 백업(`scripts/backup_postgres.sh`) → ② 새 extractor 배포 → ③ `docker compose exec api python -m app.frames.job --source-type support_history`(정확한 CLI는 승인 전 `--help`로 확인) → ④ 검증 쿼리 재실행.
- 중단/롤백: 작업은 upsert라 중단 후 재실행 가능. 되돌리기는 ①의 백업에서 `issue_frames`만 복원(또는 `extractor_version='2026-09-30.1'` 행을 백업 테이블에서 복사).
- 검증: 충족률 전/후, 표본 60건 재분류, `kb_similar_incident` 스모크(2502, 2634) 순위 유지.
