# 예상 질문 100 · 코퍼스 테스트 · 설계 보완 분석

**일자:** 2026-07-18  
**대상 코퍼스:** `raw/` (support_history 2278 · tech_repo 2709 · checkitems 4434 · tuning_ai 등)  
**산출물:** `query_catalog_100.json` · `query_catalog_100.md` · `query_catalog_100_answerability.json`

---

## 1. 통찰: 세 페르소나가 묻는 것의 본질

| 페르소나 | 의사결정 목적 | 질문의 형태 | 실패 시 비용 |
|----------|---------------|-------------|--------------|
| **기술지원 전문가** | 지금 이 증상, 이 환경에서 무엇을 보고 무엇부터 손댈까 | 사례 검색 · 원리 · 체크리스트 · 예방 | 오진·장애 확대 |
| **임원/부서장** | 여력·우선순위·제안·리스크 | 공수/대수/M/M · 비중 · 정책 · 포트폴리오 | 잘못된 수주/인력 배분 |
| **장애관리 담당** | mid-incident 유사장애·SOP·재발방지 | FRB 패턴 · SOP · 등급 · 후속개선 | 복구 지연·재발 |

공통 통찰:
1. **전문가는 “한 건의 정밀 근거”**, 관리자는 **“표·숫자·정책”**, 장애담당은 **“유사 패턴 + 즉시 행동”**을 원한다.
2. 문서에 답이 *존재*하는 것과 시스템이 *완전 답변*하는 것은 다르다.
3. 100문 테스트 결과: **키워드 존재율은 매우 높음(0 none)**. 병목은 **질의 유형별 실행 경로**다.

---

## 2. 질문 100 구성

| 페르소나 | 수 | 대표 유형 |
|----------|----|-----------|
| Expert (E01–E40) | 40 | factoid, technical_synthesize, checklist, entity, capacity, prevention |
| Manager (M01–M30) | 30 | capacity_lookup, aggregate_stats, risk, factoid |
| Incident (I01–I30) | 30 | technical_synthesize, FRB/SOP, prevention, aggregate |

전체 목록: [`query_catalog_100.md`](query_catalog_100.md)

---

## 3. 테스트 결과

### 3.1 키워드 코퍼스 히트 (존재 여부)

| Band | 기준 | 건수 |
|------|------|------|
| **strong** | 관련 문서 ≥5 | **94** |
| **weak** | 1–4 | **6** |
| **none** | 0 | **0** |

**weak 6건 (문서 희소, 내용은 고품질):**
- E02 GRO offload 전량 종합 (3)
- M02 분야별 단가 (4)
- M06 1주2M/M·2주4M/M (3)
- M08 대외 SaaS 2달 전 요청 (2)
- M19 서비스 base vs M/M (1)
- M25 0.25M/M 1안 전체 분야 목록 (3)

→ 데이터 *부재*가 아니라 **희소·표 형태·동의어** 문제.

### 3.2 완전 답변 가능성 (설계 관점 재분류)

키워드 히트 ≠ 답변 완성. 재분류:

| 등급 | 의미 | 건수 | 시스템 준비 (v2.1 기본 RAG만) |
|------|------|------|-------------------------------|
| **A** | 단일/소수 문서 factoid | 30 | 검색+citation이면 가능 |
| **A+** | 구조화 체크리스트 | 3 | checkitem 전용 경로 필요 |
| **B** | 다중 문서 합성 | 43 | Hybrid RAG+Trust로 가능 |
| **C** | 전량/엔티티/예방 hop | 9 | **Planner+exhaustive 필수** |
| **D** | 코퍼스 통계·집계 | 7 | **Analytics API 필수** |
| **E** | 공수/대수 규칙+계산 | 8 | **capacity_rules+계산기 필수** |

**준비 상태 요약:**
- 즉시 경로로 가능(검색/RAG/checkitem): **76/100**
- Planner 보완 후: **+9 → 85**
- Rules 보완 후: **+8 → 93**
- Analytics 보완 후: **+7 → 100**

---

## 4. 페르소나별 갭

### 전문가 (40)
- 강점: 사례·테크리포·PISA 근거 풍부 → A/B 다수
- 갭: 모니모 카테고리화(C), GRO 전량(C), 예방 hop(C), 2주 대수(E), Oracle 항목 수(D)

### 관리자 (30) — **가장 설계 부담 큼**
- capacity 8건 중 다수가 E (표+환산)
- 연도별/Component/SCP 비중 등 D (메타데이터 집계)
- 정책·FAQ factoid는 A로 가능하나 **표 파싱** 품질이 생명

### 장애관리 (30)
- B 유형 중심 → RAG+유사장애 검색이 핵심
- 갭: 재발방지 전량(C), 긴급 체크리스트 번들(C), 장애지원 제목 패턴(D)
- mid-incident UX: **Fast QA + SOP 강제 링크 + Trust** 가 제품 차별점

---

## 5. “완벽 답변”을 위한 설계 보완 (필수 모듈)

### 5.1 Query Type Registry (확장)

