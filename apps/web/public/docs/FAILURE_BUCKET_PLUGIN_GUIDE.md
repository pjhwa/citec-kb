# 진단 플러그인 개발 지침 — citec-kb `failure_bucket` 자가개선 루프 연동

**대상:** `packet-capture-rca`처럼 특정 진단 영역(패킷/Pacemaker·Corosync 클러스터/Windows
이벤트로그·AD 복제 등)을 분석하는 새 Claude Code 스킬·플러그인을 만드는 개발자.
**목적:** 매 플러그인이 각자 방식으로 citec-kb를 쓰지 않고, 같은 규약으로 과거 확인된 실패
패턴(`failure_bucket`)을 조회·재사용하고 새로 확인한 패턴을 되먹임하게 한다.

이 문서는 `packet-capture-rca` 스킬의 `references/citec-kb-integration.md`(실제 동작 검증된
워크플로)를 일반화한 것이다. 특정 도메인 예시가 필요하면 그 파일을 함께 참고할 것. 이 지침이 요구하는
`fb_domain`/`evidence_ref`/`source_plugin` 필드는 citec-kb 쪽 설계 변경
([2026-08-06-failure-bucket-multi-plugin-design.md](./superpowers/specs/2026-08-06-failure-bucket-multi-plugin-design.md))에
의존한다 — 대상 citec-kb 배포가 그 변경을 아직 반영하지 않았다면(구버전 MCP 시그니처), 먼저
`kb_tools_help()`로 실제 파라미터를 확인하고 없는 파라미터는 생략한다.

---

## 0. 이 지침이 다루지 않는 것

- **CITECTS/Jira 지원 이력 조회는 이 지침의 대상이 아니다.** "과거 유사 사례가 있었는가"는
  `kb_similar_incident`(citec-kb에 동기화된 지원이력 사본)로 확인하고, 정식 지원 티켓 자체의
  조회/생성/갱신은 `jira-mcp`(`citec-mcp-workbench` 스킬 영역)로 한다. 이 문서는 오직
  `failure_bucket`(구조화된 실패 패턴 라이브러리) 하나만 다룬다.
- 승인/리뷰 워크플로는 없다. 등록은 즉시 검색에 노출되고, 운영 대시보드로 사후 가시성만 제공된다.
  따라서 §4의 "근거 없는 등록 금지" 원칙이 실질적인 유일한 품질 게이트다 — 가볍게 여기지 않는다.

---

## 1. 원칙 (모든 플러그인 공통)

1. **분석을 시작하기 전에 먼저 citec-kb부터 조회한다.** 알려진 판별 신호를 기준으로 원자료(캡처,
   로그, 이벤트)를 훨씬 빠르게 좁혀 들어갈 수 있다.
2. **한 도구만 믿지 않는다.** `kb_match_failure_bucket`(구조화 매칭), `kb_search`(하이브리드
   전문/의미 검색, `section="failure_bucket"`), `kb_list_failure_buckets`(도메인별 목록),
   `kb_similar_incident`(과거 장애 티켓)를 상호 보완적으로 쓴다.
3. **새 신호를 확인할 때마다 재조회한다.** 분석 중 신호가 늘어나면 `kb_match_failure_bucket`을
   갱신된 `observed_signals`로 다시 호출한다. 한 번으로 끝내지 않는다.
4. **분석이 끝나면 반드시 되먹임한다.** 등록(`kb_register_failure_bucket`) 또는 확인/반박
   (`kb_refine_failure_bucket`, `confirm=true/false`) 중 정확히 하나를 수행한다. 이게 없으면
   자가개선 루프가 끊기고 다음 세션이 처음부터 다시 분석해야 한다.
5. **근거 없는 등록/확정을 금지한다.** 실제로 관찰되지 않은 신호를 지어내지 않는다. 확신이 서지
   않으면 등록을 보류하거나 조건부/추정 수준으로만 보고한다. 등록할 때는 반드시 §4의
   `evidence_ref`를 구체적으로 남긴다 — "확정된 내용"이라는 부서 방침을 지키는 유일한 기계적 장치다.

