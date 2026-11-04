# ToolBox 도구 ↔ citec-kb failure bucket 연동 가이드

**독자:** CI-TEC ToolBox 도구를 만드는 개발자. KB 내부 구조를 몰라도 이 문서만으로 연동을 구현하고, 시험하고, 신청할 수 있어야 한다.
**버전:** 2026-11-04.1

> **Claude 스킬 작성자라면** 이 문서가 아니라 [FAILURE_BUCKET_PLUGIN_GUIDE.md](./FAILURE_BUCKET_PLUGIN_GUIDE.md)를 본다. 이 문서는 서버가 REST로 직접 호출하는 도구용이다. 두 문서가 겹치는 규칙(`fb_domain` 고르기, 신호 작성 기준, `evidence_ref` 작성 기준)은 가이드를 링크하고 여기에 복사하지 않는다.

---

## 0. 한눈에 보기

**무엇을 호출하나**

1. 분석을 시작할 때 `POST /v1/failure-buckets/match`로 과거에 확인된 패턴이 있는지 조회한다.
2. 분석이 끝났을 때 새 패턴이면 `POST /v1/failure-buckets`로 등록하고, 기존 패턴이 맞았거나 아니었으면 `POST /v1/failure-buckets/{id}/refine`으로 되먹임한다.

**무엇을 지켜야 하나**

| # | 규칙 |
|---|---|
| 1 | **KB 호출이 실패해도 도구의 본 기능은 끝까지 가야 한다.** KB는 부가 기능이다. 타임아웃·5xx·연결 실패에서 분석 결과 제공을 막지 않는다. |
| 2 | 등록 응답에 `possible_duplicate_of`가 있으면 **다시 등록하지 말고 refine**을 호출한다. |
| 3 | `created_by`는 `<도구>@<판>`(예: `sos-analyzer@v0.1`). 시험 중에는 끝에 `-test`를 붙인다(운영 검색에 섞이지 않는다). |
| 4 | `evidence_ref`는 **사람이 나중에 열 수 있는 영구 위치**를 가리킨다. 도구가 30일 뒤 지우는 파일이나 로그인이 필요한 화면만 쓰지 않는다. 결과 보고서를 Confluence에 올리고 `confluence:<page id>`를 쓴다. |
| 5 | 4xx는 재시도하지 않는다. 타임아웃·5xx·연결 실패만 최대 3회까지 재시도한다. |
| 6 | 잘못 등록했으면 새로 등록해서 덮지 말고 KB 담당자에게 retire를 요청한다. |

**어떻게 신청하나:** §11의 신청서를 채워 접수한다. 접수 경로는 **확인 필요**(§11).

```
도구: 분석 시작
   │
   ├─ POST /v1/failure-buckets/match   (증상·관찰 신호)        ← 실패해도 계속 진행
   │      └─ 후보가 있으면 판별/반증 신호를 분석 체크리스트로 사용
   │
   ├─ … 분석 … (새 신호가 나올 때마다 match를 다시 호출해도 좋다)
   │
   └─ 분석 종료
        ├─ 새 패턴            → POST /v1/failure-buckets
        │                         └─ 응답에 possible_duplicate_of 있음 → POST …/{id}/refine (재등록 금지)
        ├─ 기존 패턴이 맞았음   → POST /v1/failure-buckets/{id}/refine  {"confirm": true}
        └─ 기존 패턴이 아니었음 → POST /v1/failure-buckets/{id}/refine  {"confirm": false, "add_counter_signal": "…"}
```

---

## 1. 엔드포인트

base URL은 `$KB`로 쓴다. 배포별 값은 [EXTERNAL_API.md](./EXTERNAL_API.md)의 "기본 베이스"와 KB 담당자 안내를 따른다(개발 기본값 `http://localhost:8573`).

| 용도 | 메서드·경로 | 필요한 롤(인증 켜진 뒤) | 도구가 호출 |
|---|---|---|---|
| 등록 | `POST /v1/failure-buckets` | `author` | ✅ |
| 목록 | `GET /v1/failure-buckets` | 없음(열려 있음) | ✅ |
| 상세 | `GET /v1/failure-buckets/{id}` | 없음 | ✅ |
| 확인/반박 | `POST /v1/failure-buckets/{id}/refine` | `author` | ✅ |
| 매칭 | `POST /v1/failure-buckets/match` | 없음 | ✅ |
| retire / restore / retire-batch | `…/{id}/retire`, `…/{id}/restore`, `…/retire-batch` | `senior` / `admin` | ❌ **사람이 KB 담당자에게 요청**한다. 도구는 호출하지 않는다. |