| type | 설명 | 100문 매핑 예 |
|------|------|----------------|
| `factoid` | 단일 사실 | E06, M05, I14 |
| `technical_synthesize` | 다중 사례 종합 | E16, I02, I09 |
| `checklist` | PISA 필드 목록 | E05, E12, E40 |
| `entity_aggregate` | 시스템 단위 전량+분류 | E03, M07 |
| `prevention_map` | 사례→예방 가이드 | E04, I04, I27 |
| `capacity_lookup` | 공수/대수/단가 규칙 | E11, M01–M02, M25 |
| `aggregate_stats` | 코퍼스 통계 | M03, M04, M16, I23 |
| `risk_policy` | 정책·기조·리스크 문장 | M09, M15, M21 |
| `incident_similar` | 유사장애 (증상 벡터+키워드) | I17, I02 |
| `runbook_bundle` | mid-incident 패키지 | I01, I21 |

### 5.2 신규 컴포넌트

```
┌─────────────┐   ┌──────────────────┐   ┌─────────────────┐
│ Query       │──▶│ Router           │──▶│ Path handlers   │
│ (persona    │   │ (type+slots)     │   │ A search        │
│  optional)  │   └──────────────────┘   │ B rag           │
└─────────────┘                          │ C exhaustive    │
                                         │ D analytics     │
                                         │ E capacity calc │
                                         └────────┬────────┘
                                                  ▼
                                         Trust Banner + Cite
```

1. **Analytics Store** (Postgres SQL on documents metadata)  
   - `COUNT(*) GROUP BY year, component, entity_id`  
   - 제목 토큰 top-N  
   - **LLM 없이** 숫자 확정 (환각 방지)

2. **Capacity Rules Table** (시드 from FAQ)  
   ```
   period=1w, field=Linux, units=20, mm=0.25, basis=안1, source=QRB_FAQ
   ```
   - 2주 질의 → ×2 + “환산” 라벨  
   - 혼합 패키지 예시 행 별도 저장

3. **Incident Bundle Packs**  
   - `pack:linux-emergency` = Hang SOP + 핵심 PISA + 최근 hang 티켓 top  
   - 한 번의 질의로 묶음 반환 (mid-incident)

4. **Gold Eval Set = 본 100문**  
   - CI에서 band/ready 회귀  
   - List Completeness, Numeric Accuracy(capacity), Groundedness

### 5.3 Trust 규칙 보강

| 질문 유형 | Trust 규칙 |
|-----------|------------|
| capacity 환산 | 원표 citation + “2주=1주×2 (문서 직접 표 아님)” |
| aggregate_stats | SQL 결과 숫자만, LLM 문구 장식만 허용 |
| exhaustive | “N건 전량(상한 내)” 명시, 누락 시 ABSTAIN 부분 |
| weak 문서(1–3건) | 전량 나열 가능하나 일반화는 MEDIUM |

### 5.4 PR 추가 (v2.2)

| PR | 내용 | 해소 질문 |
|----|------|-----------|
| PR-22 | query catalog 100 gold + eval harness | 전체 회귀 |
| PR-23 | analytics API (year/component/entity counts) | M03,M04,M16,M23,I23,E26 |
| PR-24 | capacity_rules seed + calculator endpoint | E11,M01,M02,M25… |
| PR-25 | incident bundle packs | I01,I21 |
| PR-26 | persona-aware UI presets (전문가/관리자/장애) | UX |
| PR-27 | table extractor for FAQ/Confluence | weak 표 질의 강화 |

---

## 6. 목표 달성 로드맵 (100문 100% 완전 답)

| Phase | 범위 | 커버 목표 |
|-------|------|-----------|
| P1 검색+RAG+checkitem | A, A+, B | ~76% |
| P2 Planner+entity+exhaustive+prevention | +C | ~85% |
| P3 capacity_rules | +E | ~93% |
| P4 analytics | +D | **~100%** |

---

## 7. 결론

1. **코퍼스 품질:** 100문 중 문서 근거 0건은 없음. 지식 공백보다 **접근 경로 공백**.
2. **관리자 질문**이 시스템을 “검색엔진”이 아니라 **의사결정 지원 시스템**으로 만든다 → analytics + capacity 없으면 실패.
3. **장애 담당** 가치는 속도: Fast 프로필 + Bundle + SOP 강제.
4. **전문가** 가치는 정밀: Hybrid + lexicon + parent + Trust 기권.
5. 본 100문은 **영구 gold set**으로 채택하고, 설계 완료 조건 = **100문 eval 통과**.


---

## 8. 그룹장 유사장애 질의 (v2.3 추가)

### 8.1 대표 질문 형태
- 이와 **유사한 장애**가 이전에 있었는지
- **어떤 장애**였는지 (증상·영향·환경)
- **어떻게 해결**했는지
- **이번에도 그 해결방안이 도움이 되는지** (적용 가능성)

### 8.2 코퍼스
- 원인+조치 동시 서술 티켓 ~516건 — “어떻게 해결” 슬롯 공급에 충분
- 단순 유사도만으로 “도움이 된다” 단정 금지 → **applicability** 분리

### 8.3 설계
- 질의 유형 `similar_incident` (§3G)
- 4슬롯 응답 + 그룹장 1분 브리핑 UX
- Gold 확장 G01–G10, PR-28~30

### 8.4 실패 모드
| 실패 | 완화 |
|------|------|
| 증상만 비슷, 원인 축 다름 | 적용 비권고 + 차이점 명시 |
| 현재 환경 슬롯 공백 | 조건부/기권 + 확인 질문 |
| 조치 없는 티켓을 해결책으로 제시 | frame.quality 강등 |
| 장문 답변으로 그룹장 피로 | 3카드 브리핑 고정 |