---

## 2. `fb_domain` — 내 플러그인의 도메인 값 정하기

등록/조회 시 항상 `fb_domain`을 채운다. 즉흥적으로 새 문자열을 짓지 않고, citec-kb의
`references/failure-bucket-domains.md`(부서 공용 어휘 목록)를 먼저 확인한다.

| 플러그인 | fb_domain |
|---|---|
| packet-capture-rca | `network` |
| pacemaker-tools | `cluster` |
| windows-tools | `windows` |
| (새 플러그인) | 목록에 없으면 신설 — 아래 절차 |

**새 `fb_domain`이 필요한 경우:**
1. 정말 기존 값으로 충분하지 않은지 확인한다(예: "AD 복제 실패"를 `windows`와 별도로 나눌 필요가
   실제로 있는가, 아니면 `windows` 안에서 신호로 구분해도 충분한가).
2. `references/failure-bucket-domains.md`에 행을 추가하는 PR을 citec-kb 저장소에 낸다(코퍼스
   전역 `domain`/`area` 값에 매핑하는 `taxonomy.py`의 매핑 테이블도 함께—citec-kb 개발팀 몫이지만
   플러그인 개발자가 PR로 제안할 수 있다).
3. 등록 없이 새 값을 그냥 써버리면 citec-kb가 경고를 반환하도록 설계돼 있다(구버전이면 무시될 수
   있음) — 경고를 보면 오타인지 신설인지 먼저 확인한다.

---

## 3. 이 스킬의 phase에 매핑 — 일반 템플릿

아래는 3단계(분석 전/중/후) 구조를 자기 플러그인의 phase 이름으로 바꿔 쓰면 되는 템플릿이다.
(`packet-capture-rca`의 phase 1/3/4/5/7 매핑은 `citec-kb-integration.md` 참고.)

### 3-1. 분석 착수 시점 — 1차 조회

```
kb_match_failure_bucket(
  observed_signals=[],                 # 아직 없으면 빈 배열
  symptom="<사용자가 보고한 증상 원문>",
  fb_domain="<내 플러그인의 fb_domain>"
)
```

결과가 있으면 `kb_get_failure_bucket(bucket_id=)`으로 판별/반증 신호 전문을 받아 분석 체크리스트로
삼는다. 결과가 부실하면:

```
kb_list_failure_buckets(fb_domain=...)
  → kb_search(query="<증상 키워드>", section="failure_bucket")
  → kb_similar_incident(symptom=..., product=...)
```

### 3-2. 분석 진행 중 — 반복 재조회

원자료를 더 파고들며 새 신호(로그 이벤트, 타이밍, 플래그, 에러 코드 등)를 확인할 때마다:

```
kb_match_failure_bucket(
  observed_signals=[...누적된 신호...],
  symptom="...",
  fb_domain="..."
)
```

응답의 `matched_signals`/`contradicted`를 함께 본다. `contradicted`가 뜬 버킷은 사실상 배제
대상이다. 이 재조회는 1회로 끝내지 않는다.

### 3-3. 분석 종료 시점 — 되먹임 (정확히 하나)

- **완전히 새로운 패턴** →

  ```
  kb_register_failure_bucket(
    bucket_name="<증상+원인 형태의 짧은 이름>",
    symptom="<사용자/운영 관점 현상>",
    discriminating_signals=["<관찰된 구체적 신호>", ...],
    counter_signals=["<반증 신호>", ...],
    root_cause="<근본 원인>",
    recommended_action="<권장 조치>",
    fb_domain="<내 플러그인의 fb_domain>",
    evidence_ref="<원자료를 가리키는 구체적 포인터 — §4>",
    source_plugin="<plugin-name>@<version>"
  )
  ```

  응답에 `possible_duplicate_of`가 포함돼 있으면, 등록은 이미 됐더라도 그 버킷을
  `kb_get_failure_bucket`으로 열어 실제로 같은 패턴인지 확인한다. 같은 패턴이면 이번 등록 대신
  `kb_refine_failure_bucket(confirm=true)`을 썼어야 한다는 뜻이니, 다음부터는 등록 전
  `kb_match_failure_bucket`을 더 폭넓게(신호를 다양하게 바꿔가며) 먼저 돌린다.