현재 인증은 꺼져 있다(§9).

---

## 2. 등록 요청

`POST /v1/failure-buckets`, `Content-Type: application/json`. 서버 모델(`FailureBucketCreate`) 기준이다.

| 필드 | 필수 | 제한 | 설명 |
|---|---|---|---|
| `bucket_name` | ✅ | 1–256자 | "증상+원인" 형태의 짧은 이름 |
| `fb_domain` | ✅ | 1–32자 | 진단 영역. 기존 값 목록은 [failure-bucket-domains.md](../references/failure-bucket-domains.md), 고르는 기준은 가이드 §2. 목록에 없는 값도 등록은 되지만 `fb_domain_warning`이 붙는다. |
| `evidence_ref` | ✅ | 1자 이상 | 근거 포인터(§6) |
| `discriminating_signals` | 권장 | 문자열 배열 | 이 패턴을 판별하는 관찰 가능한 신호. 작성 기준은 가이드 §5 |
| `symptom` | 권장 | 최대 4000자 | 사용자/운영 관점 현상 |
| `root_cause` | 권장 | 최대 4000자 | 근본 원인 |
| `recommended_action` | 권장 | 최대 4000자 | 권장 조치 |
| `counter_signals` | 선택 | 문자열 배열 | 이 패턴이 아님을 판별하는 신호 |
| `protocol` | 선택 | 최대 32자 | 예: TCP, TLS, HTTP |
| `environment` | 선택 | 최대 16자 | `csp`\|`msp`\|`onprem`\|`hybrid`. 원자료로 확인됐을 때만 채운다 |
| `created_by` | 선택 | 최대 128자 | §5. 비우면 인증된 주체 이름(인증 꺼짐이면 `anonymous`) |

원자료에서 실제로 관찰하지 않은 신호를 지어내서 넣지 않는다. 근거가 약하면 등록을 보류한다.

**예시** (`body-test.json`)

```json
{
  "bucket_name": "LB idle-timeout RST",
  "fb_domain": "network",
  "symptom": "업로드 중 연결 끊김",
  "discriminating_signals": ["RST 직전 idle 60초 이상"],
  "counter_signals": [],
  "root_cause": "LB idle timeout 60초",
  "recommended_action": "idle timeout 상향",
  "evidence_ref": "confluence:2412784426",
  "created_by": "sos-analyzer@v0.1-test"
}
```

```bash
curl -s -X POST $KB/v1/failure-buckets -H 'Content-Type: application/json' -d @body-test.json
```

**응답(발췌)**

```json
{
  "id": "82442015-de7e-422f-9c25-7729ccc7d802",
  "status": "test",
  "created_by": "sos-analyzer@v0.1-test",
  "registrant_kind": "human",
  "already_registered": false,
  "created_by_warning": "created_by='sos-analyzer@v0.1-test'의 시스템 이름 'sos-analyzer'이 허용 목록(config/system_registrants.json)에 없습니다. …",
  "confidence": 0.5,
  "…": "…"
}
```

| 응답 필드 | 의미 |
|---|---|
| `id` | 이후 refine·get에 쓰는 bucket id |
| `status` | `active`(운영 노출) · `test`(`-test` 시험 등록) · `retired`(되돌려져 숨김) |
| `already_registered` | `true`면 같은 요청이 이미 등록돼 있어 새 행을 만들지 않았다(§4) |
| `possible_duplicate_of` | 비슷한 활성 bucket이 있다는 뜻 → refine(§4) |
| `registrant_kind` | `system`(허용 목록에 있는 `created_by`) · `human` |
| `created_by_warning` | `created_by` 형식·허용 목록 문제. 등록은 거부되지 않는다 |
| `fb_domain_warning` | 목록에 없는 `fb_domain` |
| `evidence_ref_warning` | 알려진 접두어가 아님 |
| `evidence_ref_note` | `insight:`/`agent-room:`만 쓰고 `confluence:`를 병기하지 않았다는 안내(경고 아님) |

