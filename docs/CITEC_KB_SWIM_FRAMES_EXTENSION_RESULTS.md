# citec-kb: SWIM(incident_reports) IssueFrame 추출 확장 — 실행 결과

Phase 2(`CITEC_KB_SWIM_PHASE2_PROMPT.md`)에서 "SWIM 문서에 대한 IssueFrame 추출은
Phase 3 후보 — 스니펫 기반 폴백으로 충분"이라고 스코프 밖으로 미뤄뒀던 작업을
이번에 실제로 진행했다. 계기: `python -m app.frames.cli --source-type
incident_reports` 실행 결과를 요청받아 돌려봤더니 `processed: 0`으로 SWIM 문서가
단 한 건도 처리되지 않는 걸 확인 → 근본원인 2건을 특정하고 수정 → 전체
15,333건에 대해 실제로 프레임을 추출했다.

## 발견한 버그 2건

### 버그 1 — `app/frames/job.py`: `external_id LIKE 'CITECTS-%'` 필터가 source_type과 무관하게 항상 걸림

```python
.where(Document.external_id.like("CITECTS-%"))
```

SWIM의 `external_id`는 `26090761356` 같은 숫자 failSeq라 `CITECTS-%`에 매치되지
않아, `--source-type incident_reports`로 돌려도 대상 문서가 0건으로 걸러졌다.

**수정**: 이 필터를 `source_type == "support_history"`일 때만 적용하도록 분기.
(Jira raw export에는 `# 부서 소개` 같은 비-티켓 마크다운이 섞여 있어 이 필터가
필요했지만, `incident_reports`는 애초에 전부 SWIM 장애보고서 1건=1파일 구조라
동일한 필터가 불필요 — 오히려 전부 제외시키는 부작용만 있었다.)

### 버그 2 — `app/frames/extract.py`: 불릿 문자 클래스에 `■`가 없음

기존 정규식들은 라벨 앞 불릿 문자로 `-`, `*`, `•`, `○`만 인식했다. SWIM
마크다운은 `■ 장애상황:`/`■ 장애원인:`/`■ 장애조치:` 형식을 쓰는데, `■`가 그
문자 클래스(`[-*•○]`)에 없어서 버그 1을 고치고 나서도 추출이 전혀 안 됐다
(`root_cause`/`resolution` 둘 다 `null`, `sections_found: []`).

**수정**: 라벨 앞 접두 클래스와 섹션 경계(lookahead) stop-token 클래스 양쪽 모두에
`■`를 추가(`_INLINE_PATTERNS`의 6개 정규식). 겸사겸사 `symptom` 트리거 단어에
`장애\s*상황`도 추가(기존엔 `요청이슈|증상|현상`만 있어서 SWIM의 `■ 장애상황:`이
symptom 슬롯을 못 채우고 title로만 폴백하고 있었다).

## 수정 후 — 단위 테스트로 고정

`apps/api/tests/test_frames_extract.py`에 SWIM 샘플(Phase 1 어댑터 프롬프트의
"샘플 A" — 이라크 사무소 지역정전 건) 기반 회귀 테스트 추가:

```python
def test_swim_bullet_recognized():
    fr = extract_frame_from_markdown(_SWIM_MD, title="...")
    assert fr["symptom"] == "사내시스템 및 인터넷 접속 불가"
    assert fr["root_cause"] and "지역정전" in fr["root_cause"]
    assert fr["resolution"] and "전원복구" in fr["resolution"]
    assert fr["quality"] >= 0.8
```

기존 support_history 케이스 4개(`test_extract_slots_from_sample` 등)는 회귀 없이
그대로 통과 — `■`를 추가했을 뿐 기존 `-*•○` 문자 클래스에서 아무것도 빼지
않았으므로 예상대로다.

```
$ python -m pytest apps/api/tests/test_frames_extract.py -v
tests/test_frames_extract.py::test_extract_slots_from_sample PASSED
tests/test_frames_extract.py::test_quality_both_slots PASSED
tests/test_frames_extract.py::test_inline_cause_resolution_labels PASSED
tests/test_frames_extract.py::test_wiki_heading_장애원인_and_조치내역 PASSED
tests/test_frames_extract.py::test_swim_bullet_recognized PASSED
5 passed in 0.11s
```

전체 스위트:

```
$ python -m pytest apps/api/tests/ -q
1 failed, 214 passed in 5.28s
```

(실패 1건은 `test_mock_idp_e2e.py`의 로컬 postgres:5433 연결 문제 — 이번
세션 이전부터 있던 것으로, 이번 변경과 무관.)

## 단일 샘플 재현 (수정 전 → 후)

이라크 사무소(SELV-Iraq) 지역정전 건(`26090761356`)을 `extract_frame_from_markdown()`에
직접 넣은 결과:

**수정 전**
```json
{
  "symptom": "[삼성전자] 이라크 사무소(SELV-Iraq) 지역정전으로 사내시스템 및 인터넷 접속 불가",
  "root_cause": null,
  "resolution": null,
  "quality": 0.25,
  "raw_extract": {"sections_found": []}
}
```

**수정 후**
```json
{
  "symptom": "사내시스템 및 인터넷 접속 불가",
  "root_cause": "전원문제로 확인되며 고객사 건물 또는 지역 이슈인지 파악중 지역정전",
  "resolution": "전원복구 후 정상화 지역정전 해소 후 서비스 정상",
  "quality": 0.95
}
```

## 실제 CLI 실행 결과 (로컬 docker-compose, 운영과 동일 데이터 — SWIM 15,333건)

### 1) 수정 전 (버그 1만 존재하던 시점) — 이번 세션 초반 최초 요청에 대한 답

