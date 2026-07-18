# CI-TEC 지식기반 검색 플랫폼 — 상세 구현 계획

| 항목 | 내용 |
|------|------|
| 문서 버전 | 1.0 |
| 기준 설계 | `CI-TEC_Knowledge_Platform_Design.html` **v2.3** |
| 평가 세트 | `query_catalog_100.json` (100+ G01–G10) |
| 환경 | 폐쇄망 · Docker 경량(5 서비스) · GLM 5.2 AWQ 4bit 130k |
| 사용자 | 초기 50–100명 |
| 작성일 | 2026-07-18 |

---

## 0. 한 페이지 요약

### 목표 제품
분산 지식(Jira 지원이력 · Confluence Tech-Repo · PISA · AI 분석)을 통합해  
**정확한 검색 · 근거 기반 답변 · 유사장애 브리핑 · 관리자 공수/통계 · 승인형 지식 환류**를 제공한다.

### 성공 정의 (출시 게이트)
| 게이트 | 기준 |
|--------|------|
| G0 | `docker compose up` 로 로컬/사내 VM 기동, raw 전량 ingest 완료 |
| G1 | Hybrid 검색 P@3 ≥ 0.90 (gold 50+) |
| G2 | RAG groundedness ≥ 0.95, Trust/기권 동작 |
| G3 | `similar_incident` 4슬롯 브리핑 + 적용성 판정 (G01–G10) |
| G4 | capacity 숫자 = Rules/SQL만, 환산 라벨 표시 |
| G5 | query catalog **≥ 95% pass**, numeric 문항 100% |
| G6 | 50–100명 부하 스모크, 검색 p95 ≤ 700ms / Fast QA TTFT ≤ 2.5s |

### 일정 총괄 (권장 12–16주)

```
W1–2   Phase 0  스파이크·기반
W3–7   Phase 1  고정밀 검색 MVP          → G0, G1
W8–11  Phase 2  Trust QA + 유사장애      → G2, G3
W12–14 Phase 3  Planner·Capacity·Analytics → G4, G5
W15–16 Phase 4  Flywheel·운영·하드닝     → G6
```

병행 가능 구간은 §4 DAG 참고. 인력 가정: BE 1.5 · FE 1 · ML/서빙 0.5 · 도메인 리뷰 0.3.

---

## 1. 전제 · 제약 · 비목표

### 1.1 전제 (착수 전 확정)
| # | 전제 | 미확정 시 영향 | 기본 가정 |
|---|------|----------------|-----------|
| P1 | GLM OpenAI-compatible 엔드포인트 | RAG 연동 지연 | chat/completions + stream |
| P2 | 130k tokenizer 실측 | packer 오설정 | HF tokenizer 기준 측정 |
| P3 | BGE-M3 웨이트 사내 반입 | 임베딩 불가 | worker 내 배치 또는 공용 API |
| P4 | raw 경로 read 가능 | ingest 불가 | `/data/raw` 볼륨 마운트 |
| P5 | 초기 SSO 연기 가능 | 인증 범위 | Phase1 IP allowlist |

### 1.2 제약
- 인터넷 egress 0 · 이미지/wheel 사내 레지스트리
- Docker 5서비스 유지: `web`, `api`, `worker`, `postgres`, `redis` (LLM은 compose 밖)
- 50–100명: 권장 8 vCPU / 16–24GB
- Precision > Coverage · 숫자 답변은 SQL/Rules만

### 1.3 본 계획의 비목표
- 자동 원격 조치 실행
- 소스 ACL 실시간 미러
- OpenSearch/Qdrant/Kafka 초기 도입
- LLM 파인튜닝

---

## 2. 목표 아키텍처 (구현 단위)

```
[raw/ | Jira/Conf API] 
    → adapters → Postgres(documents, sections, chunks, checkitems, entities, frames, rules)
    → embed job → pgvector + FTS
    → api: search | rag | similar_incident | capacity | analytics | insight
    → web: 검색 | 챗 | War-room | 관리자 표 | 승인
    → GLM 5.2 (external)
```