경고와 안내는 거부가 아니다. 도구는 경고를 로그에 남기고 담당자가 고치게 한다.

---

## 3. match / refine

**match** — `POST /v1/failure-buckets/match`

```bash
curl -s -X POST $KB/v1/failure-buckets/match -H 'Content-Type: application/json' \
  -d '{"observed_signals":["RST 직전 idle 62초"],"symptom":"다운로드 중 연결 끊김","fb_domain":"network","top_k":5}'
```

`results[]`에 `bucket_id`, `bucket_name`, `confidence`, `matched_signals`, `contradicted`가 온다. `contradicted`가 있는 후보는 사실상 배제 대상이다. `fb_domain`에 모르는 값을 주면 400과 허용 값 목록이 온다. **기본으로 `active` bucket만 조회된다**(`retired`·`test` 제외).

**refine** — `POST /v1/failure-buckets/{id}/refine`

| 상황 | 본문 |
|---|---|
| 기존 패턴이 맞았음 | `{"confirm": true, "add_signal": "<새로 확인한 신호, 선택>"}` |
| 기존 패턴이 아니었음 | `{"confirm": false, "add_counter_signal": "<이 패턴을 배제시킨 신호>"}` |

`retired` bucket을 refine하면 409(`"retired: restore 후 사용"`)이 온다.

---

## 4. 중복·멱등

| 상황 | 서버 동작 | 도구가 할 일 |
|---|---|---|
| 응답에 `possible_duplicate_of`가 있음 | 등록은 이미 됐다. 서버가 막지 않는다 | 그 bucket을 `GET`으로 열어 같은 패턴인지 확인하고, 같으면 이후부터 등록 대신 refine. 같은 패턴을 이름만 바꿔 다시 등록하지 않는다 |
| 같은 `created_by` + 같은 `evidence_ref` + 같은 `bucket_name`의 활성 bucket이 이미 있음 | **새 행을 만들지 않고** 기존 행을 `already_registered: true`와 함께 **200**으로 돌려준다 | 정상 응답으로 처리한다. 타임아웃 뒤 재시도해도 중복이 생기지 않는다 |

멱등 판정은 `test` 등록끼리도 적용된다. 한계: DB 유일 제약이 아니라 조회 후 등록이라, **동시에** 같은 요청을 두 번 보내면 둘 다 생길 수 있다. 직렬로 재시도한다.

예시(위 `body-test.json`을 다시 전송):

```json
{"id": "82442015-de7e-422f-9c25-7729ccc7d802", "status": "test", "already_registered": true}
```

---

## 5. `created_by`

- 형식 `<도구>@<판>`. 예: `sos-analyzer@v0.1`. 도구 이름은 소문자·숫자·`.`·`_`·`-`.
- **시험 등록:** 끝에 `-test`를 붙인다(`sos-analyzer@v0.1-test`). §8.
- `registrant_kind=system`이 되려면 도구 이름이 KB의 허용 목록(`config/system_registrants.json`)에 있어야 한다. 신청이 접수돼 한 줄 추가되기 전에는 `human`으로 처리되고 `created_by_warning`이 붙는다 — 시험 단계에서는 정상이다.
- 현재 허용 목록은 `insight-ops`, `agent-room` 둘이다. ToolBox 도구는 신청이 들어오면 한 줄씩 추가한다(미리 넣어두지 않는다).
- 인증이 켜진 뒤에는 `created_by`를 **발급받은 토큰 이름과 같은 값**으로 쓴다. 다르면 거부되지 않고 `created_by_warning`이 붙는다.
- KB 쪽에서는 `created_by`별 최근 24시간 등록 건수를 운영 대시보드로 본다(과다 등록 감시용). 서버는 일일 상한을 강제하지 않는다.

---

## 6. `evidence_ref`

작성 기준(구체적 포인터, 자유 서술 금지)은 가이드 §4를 따른다. 접두어:

