# 패킷 분석 시 citec-kb MCP 활용 지침 (Claude 전용)

**대상:** 네트워크 패킷 캡처(pcap)를 분석하는 Claude 세션.
**목적:** 과거에 확인된 실패 패턴(`failure_bucket`)을 citec-kb에서 놓치지 않고 검색·활용하고,
새로 발견한 패턴은 반드시 citec-kb에 등재해 다음 분석에 재사용되게 한다. **분석 자체는
패킷 데이터를 직접 보고 판단**하되, citec-kb는 "과거에 이미 정리된 판별 기준"을 참고/기록하는
보조 지식베이스로 취급한다.

관련 문서: [MCP.md](./MCP.md) · [AI_AGENT_GUIDE.md](./AI_AGENT_GUIDE.md) §4.15 · [EXTERNAL_API.md](./EXTERNAL_API.md)

---

## 0. 핵심 원칙

1. **분석 시작 전에 반드시 citec-kb부터 조회한다.** 패킷을 열어보기 전에 이미 알려진 패턴인지
   먼저 확인하면, 알려진 판별 신호를 기준으로 캡처를 훨씬 빠르게 좁혀 들어갈 수 있다.
2. **한 가지 도구만 믿지 않는다.** `kb_match_failure_bucket`(구조화 매칭), `kb_search`(하이브리드
   전문/의미 검색), `kb_list_failure_buckets`(프로토콜별 목록), `kb_similar_incident`(과거 장애
   티켓)까지 상호 보완적으로 사용한다. 아래 §2를 순서대로 밟는다.
3. **분석 중 신호가 추가될 때마다 재조회한다.** 처음 관찰한 신호만으로 끝내지 않고, 패킷을
   더 파고들며 새 신호(예: TLS record 경계, 재전송 패턴, MSS/윈도우 값)를 확인할 때마다
   `kb_match_failure_bucket`을 다시 호출해 후보를 갱신한다.
4. **분석이 끝나면 반드시 citec-kb에 되먹임한다.** 새 패턴이면 등록, 기존 패턴이 맞았거나
   틀렸으면 확인/반박을 기록한다. 등재하지 않으면 다음 세션이 처음부터 다시 분석해야 한다 —
   이 되먹임이 없으면 self-improving 구조가 작동하지 않는다.
5. **근거 없는 등록/확정 금지.** 실제 패킷에서 관찰되지 않은 신호를 지어내지 않는다. 확신이
   서지 않으면 등록을 보류하거나 `조건부`/`추정` 수준으로만 보고한다.

---

## 1. 사용 가능한 MCP 도구 (failure_bucket)

| 도구 | 용도 | 시점 |
|------|------|------|
| `kb_match_failure_bucket(observed_signals=, symptom=, protocol=)` | 관찰된 신호로 후보 버킷 순위화 | 분석 전 · 분석 중 (반복) |
| `kb_list_failure_buckets(protocol=, min_confidence=)` | 특정 프로토콜의 등록된 패턴 전체 목록 | 분석 전 (탐색) |
| `kb_get_failure_bucket(bucket_id=)` | 후보 버킷 상세(판별/반증 신호, 원인, 조치) 조회 | 분석 전 · 분석 중 |
| `kb_search(query=, section="failure_bucket", area=)` | 버킷 이름·증상·조치 텍스트에 대한 하이브리드 검색 | 정확한 신호 문구를 모를 때 |
| `kb_similar_incident(symptom=, product=, environment=)` | 과거 지원이력(장애 티켓) 중 유사 사례 | 패턴이 아니라 특정 사고 사례가 필요할 때 |
| `kb_register_failure_bucket(bucket_name=, symptom=, discriminating_signals=, root_cause=, recommended_action=, counter_signals=, protocol=)` | 신규 패턴 등록 (즉시 검색 노출) | 분석 후 — 새 패턴 확정 시 |
| `kb_refine_failure_bucket(bucket_id=, add_signal=, add_counter_signal=, confirm=)` | 신호 추가 + 확인/반박 기록 → 신뢰도 자동 재계산 | 분석 후 — 기존 패턴 재확인/반박 시 |