### 2.1 권장 레포 구조
```
cite-c-knowledge/
  docker-compose.yml
  apps/
    api/                 # FastAPI
    worker/              # arq/celery jobs
    web/                 # React or lightweight SPA
  packages/
    domain/              # schemas, enums
    retrieval/           # hybrid, rrf, packer
    trust/               # trust engine
    ingest/              # adapters, cleaners
  data/
    seeds/               # entities, lexicon, capacity_rules, bundles
    gold/                # query_catalog_100.json, G01-10
  docs/                  # design copies
  scripts/               # eval, smoke, backup
```

### 2.2 기술 스택 (고정)
| 영역 | 선택 |
|------|------|
| API | Python 3.12 · FastAPI · uvicorn |
| DB | Postgres 16 + pgvector · 내장 FTS |
| Queue | Redis + arq (또는 Celery) |
| Embed | BGE-M3 (worker) |
| LLM | 사내 GLM (httpx OpenAI client) |
| Web | React+Vite 또는 초기 Streamlit→이관 |
| 관측 | 구조화 로그 + Prometheus metrics (최소) |

---

## 3. 페이즈별 구현 계획

### Phase 0 — 스파이크 · 기반 (1–2주) → 게이트 G0 일부

| 주차 | 산출물 | 상세 작업 | DoD |
|------|--------|-----------|-----|
| W1 | 환경·모델 검증 | GLM health, max context, 스트리밍, 동시성 한도 측정 문서화 | 벤치 리포트 1장 |
| W1 | 임베딩 스파이크 | BGE-M3 로드 시간·QPS·메모리 | 의사결정: worker내장 vs 공용 |
| W1 | Gold 시드 | catalog 100+G10 을 `data/gold/` 이관, 평가 스키마 정의 | JSON 스키마 확정 |
| W2 | Compose skeleton | PR-01: 5 서비스, healthcheck, 볼륨, `.env.example` | `compose up` 성공 |
| W2 | DB 마이그레이션 | PR-02: 최소 스키마 (documents/chunks/jobs) | alembic upgrade head |

**리스크:** 모델 반입 지연 → Phase1 검색은 FTS-only로 진행 가능(임베딩 후행).

---

### Phase 1 — 고정밀 검색 MVP (3–5주) → G0, G1

**목표:** 부서원이 “매일 켜는” 통합 검색. 생성 기능 없음 또는 최소.

#### W3 — Ingest
| 작업 | PR | 내용 |
|------|-----|------|
| 어댑터 | PR-03 | `support_history`, `tech_repo`, `tuning_ai`, `confluence_docs` MD 파서 |
| Cleaner | PR-03 | Jira color 마크업 제거, 빈 본문 drop, hash upsert |
| Checkitems | PR-17(조기) | JSON → `checkitems` 정규 테이블 (검색 Phase1.5에서도 사용) |
| Job | PR-03 | `ingest_jobs` 통계, 실패 DLQ 로그 |

**DoD:** raw 전량 적재, documents 건수 ≈ 파일 수, 재실행 idempotent.

#### W4 — Index
| 작업 | PR | 내용 |
|------|-----|------|
| 섹션/청크 | PR-04 | MD 헤더 분할, contextual header, 512tok/overlap |
| FTS | PR-04 | `to_tsvector` 한국어 전략(simple/ngram 1차) |
| Embed | PR-04/05 | 배치 임베딩, chunk_id = point id |
| Taxonomy 규칙 | PR-06 | path_l2/l3, work_type, 키워드→domain 태그 |

**DoD:** 임의 티켓키·파라미터 exact 검색 성공, 벡터 검색 smoke.

#### W5 — Search API
| 작업 | PR | 내용 |
|------|-----|------|
| Hybrid | PR-05 | FTS top40 + vector top40 → RRF |
| Exact boost | PR-05 | CITECTS-*, pageId, 커널 파라미터 패턴 |
| Quality gate | PR-05 | 임계 미달 시 결과 축소/empty |
| Filters | PR-05/06 | source, date, domain, path, status |
| Lexicon 시드 | PR-19(조기 일부) | GRO/모니모 동의어 최소셋 |

**DoD:** gold 50문 retrieval P@3 측정 베이스라인 기록.

#### W6–7 — Search UI + 엔티티 기초
| 작업 | PR | 내용 |
|------|-----|------|
| 검색 UI | PR-07 | 필터, 소스 뱃지, 스니펫, 증거 등급 |
| Entity seed | PR-15 | 모니모 등 20–50 시스템 + linker 규칙 |
| Checkitem API | PR-17 | Area/Subject 필터 목록 API |
| Eval | PR-12 일부 | P@3 러너 스크립트 |