| 접두어 | 형식 | 사람이 나중에 열 수 있나 | 권장 |
|---|---|---|---|
| `confluence:` | `confluence:<page id>` | ✅ 영구 | **기본 선택** |
| `capture:` | `capture:<파일>#frame=<n>` | 파일이 남아 있는 동안 | 원본 보관 위치가 영구일 때 |
| `log:` / `evtx:` | `log:node1/corosync.log#L4021-L4055` | 파일이 남아 있는 동안 | 〃 |
| `swim:` / `citects-` | `swim:INC00123456`, `CITECTS-2481` | ✅ 티켓 | 티켓이 있을 때 |
| `insight:` | `insight:ISS-…#report=…` | ⚠ insight-ops **로그인 필요** | 보고서를 Confluence에 올리고 `confluence:`를 쓴다 |
| `agent-room:` | `agent-room:<방>/<사건>…` | ⚠ **방을 지우면 사라진다** | 같은 사유로 `confluence:` 우선 |

`insight:`/`agent-room:`만 쓰고 `confluence:`를 병기하지 않으면 응답에 `evidence_ref_note`가 붙는다(거부 아님). 같은 문자열 안에 둘 다 쓰면 안내가 붙지 않는다: `"insight:ISS-1#report=r1 confluence:2412784426"`.

> **파일이 30일 후 삭제되는 도구가 많다**(SOS·SAR·Process·Ping·VMSTAT가 그렇게 안내한다 — 부록 A). 그 파일을 가리키는 `evidence_ref`는 한 달 뒤 아무도 열 수 없다. 근거로 쓸 분석 결과를 **삭제되지 않는 위치(Confluence 등)** 에 남기고 그 포인터를 쓴다.

---

## 7. 사람 승인

자동 등록 전에 사람 승인을 둘지는 **도구 담당자가 정하고 신청서에 적는다.** 서버는 승인 여부를 검증하지 않는다. 승인 없는 완전 자동 등록을 택한다면 §0의 규칙 2·4와 §10 retire 절차가 더 중요하다.

---

## 8. 시험 방법

1. `created_by`를 `<도구>@<판>-test`로 하고 등록한다. 행은 `status="test"`로 저장되어 **운영 목록·매칭·`kb_search`·그래프·집계에 나오지 않는다.**
2. 응답에서 `status: "test"`, `id`를 확인한다.
3. 운영 검색에 안 보이는지 확인한다.
   ```bash
   curl -s "$KB/v1/failure-buckets?limit=200"                          # 기본 목록에 없음
   curl -s "$KB/v1/failure-buckets?limit=200&include_retired=true"     # 숨겨진 것까지 포함하면 보임
   curl -s $KB/v1/failure-buckets/<id>                                 # 상세는 status와 함께 조회됨
   ```
4. 같은 요청을 한 번 더 보내 `already_registered: true`와 같은 `id`를 확인한다(멱등).
5. refine을 호출해 보고(`test` bucket에는 동작한다), match에 걸리지 않는 것도 확인한다.
6. **정리:** 시험 등록은 KB admin이 `retire-batch`로 일괄 정리한다. 먼저 dry_run으로 대상만 본다.
   ```bash
   curl -s -X POST $KB/v1/failure-buckets/retire-batch -H 'Content-Type: application/json' \
     -d '{"created_by_prefix":"sos-analyzer@","reason":"시험 등록 정리"}'                    # dry_run 기본값 true — 건수와 id만 반환
   ```
   `created_by_prefix`는 3자 이상, `reason`은 8자 이상. 실행은 `"dry_run": false`를 명시하며 admin 롤이 필요하다. 정리 요청은 KB 담당자에게 한다(§10).
7. 운영 전환: `-test`를 뗀 `created_by`로 새로 등록한다. (시험 행을 `restore`하면 `active`로 **승격**되지만, 시험 데이터가 운영에 들어가므로 권하지 않는다.)

---

## 9. 오류·재시도·인증 대비

**오류 응답.** `detail`의 형태:

| 코드 | `detail` | 의미 | 재시도 |
|---|---|---|---|
| 400 | 문자열 | 공백뿐인 `fb_domain`/`evidence_ref`, 또는 match의 모르는 `fb_domain`/`environment` 값(허용 값 목록이 문자열에 들어 있다) | ❌ |
| 401 / 403 | 문자열 | 인증/롤 문제(인증이 켜진 뒤) | ❌ |
| 404 | 문자열 `failure_bucket not found` | id 없음 | ❌ |
| 409 | 문자열 `retired: restore 후 사용` | retired bucket에 refine | ❌ |
| 422 | **객체 배열**(FastAPI 표준: `loc`, `msg`) | 필수 필드 누락, 길이·형식 위반(retire `reason` 8자 미만 포함) | ❌ |
| 5xx / 타임아웃 / 연결 실패 | — | KB 쪽 문제 | ✅ 최대 3회, 지수 백오프(예: 1s, 2s, 4s) |