- **기존 버킷이 맞았음** → `kb_refine_failure_bucket(bucket_id=, confirm=true, add_signal=<새로
  확인된 신호, 있다면>)`
- **기존 버킷 후보였지만 아니었음** → `kb_refine_failure_bucket(bucket_id=, confirm=false,
  add_counter_signal=<이 버킷을 배제시키는, 이번에 발견한 신호>)`

---

## 4. `evidence_ref` 작성 기준 — "확정된 내용"의 유일한 게이트

`evidence_ref`는 사람이 나중에 열어 독립적으로 확인할 수 있는 **구체적 포인터**여야 한다. 자유
서술("패킷에서 확인함" 등)은 거부 대상이다.

| 원자료 | 형식 예시 |
|---|---|
| 지원 티켓 | `CITECTS-2481` |
| Confluence 페이지 | `confluence:2412784426` |
| 패킷 캡처 | `capture:2026-08-06-upload01.pcapng#frame=4821` |
| Windows 이벤트 | `evtx:DC01-System.evtx#EventRecordID=88213` |
| Pacemaker/Corosync 로그 | `log:node1/corosync.log#L4021-L4055` |
| SWIM 장애번호 | `swim:INC00123456` |

이 필드가 없으면(또는 형식이 자유 서술이면) **등록을 보류**하고 사용자에게 "이 신호를 어떤 원자료
어느 지점에서 확인했는지" 되묻는다.

---

## 4-1. `environment` — 모든 플러그인 공통 선택 필드

`fb_domain`(진단 영역)과 별개로, `kb_register_failure_bucket`/`kb_match_failure_bucket`/
`kb_list_failure_buckets`/`kb_refine_failure_bucket`는 공통으로 `environment: csp|msp|onprem|hybrid`를
받는다. 이 패턴이 특정 환경에서만 성립한다고 **원자료로 확인됐을 때만** 채운다(원칙 5와 동일하게
근거 없는 값은 금지) — 예: `pacemaker-tools`가 "온프레미스 물리 노드에서만 나타나는 STONITH 오동작"을
등록한다면 `environment="onprem"`. `kb_match_failure_bucket(environment=...)`로 조회하면 다른 환경으로
명시 태깅된 버킷은 후보에서 제외되고, `environment`가 비어 있는(아직 미확인) 버킷은 계속 후보에
남는다 — 즉 이 필드를 채우지 않아도 기존 동작은 그대로다. 값 어휘는 `references/corpus-taxonomy.md`
가 코퍼스 전역에서 이미 쓰는 4개 값과 동일하다.

**등록 시점에 몰랐던 environment는 `kb_refine_failure_bucket(bucket_id=, environment=..., confirm=)`로
나중에 채우거나 정정할 수 있다** — `environment=""`(기본값)이면 기존 값을 그대로 두고, 값을 채우면
덮어쓴다. 이미 등록된 버킷의 `symptom`/`root_cause`를 다시 읽어보고 환경을 확인할 수 있는 경우(예:
appliance LB + k8s pod 같은 조합이 텍스트에 이미 드러나 있는 경우)에 특히 유용하다 — 이 경우도
"근거 없는 값 채우기 금지" 원칙이 그대로 적용된다: 텍스트에 실제로 있는 근거로만 채운다.

---

## 5. 좋은 discriminating_signals / counter_signals 작성 기준

- **관찰 가능한 구체적 조건**으로 쓴다. "타임아웃 문제" ❌ / "RST 직전 idle ≥ 60초" ✅ /
  "Event ID 1135 발생 후 5초 이내 1177 발생" ✅