```json
{
  "processed": 0,
  "upserted": 0,
  "skipped_existing": 0,
  "skipped_low_quality": 0,
  "errors": 0,
  "source_type": "incident_reports",
  "frames_total": 2288,
  "avg_quality": 0.298,
  "with_cause_and_resolution": 139
}
```

### 2) 수정 후 — `python -m app.frames.cli --source-type incident_reports -v` (제한 없음, 전체)

```json
{
  "processed": 15133,
  "upserted": 15133,
  "skipped_existing": 0,
  "skipped_low_quality": 0,
  "errors": 0,
  "source_type": "incident_reports",
  "frames_total": 17621,
  "avg_quality": 0.57,
  "with_cause_and_resolution": 8951
}
```

(`processed=15133`인 이유: 이 실행 직전에 타이밍 테스트로 `--limit 200`을 먼저
돌려 200건이 이미 upsert돼 있었음 — `force=False`라 기존 200건은 재조회 대상에서
자동 제외됨. 200 + 15133 = 15333 = incident_reports 전체 문서 수와 정확히 일치.)

소요 시간: 약 2분(15,133건, 순수 규칙 기반 정규식 처리라 임베딩과 달리 CPU
추론 없음 — 초당 약 100건 이상).

### 3) 회귀 확인 — `python -m app.frames.cli --source-type support_history -v`

```json
{
  "processed": 13,
  "upserted": 13,
  "skipped_existing": 0,
  "skipped_low_quality": 0,
  "errors": 0,
  "source_type": "support_history",
  "frames_total": 17634,
  "avg_quality": 0.57,
  "with_cause_and_resolution": 8953
}
```

`processed=13`은 기존에 프레임이 없던 신규/누락 support_history 티켓 13건을
정상적으로 마저 처리한 것 — 버그 1 수정으로 `source_type == "support_history"`일
때는 여전히 `CITECTS-%` 필터가 그대로 적용되고 있음을 확인(회귀 없음).

### 4) DB 최종 집계 (`issue_frames` 테이블, source_type별)

| source_type | frames | avg_quality | with_cause_and_resolution |
|---|---|---|---|
| incident_reports | 15,333 | 0.610 | 8,812 (57.5%) |
| support_history | 2,301 | 0.299 | 141 (6.1%) |

SWIM 쪽 평균 품질(0.610)이 기존 support_history(0.299)보다 오히려 높게
나왔다 — SWIM 원본이 `■ 장애상황/장애원인/장애조치` 3단 구조로 이미 잘
정형화돼 있어 규칙 기반 추출과 궁합이 좋기 때문으로 보인다.

## kb_similar_incident 체감 효과 (수정 전 → 후)

쿼리: `"지역정전으로 사내시스템 및 인터넷 접속 불가"`, `top_k=10`

**수정 전** (프레임 없음 → 전부 스니펫 폴백, `frame_quality=0`):
top 10 전부 `support_history` 문서. `incident_reports`는 후보 풀(104건)에는
있었지만 `qboost`(프레임 품질 가산점)를 전혀 못 받아 랭킹 밖으로 밀림.

**수정 후**:
```
support_history:CITECTS-2595  rank=1.2075  frame_q=0.25
incident_reports:26062661216  rank=1.08    frame_q=0.8
incident_reports:26072261267  rank=1.0069  frame_q=0.65
incident_reports:26082761337  rank=0.9163  frame_q=0.4
incident_reports:26071661256  rank=0.8995  frame_q=0.4
support_history:CITECTS-2093  rank=0.84    frame_q=1.0
incident_reports:25112259670  rank=0.8367  frame_q=1.0
incident_reports:19102233742  rank=0.8129  frame_q=1.0
incident_reports:25101659345  rank=0.77    frame_q=1.0
support_history:CITECTS-2021  rank=0.72    frame_q=1.0
```
top 10 중 7건이 `incident_reports` — 여러 건이 `support_history`보다 랭킹이
높다. SWIM 전용 표현으로 질의했을 때는 더 뚜렷하다:

쿼리: `"이라크 사무소 지역정전으로 사내시스템 및 인터넷 접속 불가 SELV-Iraq"`
```
incident_reports:25071058872  이라크 사무소(SELV-Iraq) 건물 전원문제 …
support_history:CITECTS-2113
support_history:CITECTS-2531
incident_reports:24120558045  이라크 사무소(SELV-Iraq) 네트워크(WAN) 서비스 불가
incident_reports:25050558709  이라크 사무소(SELV-Iraq) 기간통신사 문제 …
incident_reports:26062661216  네덜란드 물류창고(GPCE) 지역정전 …
incident_reports:26072261267  튀니지 지점(SETN) 지역정전 …
incident_reports:25071858908  이라크 바그다드 사무소 건물 전원문제 …
support_history:CITECTS-2093
incident_reports:26082761337  튀니지 지점(SETN) 지역정전 …
```
1위가 정확히 같은 이라크 사무소(SELV-Iraq)의 유사 장애 재발 건으로 잡혔다.

## 변경 파일

- `apps/api/app/frames/job.py` — `CITECTS-%` 필터를 `support_history` 전용으로 분기
- `apps/api/app/frames/extract.py` — `■` 불릿 인식 추가(6개 정규식) + `장애상황` symptom 트리거 추가
- `apps/api/tests/test_frames_extract.py` — SWIM 회귀 테스트 추가

## 배포

로컬 docker-compose `citec-kb-api-1` 재시작으로 코드 반영 후 위 CLI를 직접
실행해 15,333건 전체에 프레임을 채웠다(이 작업 자체가 곧 "배포 후 실행"이라 별도
코드 번들 생성은 하지 않음 — 운영 서버에도 반영하려면 코드 번들 배포 후 운영에서
동일하게 `python -m app.frames.cli --source-type incident_reports`를 한 번
실행해야 한다).