**권장값**(서버가 강제하지 않는다): 연결 3초, match 읽기 10초, 등록·refine 읽기 30초(등록은 색인까지 동기로 수행한다). KB가 응답하지 않아도 **도구의 분석 결과 제공을 막지 않는다** — 호출은 백그라운드나 별도 스레드에서 하고, 실패는 로그만 남긴다. 재시도는 §4의 멱등 덕분에 등록에도 안전하다(직렬로).

**인증 대비.** 현재 `AUTH_MODE=off`이고, 켜는 시점은 KB 담당자가 공지한다. 켜지면:

- 쓰기(등록·refine)에 `X-API-Key` 헤더가 필요하다. **읽기와 match는 열려 있다.**
- 키는 **환경 변수로 받고, 값이 있을 때만 헤더를 붙이도록** 구현한다. 그러면 꺼진 지금도, 켜진 뒤에도 코드 변경이 없다.
  ```python
  headers = {"Content-Type": "application/json"}
  key = os.environ.get("CITEC_KB_API_KEY", "").strip()
  if key:
      headers["X-API-Key"] = key
  ```
- 시스템 토큰은 `author` 롤 하나다. 토큰 이름은 `created_by`와 같은 값으로 발급된다.
- 토큰 요청은 신청서(§11)의 "토큰 이름" 항목에 적는다. 발급·회수 절차는 [AUTH_ENABLEMENT.md](./AUTH_ENABLEMENT.md).

---

## 10. 되돌리기

잘못 등록했으면 **새로 등록해서 덮지 않는다.** KB 담당자에게 **bucket id와 사유(8자 이상)** 를 보내 retire를 요청한다. retire는 삭제가 아니라 **숨김**이다: 행은 남고, 목록·매칭·검색·그래프 이웃·집계에서 빠지며, `GET /v1/failure-buckets/{id}`로는 `status: "retired"`와 사유가 보인다. 잘못 숨겼으면 restore로 복구한다. 하드 삭제는 없다.

도구가 `retire`/`restore`를 호출하지 않는 이유: 시스템 등록자가 자기 등록건을 스스로 지우지 못하게 하기 위함이다.

담당자 연락 경로: **확인 필요.**

---

## 11. 신청서 양식

도구별 값은 담당자가 정해 신청한다. KB가 미리 정하지 않는다. 신청이 접수되면 KB 담당자가 허용 목록(`config/system_registrants.json`)에 한 줄을 추가하고, 새 `fb_domain`이면 [failure-bucket-domains.md](../references/failure-bucket-domains.md)(와 `app/taxonomy.py` 매핑)에 추가한다.

| 항목 | 작성 |
|---|---|
| 도구 이름 | |
| 담당자 | |
| 제안 `created_by` 이름 (`<도구>`; 판은 배포마다 붙임) | |
| 사용할 `fb_domain` (기존 값 또는 신규 제안 + 사유) | |
| 등록 방식 (완전 자동 / 사람 승인 후) | |
| 하루 예상 등록 건수 | |
| 사용할 `evidence_ref` 접두어와 근거 열람 방법 (사람이 나중에 어떻게 여는가) | |
| 인증이 켜질 때 쓸 토큰 이름 | |

- 신청 접수 경로: **확인 필요**
- KB 담당자 연락 경로: **확인 필요**

---

## 12. 연동 완료 체크리스트