도구 시그니처와 REST 매핑 상세는 `docs/AI_AGENT_GUIDE.md` §4.15, `docs/MCP.md` 참고.

---

## 2. 워크플로 — 분석 전 (Pre-analysis)

패킷을 열기 전, 알려진 증상 키워드와 프로토콜만으로 먼저 조회한다.

```
1) kb_match_failure_bucket(
     observed_signals=[],              # 아직 없으면 빈 배열
     symptom="<사용자가 보고한 증상 그대로>",
     protocol="<알고 있다면 TCP|TLS|HTTP 등>"
   )
2) 결과가 있으면 → kb_get_failure_bucket(bucket_id=...)로 판별 신호 전문 확인
3) 결과가 부실하면 → kb_list_failure_buckets(protocol=...)로 해당 프로토콜의 전체 목록 훑기
4) 그래도 부족하면 → kb_search(query="<증상 키워드>", section="failure_bucket")로
   버킷 이름/조치 텍스트 전문검색 (신호 문구를 정확히 모를 때 유용)
5) 필요하면 → kb_similar_incident(symptom=..., product=...)로 과거 장애 티켓까지 확인
   (failure_bucket에는 없지만 support_history에 유사 사고가 있을 수 있음)
```

이 단계에서 후보가 나오면, 해당 버킷의 **판별 신호(discriminating_signals)를 캡처 분석의
체크리스트로 그대로 사용**한다 — "이 신호가 실제로 있는가?"를 확인하는 순서로 분석을
진행하면 무작정 패킷을 훑는 것보다 훨씬 빠르다.

---

## 3. 워크플로 — 분석 중 (During analysis)

패킷을 들여다보며 구체적인 신호(타이밍, 플래그, 페이로드 특징 등)를 확인할 때마다:

```
observed_signals에 새로 확인한 신호를 계속 누적하며 재호출:

kb_match_failure_bucket(
  observed_signals=["RST 직전 idle 62초", "FIN 없이 RST"],
  symptom="다운로드 중 연결 끓김",
  protocol="TCP"
)
```

- 응답의 `matched_signals`(부합한 신호)와 `contradicted`(반증 신호에 걸린 것)를 함께 본다.
  `contradicted`가 있으면 그 버킷은 사실상 배제 대상이다(라벨 `비권고`).
- `label`이 `가능`이면 해당 버킷의 `recommended_action`을 조치 후보로 제시하되, 반드시
  **현재 캡처에서 그 조치가 타당한지 재검증**한다 (환경·버전 차이가 있을 수 있음).
- 여러 버킷이 `조건부`로 걸리면, 서로를 구분 짓는 반증 신호가 무엇인지 `kb_get_failure_bucket`
  으로 확인하고, 그 신호가 실제 캡처에 있는지 확인하는 방향으로 분석을 좁힌다.
- 이 재조회는 **1회로 끝내지 않는다** — 새 단서가 나올 때마다 반복한다.

---

## 4. 워크플로 — 분석 후 (Post-analysis, 되먹임)

분석이 끝나면 아래 중 정확히 하나에 해당하는 조치를 취한다. **생략하지 않는다.**

### 4-1. 완전히 새로운 패턴을 발견한 경우 → 등록

```
kb_register_failure_bucket(
  bucket_name="<간결한 패턴 이름, 예: 'LB idle-timeout으로 인한 RST'>",
  symptom="<사용자/운영 관점에서 관찰되는 현상>",
  discriminating_signals=[
    "<이 패턴을 식별하는 구체적 신호 1>",
    "<신호 2>"
  ],
  counter_signals=[
    "<이 패턴이 아님을 시사하는 반증 신호 — 비슷한 패턴과 구분 짓는 근거>"
  ],
  root_cause="<근본 원인>",
  recommended_action="<권장 조치>",
  protocol="TCP|TLS|HTTP|..."
)
```