**Phase 1 종료 게이트 G1**
- [ ] P@3 ≥ 0.90 (또는 합의 베이스라인 대비 +X% with 이슈 목록)
- [ ] 검색 p95 ≤ 700ms (리랭크 전)
- [ ] checkitem “Linux FS” 질의 테이블 응답
- [ ] 사용자 파일럿 5–10명 피드백 1회

---

### Phase 2 — Trust QA + 그룹장 유사장애 (3–4주) → G2, G3

**목표:** 근거 있는 답변 + 그룹장 War-room 브리핑.  
**원칙:** 검색 품질 게이트 통과 전 Deep RAG 확대 금지.

#### W8 — Trust + RAG 코어
| 작업 | PR | 내용 |
|------|-----|------|
| Trust engine | PR-08 | Retrieval/Evidence/Faithfulness → 4단 배너 |
| 기권 규칙 | PR-08 | 임계 미달 ABSTAIN |
| GLM client | PR-09 | stream, token budget packer (Fast 8–20k) |
| Citation | PR-09 | [C#] 강제, faithfulness 1회 재시도 |
| 챗 UI | PR-10 | Trust Banner, 출처 패널 |

**DoD:** Fast QA 샘플 20문 groundedness 수동/자동 ≥ 0.95 목표.

#### W9 — Issue Frame + 유사장애 Retrieve
| 작업 | PR | 내용 |
|------|-----|------|
| Frame 추출 | PR-16 | 배치: symptom/cause/resolution/components (규칙 우선, LLM 보조) |
| quality 점수 | PR-16 | 원인+조치 동시 존재 가산 |
| SI retrieve | PR-28 | dual retrieve + Resolved/frame 가중 |
| 랭킹 | PR-28 | 증상·환경·조치완결성 |

#### W10 — Applicability + 그룹장 UX
| 작업 | PR | 내용 |
|------|-----|------|
| 적용성 스코어 | PR-29 | 가능/조건부/비권고/기권 |
| 4슬롯 브리핑 | PR-29 | 1분 요약 + 유사 3카드 |
| War-room 모드 | PR-26 일부 | 현재 장애 슬롯 입력 폼 |
| Bundle 시드 | PR-25 | linux-hang, network-timeout 2팩 |

#### W11 — 하드닝
| 작업 | PR | 내용 |
|------|-----|------|
| SI eval | PR-30 | G01–G10, false-apply 케이스 |
| 동시성 | — | Deep 세마포어 2, Fast 6 |
| 감사 로그 | PR-14 일부 | query/answer 저장 |

**Phase 2 종료 게이트 G2+G3**
- [ ] Trust 4단 UI 동작, 기권 시 단정 조치 문구 0건(샘플 감사)
- [ ] G01–G10 중 ≥ 8 pass (적용성 라벨 포함)
- [ ] 그룹장 시나리오 워크스루 1회 통과

---

### Phase 3 — Planner · Capacity · Analytics · 100문 (3주) → G4, G5

**목표:** 관리자 숫자 질문 + 전량/예방 + gold 95%+.

#### W12 — Query Planner
| 작업 | PR | 내용 |
|------|-----|------|
| Router | PR-18 | factoid / synthesize / checklist / exhaustive / capacity / analytics / similar_incident / prevention |
| Exhaustive | PR-18 | entity·lexicon 전량 스캔, 상한, completeness 메타 |
| Prevention hop | PR-20 | frame.components → PISA/tech path |
| Lexicon 확장 | PR-19 | GRO, monimo, SCP 등 |

#### W13 — Capacity + Analytics
| 작업 | PR | 내용 |
|------|-----|------|
| capacity_rules seed | PR-24 | FAQ 1주 표, 단가, mm_per_field |
| Calculator | PR-24 | 2주 환산 + citation + “환산” 라벨 |
| Analytics API | PR-23 | year/component/entity counts, title tokens |
| 표 추출 | PR-27 | FAQ/Confluence 표 보존 인덱싱 |

#### W14 — Gold 100 회귀
| 작업 | PR | 내용 |
|------|-----|------|
| Full eval | PR-22 | catalog 100 + G10 |
| 메트릭 대시보드 | PR-12 | nDCG, list completeness, numeric accuracy |
| 실패 분석 | — | 실패 문항 → 시드/규칙/파서 패치 스프린트 |

**Phase 3 종료 게이트 G4+G5**
- [ ] capacity 문항 숫자 100% Rules/SQL 일치
- [ ] analytics 문항 LLM 계수 0
- [ ] catalog pass ≥ 95%

---

### Phase 4 — Flywheel · 동기화 · 운영 (2주) → G6

| 작업 | PR | 내용 |
|------|-----|------|
| Insight 워크플로 | PR-11 | draft → 시니어 승인 → promote ingest |
| API 증분 | PR-13 | Jira/Confluence (네트워크 허용 시) |
| SSO·감사 | PR-14 | OIDC/SAML, audit, 백업 런북 |
| 부하 테스트 | — | 20 동시 검색, 5 동시 Fast QA |
| 운영 문서 | — | runbook, oncall, restore drill |
| Persona UI 완성 | PR-26 | 전문가/관리자/War-room 프리셋 |

**Phase 4 종료 게이트 G6**
- [ ] 50–100명 스모크, SLA 충족
- [ ] 백업/복구 리허설 1회
- [ ] 부서 파일럿 종료 보고

---

## 4. PR 의존성 DAG (실행 순서)

```
PR-01 compose
  └─ PR-02 schema
       ├─ PR-03 ingest ──────────────┬─ PR-06 taxonomy
       │                             ├─ PR-15 entity
       │                             ├─ PR-16 frames
       │                             └─ PR-17 checkitems
       └─ PR-04 index/embed
            └─ PR-05 hybrid search ──┬─ PR-07 search UI
                 │                   ├─ PR-08 trust
                 │                   │    └─ PR-09 rag ── PR-10 chat UI
                 │                   ├─ PR-19 lexicon
                 │                   └─ PR-12 eval (partial)
                 │
                 PR-18 planner ◄── PR-15,17,05
                 PR-28 SI ◄── PR-05,16
                      └─ PR-29 applicability UI
                           └─ PR-30 SI tests
                 PR-20 prevention ◄── PR-16,17,18
                 PR-23 analytics ◄── PR-02,03
                 PR-24 capacity ◄── PR-02 (+ FAQ seed)
                 PR-22 full gold ◄── PR-18,23,24,28,09
                 PR-25 bundles
                 PR-11 insight ◄── PR-09
                 PR-13 sync ◄── PR-03
                 PR-14 ops (횡단)
                 PR-26 persona UI
                 PR-27 table extract
```

### 병렬 트랙 (인력 2 BE 가정)
| 트랙 A (검색 코어) | 트랙 B (지식 구조) | 트랙 C (FE) |
|--------------------|--------------------|-------------|
| 01→02→03→04→05 | 15 entity, 17 checkitem, 16 frame | 07 search UI |
| 08→09 | 24 capacity seed, 23 analytics | 10 chat, 29 SI UI |
| 18 planner, 28 SI | 19 lexicon, 20 prevent | 26 persona |

---

## 5. 스프린트 백로그 (2주 스프린트 기준 예시)

### Sprint 1 (W1–2): Foundation
- [ ] Compose + CI lint
- [ ] Schema v1 + alembic
- [ ] GLM/embed 스파이크 리포트
- [ ] Gold JSON 레포 이관

### Sprint 2 (W3–4): Ingest & Index
- [ ] 4 adapters + cleaner
- [ ] Full raw backfill
- [ ] Chunk + FTS + embed batch
- [ ] Ingest metrics

### Sprint 3 (W5–6): Search MVP
- [ ] Hybrid API + filters + gate
- [ ] Search UI
- [ ] Entity monimo seed
- [ ] Checkitem list API
- [ ] P@3 baseline

### Sprint 4 (W7–8): Trust QA
- [ ] Trust engine + RAG Fast
- [ ] Chat UI + citations
- [ ] Faithfulness check
- [ ] Frame extraction job v1

### Sprint 5 (W9–10): Similar Incident
- [ ] SI pipeline + rank
- [ ] Applicability + 4-slot brief
- [ ] War-room UI
- [ ] Bundles ×2
- [ ] G01–G10 eval

### Sprint 6 (W11–12): Planner & Exhaustive
- [ ] Query router
- [ ] Exhaustive mode
- [ ] Prevention hop
- [ ] Lexicon full

### Sprint 7 (W13–14): Numbers & Gold
- [ ] Capacity calculator
- [ ] Analytics API
- [ ] Catalog 100 eval ≥95%
- [ ] Table extractor

### Sprint 8 (W15–16): Ship
- [ ] Insight approve loop
- [ ] Ops/SSO/backup
- [ ] Load test
- [ ] Pilot report

---

## 6. 데이터 모델 구현 순서

| 순서 | 테이블 | Phase | 비고 |
|------|--------|-------|------|
| 1 | sources, documents, ingest_jobs | 0–1 | SoR |
| 2 | document_sections, chunks, embeddings | 1 | 검색 |
| 3 | checkitems | 1 | 정규 JSON 분해 |
| 4 | entities, document_entities | 1–2 | 모니모 등 |
| 5 | lexicon_terms | 1–2 | GRO 등 |
| 6 | issue_frames | 2 | SI·예방 |
| 7 | capacity_rules, pricing_rules | 3 | 관리자 |
| 8 | queries, answers, feedback | 2 | 감사·학습 |
| 9 | insights | 4 | flywheel |
| 10 | bundles | 2–3 | war-room |

마이그레이션 규칙: **확장만, destructive 변경은 expand-contract**.

---

## 7. API 표면 (구현 계약)

### 7.1 Phase 1
```
POST /v1/search
  {q, filters{}, top_k, mode?}
  → {results[{doc_id, title, snippet, score, grade, tags}], trust_retrieval}

GET  /v1/checkitems?area=&q=
GET  /v1/documents/{id}
POST /v1/ingest/run  (admin)
GET  /v1/health
```

### 7.2 Phase 2
```
POST /v1/chat
  {q, mode: fast|deep, filters, stream}
  → SSE tokens + final {answer, citations[], trust}

POST /v1/similar-incident
  {symptom, context{env, product, service, started_at}, top_k=3}
  → {brief, cases[{what, cause, resolution, applicability, trust}], actions[], questions[]}
```

### 7.3 Phase 3
```
POST /v1/query   # unified router (optional facade)
GET  /v1/analytics/tickets?group_by=year|component|entity_id
GET  /v1/analytics/entity-share?entity=
POST /v1/capacity/estimate
  {period_days: 14, fields?: []}
  → {per_field_units, mm, basis, scale_note, citations[]}
```

### 7.4 Phase 4
```
POST /v1/insights  GET/PATCH /v1/insights/{id}/status
POST /v1/feedback
```

모든 응답: `request_id`, `citations[]` (해당 시), `trust` 객체.

---

## 8. 평가 · 품질 운영

### 8.1 Gold 세트 운영
| 세트 | 용도 | 빈도 |
|------|------|------|
| gold-50 retrieval | P@3, nDCG | PR마다 |
| gold-100 full | 라우팅·답변 | Phase3+ 주 1 |
| G01–G10 SI | 유사·적용성 | SI PR마다 |
| false-apply set | 적용 비권고 회귀 | SI PR마다 |
| capacity numeric | 숫자 일치 | capacity PR마다 |

### 8.2 메트릭 대시보드 (최소)
- ingest lag, fail count  
- search latency, empty rate  
- RAG TTFT, tokens  
- abstention rate, 👎 rate  
- SI applicability distribution  

### 8.3 품질 회의
- 격주 30분: 실패 gold 리뷰, 시니어 1인 + BE  
- 임계 변경은 gold 재측정 없이 금지  

---

## 9. 인력 · R&R

| 역할 | 책임 | 공수(권장) |
|------|------|------------|
| BE 리드 | API, retrieval, planner, SI | 1.0 |
| BE/데이터 | ingest, schema, analytics, capacity seed | 0.5–1.0 |
| FE | 검색·챗·War-room·관리자 표 | 1.0 |
| ML/서빙 | embed, GLM 연동, 토큰 벤치 | 0.3–0.5 |
| 도메인 시니어 | gold 라벨, frame 검수, capacity 시드 확정, 인사이트 승인 | 0.2–0.4 |
| 그룹장 스폰서 | War-room 시나리오 수용 테스트 | 마일스톤 시 2h |

---

## 10. 인프라 · 배포

### 10.1 환경
| 환경 | 용도 |
|------|------|
| dev | 개발자 compose |
| staging | 사내 VM, 전체 raw, 파일럿 |
| prod | 동일 스택, 리소스 권장 스펙 |

### 10.2 리소스 (prod 초기)
- 8 vCPU / 16–24GB RAM / 80GB disk  
- Postgres shared_buffers 튜닝, 백업 일 1회  
- GLM 동시성: Fast 6 / Deep 2 / SI 3  

### 10.3 보안 체크리스트
- [ ] egress deny  
- [ ] secrets vault  
- [ ] audit log 90일  
- [ ] 관리 API 권한 분리  
- [ ] 프롬프트에 근거 텍스트 untrusted 처리  

---

## 11. 리스크 등록부 (구현 관점)

| ID | 리스크 | 영향 | 완화 | 소유 |
|----|--------|------|------|------|
| R1 | 임베딩 반입 지연 | Phase1 벡터 지연 | FTS-first 출시 | ML |
| R2 | 한국어 FTS 품질 | P@3 미달 | ngram+lexicon, 이후 분석기 | BE |
| R3 | Frame 추출 품질 | SI 실패 | 규칙 우선, quality 강등 | BE+도메인 |
| R4 | 4bit 수치 오류 | 잘못된 조치 | citation 복사, Trust | BE |
| R5 | 적용성 오판 | 그룹장 오판 | false-apply 테스트, 기권 | BE |
| R6 | 범위 팽창 | 일정 지연 | Phase 게이트 엄수 | 리드 |
| R7 | SSO 지연 | 보안 이슈 | IP allow + 감사 | Ops |

---

## 12. 의사결정 로그 (착수 시 채울 것)

| 결정 | 옵션 | 권장 | 기한 |
|------|------|------|------|
| FE 초기 | Streamlit vs React | React(검색) / 챗 동시 | W2 |
| Embed 위치 | worker vs 공용 API | 공용 있으면 공용 | W1 |
| 리랭크 | 없이 vs 경량 CE | P@3 미달 시만 | G1 후 |
| SSO | Phase1 연기 vs 즉시 | Phase1 연기 | W2 |
| 인사이트 승인자 | 셀프 vs 시니어 | 시니어 필수 | W14 |

---

## 13. 주간 운영 리듬 (구축 기간)

| 요일 | 활동 |
|------|------|
| 월 | 스프린트 목표 정렬 15m |
| 수 | 통합 데모(검색 또는 SI) 20m |
| 금 | gold 회귀 결과 + 리스크 리뷰 30m |
| 격주 | 도메인 시니어 실패 케이스 라벨링 1h |

---

## 14. 마일스톤 체크리스트 (인쇄용)

### M0 착수 (W0)
- [ ] 스폰서·인력 확정  
- [ ] GLM/embed 접근 확인  
- [ ] raw 볼륨·레지스트리 준비  
- [ ] 본 계획 리뷰 승인  

### M1 검색 MVP (W7)
- [ ] G0, G1  
- [ ] 파일럿 사용자 온보딩  

### M2 지능 답변 + 유사장애 (W11)
- [ ] G2, G3  
- [ ] 그룹장 워크스루  

### M3 숫자·전량·gold (W14)
- [ ] G4, G5  

### M4 운영 이관 (W16)
- [ ] G6  
- [ ] runbook·백업·권한  
- [ ] 부서 공식 오픈  

---

## 15. 즉시 다음 액션 (이번 주)

1. **계획 리뷰 미팅** (60분): 페이즈 게이트·인력·SSO 결정  
2. **PR-01 착수**: monorepo + compose skeleton  
3. **W1 스파이크**: GLM 130k 실측 + BGE-M3 메모리  
4. **도메인 1h**: capacity_rules 표 FAQ 확정(1안), 모니모 엔티티 별칭 검수  
5. **gold 이관**: `design/query_catalog_*.json` → 레포 `data/gold/`  

---

## 16. 참고 산출물 맵

| 산출물 | 경로 |
|--------|------|
| 설계서 v2.3 | `design/CI-TEC_Knowledge_Platform_Design.html` |
| 질문 100 분석 | `design/Query_Catalog_100_Analysis.md` |
| Gold JSON | `design/query_catalog_100.json` |
| 본 구현 계획 | `design/IMPLEMENTATION_PLAN.md` |

---

**문서 끝.** 승인 후 Phase 0 / PR-01부터 실행하면 된다.