- [ ] 분석 시작 시 `match`를 호출하고, 실패해도 분석을 계속한다
- [ ] 분석 종료 시 등록 또는 refine 중 하나만 한다
- [ ] 응답 `possible_duplicate_of`가 오면 재등록하지 않고 refine한다
- [ ] 같은 요청 재시도 시 `already_registered: true`를 정상 응답으로 처리한다
- [ ] `created_by`가 `<도구>@<판>` 형식이고, 시험은 `-test` 접미다
- [ ] `evidence_ref`가 영구 위치를 가리킨다(`confluence:` 우선). 30일 후 삭제되는 파일이나 로그인 필요 화면만 쓰지 않았다
- [ ] 4xx는 재시도하지 않고, 5xx·타임아웃만 최대 3회 재시도한다
- [ ] KB 호출 실패·지연이 도구 본 기능을 막지 않는다
- [ ] `CITEC_KB_API_KEY` 환경 변수가 있을 때만 `X-API-Key`를 붙인다
- [ ] `-test`로 시험하고, 운영 목록·검색에 나타나지 않는 것을 확인했다
- [ ] 시험 등록 정리를 KB 담당자에게 요청했다
- [ ] 신청서를 접수했다

---

## 부록 A. ToolBox 현황 (참고)

> **출처와 범위:** 2026-11-04에 파트장 PC에서 `http://70.222.78.8:8090/main.html`과 각 도구의 **첫 화면**을 직접 열어 확인한 결과(20개 도구)다. 도구에 파일을 올려 실제 출력을 본 것은 **아니다.** 첫 화면에 보이는 설명까지가 확인 범위다. 이 부록은 사실 확인용이며 **도구별 등록 대상 여부와 값은 정하지 않는다.** 개발시스템은 ToolBox에 접속할 수 없어 이 문서 작성 중 재확인하지 않았다.

### A-1. 도구 목록

| id | 이름 | 분야 | 용도(페이지 설명) |
|---|---|---|---|
| knowledge | CI-TEC Knowledge Portal | 지식 | 기술 지식·노하우 KB 검색 (KB 자체) |
| insight | Insight-Ops-AI | 지식 | AI 기반 운영 데이터 통합 분석 |
| lookin | Lookin | 시스템 | IT 인프라 구성 진단 |
| pisa | Internal PISA | 시스템 | PISA 대시보드 |
| sos | SOS Analyzer | 시스템 | Linux sosreport 구성·장애 분석 |
| deeper | Deeper | 시스템 | Linux 장애 원인(RCA) 추적 |
| vmware | VMware Analyzer | 시스템 | VMware 구성·성능 분석 |
| sar | SAR Analyzer | 성능 | CPU·메모리·디스크·네트워크 시계열 |
| probe | ProbeONE Analyzer | 성능 | 프로브 데이터·멀티 노드 비교 |
| process | Process Analyzer | 성능 | pidstat 프로세스별 자원 |
| ping | Ping Analyzer | 성능 | 네트워크 지연·패킷 손실 |
| vmstat | VMSTAT Analyzer | 성능 | vmstat 항목별 추이 |
| db | DB Analyzer | 성능 | DB 분석 안내 문서(Confluence 링크) |
| hana | HANA Analyzer | 성능 | SAP HANA 덤프·Kernel Profiler |
| perfone | PerfONE CS | 성능 | 분석 도구 배포 저장소(코드 저장소 링크) |
| log | Log Analyzer | 로그 | 시스템 로그 장애 단서 분석 |
| elao | ProbeONE ELAO | 로그 | ProbeONE 로그 타임라인 분석 |
| opensearch | OpenSearch | 로그 | SCP PS/PP/PK/PG 로그 검색·AI 원인분석 |
| osdeeper | OpenSearch Deeper | 로그 | OpenSearch 로그 상세 원인 분석 |
| kernel | Kernel Errata Tracker | 커널 | RHEL·Oracle Linux·Ubuntu 커널 권고 추적 |

### A-2. 첫 화면에서 확인한 출력 형태

(포트는 모두 70.222.78.8)