- **측정 가능한 임계값·플래그·이벤트 ID 조합**을 명시한다.
- counter_signals는 "이 패턴이 아님"이 아니라 **왜 아닌지 판별 가능한 신호**로 쓴다.
- `bucket_name`은 "증상+원인" 형태로 짧게 쓴다.
- **신호 개수를 인위적으로 부풀리지 않는다.** citec-kb의 매칭 스코어는 매칭 개수에 상한을 두고
  계산하므로(신호가 많다고 불리해지지는 않는다) 정확한 신호를 필요한 만큼만 쓴다 — 상세 배경은
  citec-kb 설계 문서 §3 참고.

---

## 6. 하지 말아야 할 것 (Anti-patterns)

- citec-kb 조회 없이 바로 분석을 시작하고 끝내는 것.
- 분석 중 신호가 추가됐는데도 처음 조회 결과만으로 결론 내리는 것.
- 분석 결과를 등록/확인/반박 중 아무것도 하지 않고 세션을 끝내는 것.
- 원자료에서 실제로 확인되지 않은 신호를 `discriminating_signals`에 지어내서 넣는 것.
- `evidence_ref` 없이(또는 자유 서술로) 등록하는 것.
- `contradicted`가 뜬 버킷의 `recommended_action`을 검증 없이 그대로 적용하는 것.
- 이미 존재하는 버킷과 사실상 동일한 패턴을 이름만 바꿔 중복 등록하는 것 — 먼저
  `kb_match_failure_bucket`/`kb_list_failure_buckets(fb_domain=)`로 기존 버킷 유무를 반드시 확인.
- `fb_domain`을 비우거나 즉흥적으로 새 문자열을 짓는 것 — §2 절차를 따른다.
- "유사 사례가 있었는지" 확인이나 패턴 등록/정제를 `jira-mcp`로 하는 것 — §0 참고.

---

## 7. 새 플러그인용 스켈레톤 — `references/citec-kb-integration.md`

새 플러그인 스킬을 만들 때, 아래 골격을 그 스킬의 `references/citec-kb-integration.md`로 복사해
자신의 phase 이름과 도메인 예시로 채운다. (`packet-capture-rca`의 것이 실제로 채워진 참고 예시다.)

```markdown
# citec-kb 연동 — failure-bucket 패턴 라이브러리 + 자가개선 루프

wiki-mcp의 kb_* 도구가 세션에 있다면 이 워크플로는 선택이 아니라 필수다. fb_domain="<이 플러그인의
fb_domain>"을 항상 채운다.

## 원칙
(본 가이드 §1을 그대로 인용 또는 요약)

## 이 스킬의 phase에 매핑
- phase <N>(원자료 열기 전) — kb_match_failure_bucket(fb_domain="...", symptom=...)로 1차 조회
- phase <M>(분석 중) — 새 신호 확인마다 kb_match_failure_bucket 재호출
- phase <P>(보고 작성 후) — kb_register_failure_bucket 또는 kb_refine_failure_bucket 중 정확히 하나

## 좋은 신호 작성 기준 / evidence_ref 형식
(본 가이드 §4/§5에서 이 도메인에 맞는 예시로 구체화)

## 하지 말아야 할 것
(본 가이드 §6 그대로)
```

---

## 8. 체크리스트 (요약)

- [ ] 분석 착수 전 `kb_match_failure_bucket(fb_domain=...)` (+ 필요 시 `kb_list_failure_buckets`,
      `kb_search`, `kb_similar_incident`)
- [ ] 새 신호를 확인할 때마다 `kb_match_failure_bucket` 재호출
- [ ] 분석 종료 시 `kb_register_failure_bucket`(evidence_ref/fb_domain/source_plugin 포함) 또는
      `kb_refine_failure_bucket`(confirm=true/false) 중 하나
- [ ] `possible_duplicate_of` 응답이 오면 확인 후 필요 시 등록 대신 refine으로 정정
- [ ] `fb_domain`이 `references/failure-bucket-domains.md`에 있는 값인지 확인 (새 값이면 §2 절차)