작성 기준은 §5(품질 가이드) 참고. `discriminating_signals`가 비어 있으면 API가 거부한다.

### 4-2. 기존 버킷이 맞았던 경우 → 확인 기록

```
kb_refine_failure_bucket(bucket_id="<매칭된 버킷 id>", confirm=true)
```

새로 관찰한 신호가 있고 그 버킷의 기존 신호 목록에 없다면 함께 추가:

```
kb_refine_failure_bucket(
  bucket_id="...",
  add_signal="<이번에 새로 확인된 판별 신호>",
  confirm=true
)
```

### 4-3. 기존 버킷 후보였지만 실제로는 아니었던 경우 → 반박 기록

```
kb_refine_failure_bucket(bucket_id="<오탐이었던 버킷 id>", confirm=false)
```

가능하면 왜 아니었는지(무엇이 그 버킷과 달랐는지)를 반증 신호로 추가한다:

```
kb_refine_failure_bucket(
  bucket_id="...",
  add_counter_signal="<이번 사례에서 발견한, 해당 버킷을 배제시키는 신호>",
  confirm=false
)
```

이 되먹임(확인/반박 누적)이 `confidence`를 자동으로 갱신시키는 self-improving 메커니즘의
핵심이다 — 누적 없이는 신뢰도가 항상 초기값(0.5)에 머문다.

---

## 5. 좋은 discriminating_signals / counter_signals 작성 기준

- **관찰 가능한 구체적 조건**으로 쓴다. "타임아웃 문제" ❌ / "RST 직전 idle ≥ 60초" ✅
- **측정 가능한 임계값·플래그 조합**을 명시한다 (초 단위, 플래그 유무, 재전송 횟수 등).
- **다른 유사 패턴과 구분되는 지점**을 counter_signals에 담는다 — 단순히 "이 패턴이 아님"이
  아니라, 왜 아닌지 판별 가능한 신호여야 다음 매칭에서 실제로 걸러진다.
- bucket_name은 검색·목록에서 한눈에 식별 가능하도록 "증상+원인" 형태로 짧게 쓴다
  (예: "TLS record 재조립 지연", "LB idle-timeout으로 인한 RST").
- protocol은 가능하면 항상 채운다 — `domain` 필터링과 `kb_list_failure_buckets(protocol=)`
  조회에 쓰인다.

---

## 6. 하지 말아야 할 것 (Anti-patterns)

- ❌ citec-kb 조회 없이 바로 분석을 시작하고 끝내는 것 (§0.1 위반)
- ❌ 분석 중 신호가 추가됐는데도 처음 조회 결과만으로 결론 내리는 것 (§3 위반)
- ❌ 분석 결과를 등록/확인/반박 중 아무것도 하지 않고 세션을 끝내는 것 (§4 위반)
- ❌ 캡처에서 실제로 확인되지 않은 신호를 `discriminating_signals`에 지어내서 넣는 것
- ❌ `contradicted`(반증 신호)가 뜬 버킷의 `recommended_action`을 검증 없이 그대로 적용하는 것
- ❌ 이미 존재하는 버킷과 사실상 동일한 패턴을 이름만 바꿔 중복 등록하는 것 — 먼저
  `kb_match_failure_bucket`/`kb_list_failure_buckets`로 기존 버킷 유무를 반드시 확인

---

## 7. 최소 체크리스트 (요약)

- [ ] 분석 착수 전 `kb_match_failure_bucket` (+ 필요 시 `kb_list_failure_buckets`,
      `kb_search`, `kb_similar_incident`) 로 기존 패턴 조회 완료
- [ ] 후보 버킷의 판별/반증 신호를 캡처 분석 체크리스트로 활용
- [ ] 새 신호를 확인할 때마다 `kb_match_failure_bucket` 재호출
- [ ] 분석 종료 시 `kb_register_failure_bucket` 또는 `kb_refine_failure_bucket`
      (confirm=true/false) 중 하나를 반드시 호출
- [ ] 등록/정제 내용이 실제 캡처 근거에 기반했는지 최종 확인