| 도구 | 확인한 사실 (첫 화면 기준) | 확인하지 못한 것 |
|---|---|---|
| SOS (8093) | sosreport·SUSE supportconfig를 올리면 시스템 요약과 AI 분석을 만든다. Claude 분석(Fabrix 또는 사용자 PC의 Claude)을 선택할 수 있다. | 분석 결과의 필드 구조 |
| Log Analyzer (8093) | Pacemaker·커널·Ceph·k8s 이벤트를 분석한다. Pacemaker 로그는 Summary와 Timeline 두 리포트를 낸다. 업로드 파일은 분석 후 삭제하고 결과 HTML만 보관한다. | 판정 항목의 구조 |
| OpenSearch (8093) | SCP 6개 사이트의 OVN/OVS·syslog·Ceph 로그를 검색한다. "상세원인분석"과 "장애 타임라인" 메뉴가 있다. | 상세원인분석의 출력 |
| HANA (8096) | crashdump·rtedump 트레이스를 올리면 증상 분류표(Crash Issue, Resource Exhaustion, Lock Situation 등)에 따라 자동 분류한다. 분류 불가는 "Undetected Issue"로 두고 Claude 분석으로 넘긴다. | 분류 결과의 출력 형식 |
| SAR (8091) | sar 파일로 사이트별 대시보드와 AI 분석을 만든다. | AI 분석의 구조 |
| Process (8093) | CPU Top 10, Memory RSS Top 10, 시간대별 추이를 낸다. | |
| Ping (8093) | RTT Top N, 이상 임계치, 시간대별 추이를 낸다. | |
| VMSTAT (8093) | 항목별 추이를 낸다. | 판정 유무 |
| ProbeONE Analyzer (8095) | 로그 분석, 성능 리포트, 차트 메뉴가 있고 vRLI와 esxtop 입력을 받는다. | 판정 출력 여부 |
| ELAO (18080) | 자바스크립트 앱이라 첫 화면 텍스트를 읽지 못했다. | 전체 |
| VMware Analyzer (8600), Insight-Ops-AI (8082) | 첫 화면에 제목 외 텍스트가 없었다(스크립트 렌더링으로 추정). | 전체 |

**공통 사실:** SOS, Log, SAR, HANA는 "Claude 분석도 함께 실행"을 선택할 수 있다. 파일은 30일 후 삭제된다고 안내한다(SOS, SAR, Process, Ping, VMSTAT). 근거로 쓸 첫 분석 결과가 도구 쪽에서 사라질 수 있다는 뜻이므로 §6의 `evidence_ref` 설계에 반영했다.

### A-3. 참고 분류 (첫 화면 기준 — 등록 대상 확정이 아님)

- **원인·분류·판정 출력이 확인된 도구:** SOS, Log Analyzer, HANA. 등록 신청 여부는 각 담당자가 정한다.
- **추이·통계 출력만 확인된 도구:** SAR, Process, Ping, VMSTAT. 이 도구들은 `match` 조회부터 시작하는 것이 자연스럽다.
- **확인하지 못한 도구:** OpenSearch, ProbeONE Analyzer, ELAO, VMware Analyzer, Insight-Ops-AI. (Insight-Ops-AI는 별도로 시스템 등록자 `insight-ops`로 수용된다.)
- **등록 대상에서 제외:** `knowledge`(KB 자체), `db`(안내 문서), `perfone`(배포 저장소), `kernel`, `pisa`.
- **추후 협의:** Deeper, OpenSearch Deeper. *(협의용 메모: 두 도구의 자체 "KDB"와 "이슈 사례집"이 failure bucket과 겹칠 수 있다.)*

---

## 부록 B. 이 문서의 예시 검증

| 예시 | 검증 |
|---|---|
| §2 등록(`-test`), 응답 필드 | 2026-11-04 로컬 서버(스크래치 DB, `AUTH_MODE=off`)에 curl로 실행, 응답 일치 |
| §4 같은 요청 재전송 → `already_registered: true`, 같은 `id` | 〃 |
| §8 기본 목록에서 숨김 / `include_retired=true` / 상세 `status: "test"` | 〃 |
| §8 `retire-batch` dry_run → 실행 | 〃 (dry_run 응답 `matched`·`ids`·`retired: 0`, 실행 후 `retired: 1`) |
| §9 오류: 409, 404 | 〃 |
| §9 오류: 400(모르는 `fb_domain`, 공백 `fb_domain`), 422(필수 필드 누락) | 〃 |
| §3 match 요청/응답 필드(`bucket_id`, `bucket_name`, `matched_signals`, `contradicted`, `confidence`, `label`) | 〃 |
| §9 401/403, `X-API-Key` 동작 | curl 미실행 — 자동 테스트(`AUTH_MODE=apikey`, FastAPI TestClient)로 동작 확인. **curl 예시는 미검증** |
| §9 Python 헤더 스니펫 | 코드 조각, 미실행 — **미검증** |
