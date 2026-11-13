# LOOKIN 기술지원 문서 ↔ SWIM 장애 매칭, 개발·운영 동기화

DOgether 매처(`docs/DOGETHER_SWIM_MATCHING.md`)와 같은 방식으로, LOOKIN(Confluence `LOOKIN` 공간)의
`Ⅲ. CI-TEC 기술지원` 문서를 SWIM 장애(`incident_reports`)에 맞춘다. **auto는 꺼져 있고 review 후보만 만든다.**

## 대상과 날짜

- 대상: 연도 폴더(`NN년 기술지원`) **바로 아래** 문서 510건(개발 기준; 하위 문서 약 3,000건은 제외).
- 지원 날짜 = 제목 `[MM/DD ...]`의 월·일 + 폴더의 연도. 범위(`[10/08 ~ 10/27]`, `[09/25-11/12]`)는 시작일만 쓴다.
- 날짜를 못 만드는 문서(`[21-04]` 같은 월만 있는 표기, 대괄호 없는 제목)는 후보 창이 없어 매칭하지 않는다 — 개발에서 100건.
- 후보 = 그 날짜 ±`date_tolerance_days`(1)일의 SWIM 장애. 점수(날짜·임베딩 유사도·구성요소·분야)와 판정은 DOgether와 같다.
- 연도 폴더 밖 문서(개인 공간·그룹과제 등)는 대상이 아니다.

## 저장·실행

테이블 `lookin_swim_matches` / `lookin_swim_match_runs` (마이그레이션 `20261114_0026`, dogether 테이블과 같은 구조).
설정 `config/lookin_swim.json`. `confirmed`·`rejected`·`manual` 행은 매처가 바꾸지 않는다.

```
python -m app.matching.lookin_swim --eval | --dry-run | --apply | --export-review CSV | --import-review CSV
```

## 개발 데이터 결과 (2026-11-14)

| | 문서 | review | 후보 없음 | 날짜 없음 |
|---|---|---|---|---|
| LOOKIN 기술지원 | 510 | 402 | 8 | 100 |
| DOgether 번호 없는 장애사례 | 535 | 535 | 0 | 31 |

가정한 기준(T1=0.5, 점수 차 M)에서 auto가 될 문서 수 (저장하지 않는 참고치):

| DOgether (연도) | 글 | M≥0.05 | M≥0.10 | M≥0.15 |
|---|---|---|---|---|
| 2019 | 38 | 26 | 6 | 1 |
| 2020 | 59 | 38 | 21 | 8 |
| 2021 | 45 | 27 | 16 | 11 |
| 2022 | 62 | 29 | 16 | 9 |
| 2023 | 93 | 54 | 29 | 17 |
| 2024 | 120 | 71 | 35 | 13 |
| 2025 | 96 | 59 | 34 | 18 |
| 2026 | 22 | 16 | 13 | 7 |

SWIM 장애는 연 400~1,800건이라 오래된 해일수록 같은 날 후보가 많아 점수 차가 작다(2019년 M≥0.10은 38건 중 6건).

### 정답 근거의 한계

- DOgether는 번호가 있는 65건으로 평가했다(M≥0.10에서 auto 15건 전부 정답). 번호 없는 글에 같은 정밀도가 나온다는 보장은 없다.
- **LOOKIN은 평가할 정답이 사실상 없다.** 본문에 SWIM 번호가 적힌 기술지원 문서는 14건이고, 그중 11건은 번호가
  개발의 SWIM에 없고 2건만 날짜 후보에 정답이 들었다. 이 표본으로는 기준값을 정할 수 없다.
- 기술지원 문서에는 장애가 아닌 것(테스트·컨텐츠 개정·교육 등)이 섞여 있어, 날짜가 맞는 장애가 있어도 같은 건이 아닐 수 있다.
  → LOOKIN은 사람이 review 후보를 확인(`--export-review` → `decision` 열 → `--import-review`)한 뒤 기준을 정한다.

## 개발 ↔ 운영 동기화 `python -m app.matching.sync_matches`

문서 id가 아니라 `(source_type, external_id)`로 가리키므로 환경이 달라도 옮길 수 있다.

| 명령 | 동작 |
|---|---|
| `--export FILE.jsonl` | 매칭 행(두 테이블)과 문서 목록(dogether_preventive 전체, LOOKIN 기술지원, incident_reports: source_type·external_id·content_hash)을 내보냄 |
| `--diff A.jsonl B.jsonl` | 한쪽에만 있는 문서·매칭 행, 내용(hash)이 다른 문서, 상태가 다른 행, 상대 환경에 문서가 없어 옮길 수 없는 행을 센다. 쓰기 없음 |
| `--import FILE.jsonl` | 미리보기(기본). `--apply`를 주면 쓴다. `--only-human`은 사람 결정(manual)만 가져온다 |

가져올 때 규칙: 대상의 confirmed·rejected·manual 행은 덮어쓰지 않는다. 대상에 없는 쌍은 넣는다. 대상의 content 행은
들어오는 행이 사람 결정일 때만 바꾼다. 대상에 문서가 없는 쌍은 건너뛰고 건수를 알려준다.

### 권장 절차

1. 개발에서 `--apply` 후 `--export-review`로 검토 CSV를 만든다. 사람이 `decision`(confirmed|rejected)을 채워 `--import-review`.
2. 개발: `sync_matches --export dev.jsonl`, 운영: `sync_matches --export prod.jsonl` (api 컨테이너 안, `docker compose cp`로 꺼냄).
3. `sync_matches --diff dev.jsonl prod.jsonl` — 운영에만 있는 SWIM·문서(= 운영이 더 최신)와 개발에만 있는 문서를 확인.
4. 운영에서 `sync_matches --import dev.jsonl` 미리보기 → 건수 확인 → `--apply`. 운영 SWIM이 더 최신이면
   운영에서 `lookin_swim`/`dogether_swim --apply`를 직접 돌려 새 장애까지 후보를 만들고, 개발의 **사람 결정만**
   `--only-human`으로 가져오는 편이 안전하다.
5. 운영 반영 전 DB 백업(마이그레이션 `20261114_0026`은 새 테이블 2개 추가뿐이며 기존 테이블은 건드리지 않는다).

그래프·API 연결은 아직 없다 — LOOKIN 매칭은 저장과 검토까지만 한다(기준을 정한 뒤 별도로 추가).
