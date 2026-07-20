# CI-TEC 지식기반 검색 플랫폼 — 상세 구현 계획

| 항목 | 내용 |
|------|------|
| 문서 버전 | **1.20** |
| 기준 설계 | `CI-TEC_Knowledge_Platform_Design.html` **v2.3** |
| 평가 세트 | gold-50 retrieval · SI G01–G10 · catalog-100 route+answer · time/list/capacity gold |
| 환경 | 폐쇄망 지향 · Docker 경량(5 서비스) · GLM 5.2 (dev: OpenRouter) |
| 사용자 | 초기 50–100명 |
| 레포 | **`~/dev/citec-kb`** |
| 작성일 | 2026-07-18 |
| 갱신 | **2026-07-20 — v1.20: mock OIDC IdP (RS256 JWKS) · full login e2e · OIDC_IDP_SETUP** |

### 문서 운영 규칙 (필수)

| 규칙 | 내용 |
|------|------|
| **매 작업 종료 시** | 본 문서 **현재 상태 · 해당 Phase 체크리스트 · §15 다음 액션 · 버전/갱신일** 을 같은 세션에서 갱신 |
| **완료 표기** | 코드만 있고 문서 미반영 = 미완료로 간주. 게이트/eval 수치 있으면 수치까지 기입 |
| **이월 표기** | “나중에” 항목은 **흡수 Phase / 별도 작업 / 명시적 비목표** 중 하나로 분류 (모호한 잔여 금지) |
| **웹 동기화** | MD 복사 + HTML 재생성: `.venv/bin/python scripts/render_implementation_plan.py` (HTML은 스냅샷이므로 MD만 고치면 v1.2처럼 어긋남) |

---

## 0. 한 페이지 요약

### 목표 제품
분산 지식(Jira 지원이력 · Confluence Tech-Repo · PISA · AI 분석)을 통합해  
**정확한 검색 · 근거 기반 답변 · 유사장애 브리핑 · 관리자 공수/통계 · 승인형 지식 환류**를 제공한다.

### 현재 상태 (2026-07-20)

| 항목 | 상태 |
|------|------|
| **진행 페이즈** | **P1–P3 강** · **P4 Insight+index+OIDC+SLA** |
| **엔지니어링 게이트** | **G0·G1 완료** · **G3/G4/G5 엔지니어링** · G2 파일럿 사인 잔여 · **G6 부분** (OIDC JWT+login · load/SLA pass · IdP 실연동·도메인 사인 잔여) |
| 레포/디렉터리 | `~/dev/citec-kb` |
| Compose | web **8572** · api **8573** · postgres **8574** · redis **8575** · **worker(job queue)** |
| 코퍼스 | documents **~9,439+** · chunks **~54,446+** · embeddings **~54,429+** · checkitems **4,434** · issue_frames **2,280** |
| 소스별 | support_history 2,280 · tech_repo 2,709 · checkitem 4,434 · tuning_ai 10 · confluence_docs 4 · **insight(promote+index)** |
| 임베딩 | e5-base · dim **768** · 배치 100% · **promote 시 document 단위 즉시 embed** |
| 검색 | Hybrid HTTP · **multi_query=true 기본** · promote 문서 FTS+vector 검색 가능 |
| Planner | `POST /v1/query` · capacity→analytics→list→SI→**prevention→exhaustive**→checklist→entity→hybrid |
| 품질 | retrieval hit@3 **0.96** · SI **1.0** · catalog **110/110** · unit tests **87** · pilot **13/13** · load/SLA **pass** · mock-IdP e2e |
| UI | search · chat · si · tickets · analytics · capacity · bundles · **insights(+reindex)** · `/docs/` |
| alembic | `20260718_0002` (vector 768) |
| 미완 핵심 | 파일럿 **도메인 사인** · 원격 push · Keycloak/Entra **실서버** 검증 · 부서 오픈 |

### 성공 정의 (출시 게이트)

| 게이트 | 기준 | 상태 |
|--------|------|------|
| G0 | `docker compose up` + raw 전량 ingest | **완료** |
| G1 | Hybrid 검색 hit@3 ≥ 0.90 (gold 50+) | **완료 (0.96)** |
| G2 | RAG groundedness ≥ 0.95, Trust/기권 동작 | **엔지니어링 1차** (러너·Trust·chat 있음; 파일럿·정식 게이트 수치 사인 잔여) |
| G3 | `similar_incident` 4슬롯 + 적용성 (G01–G10) | **엔지니어링 달성** (10/10; 그룹장 워크스루 잔여) |
| G4 | capacity 숫자 = Rules/SQL만 | **달성** (JSON + **DB 10/4 rows** · estimate `loaded_from=database`) |
| G5 | query catalog ≥ 95% pass | **route+answer 110/110** · prod multi-query **이식 완료** |
| G6 | 50–100명 스모크 · SLA | **부분** (OIDC+**mock IdP e2e** · load/SLA · 실 IdP·도메인 사인 잔여) |

### 일정 총괄

```
W1–2   Phase 0  스파이크·기반              ✅ 완료
W3–7   Phase 1  고정밀 검색 MVP → G0,G1   ✅ 완료 (2026-07-20)
W8–11  Phase 2  Trust QA + 유사장애 → G2,G3   ✅ 엔지니어링 강 / 파일럿·사인 잔여
W12–14 Phase 3  Planner·Capacity·Analytics → G4,G5   ✅ 핵심 강 / 하드닝·DB시드·prod multi-query 잔여
W15–16 Phase 4  Flywheel·운영·하드닝 → G6   ← Insight+OIDC+mockIdP+SLA ✅ / 실IdP·사인 잔여
```
---

## 1. 전제 · 제약 · 비목표

### 1.1 전제

| # | 전제 | 현재 | 비고 |
|---|------|------|------|
| P1 | GLM OpenAI-compatible | dev: OpenRouter `z-ai/glm-5.2` health ok | Phase 2 RAG 본사용 |
| P2 | 130k context 프로파일 | config에 max 130k | packer는 Phase 2 |
| P3 | 임베딩 웨이트 | **e5-base 로컬 스냅샷** (`/models` 마운트) | 계획의 BGE-M3 대신 e5-base 채택 |
| P4 | raw 경로 | `data/raw` + compose 볼륨 | 등록 완료 |
| P5 | 초기 SSO 연기 | 유지 | IP allow 가정 |

### 1.2 제약
- Docker 5서비스: `web`, `api`, `worker`, `postgres`, `redis` (LLM은 compose 밖)
- 호스트 포트 대역 **8572–8580**
- Precision > Coverage · 숫자 답변은 SQL/Rules만 (Phase 3)
- API 이미지에 **CPU torch + sentence-transformers** 포함 (쿼리 임베딩; 이미지 크기↑ 트레이드오프)

### 1.3 비목표
- 자동 원격 조치 실행
- 소스 ACL 실시간 미러
- OpenSearch/Qdrant/Kafka 초기 도입
- LLM 파인튜닝

### 1.4 Phase 1 비범위였던 기간·목록·집계 → Phase 3에서 **구현됨**

| 질의 유형 예 | Phase 1 당시 | 현재 (2026-07-20) | API/경로 |
|--------------|--------------|-------------------|----------|
| 「지난 주 지원건」 | hybrid 0건 **정상** | **time_scoped_list** | `POST /v1/query` · `GET /v1/tickets` |
| 「이번 달 장애 건수」 | 키워드 부적합 | **analytics** | `GET /v1/analytics/tickets` |
| 「2주 분야별 대수」 | 범위 밖 | **capacity** | `POST /v1/capacity/estimate` |
| catalog 100 | 미이관 | **route+answer 110/110** | `app.eval.catalog_*` |

원칙 유지: 기간·목록·집계·공수는 **Router + metadata/Rules** (LLM 계수·임시 핵 금지).
---

## 2. 목표 아키텍처 (구현 단위)

```
[data/raw | 향후 Jira/Conf API]
    → ingest adapters → Postgres (documents, chunks, checkitems, …)
    → embed CLI job → pgvector(768) + FTS
    → api: search | checkitems | ingest | health
         (+ Phase2: rag, similar_incident | Phase3: capacity, analytics)
    → web: 검색 UI · 설계 문서 정적 호스팅
    → GLM 5.2 (external OpenRouter / Fabrix)
```

### 2.1 실제 레포 구조 (현재)

```
citec-kb/
  docker-compose.yml
  apps/
    api/          # FastAPI · ingest · embed · retrieval · eval
    worker/       # heartbeat 스텁
    web/public/   # search.html · docs/* · index
  packages/domain/
  config/models.json
  data/raw/ · raw_manifest.json
  docs/           # 설계·구현 계획 원본
  scripts/
  .venv/          # 호스트 배치 임베드·eval용
```

> 초기 계획의 `packages/retrieval|trust` 분리 monorepo는 아직 미적용. 로직은 `apps/api/app/{retrieval,embed,eval}`에 응집.

### 2.2 기술 스택 (현재 고정)

| 영역 | 선택 |
|------|------|
| API | Python 3.12 · FastAPI · uvicorn |
| DB | Postgres 16 + pgvector · FTS `simple` |
| Queue | Redis (worker 스텁; 본 큐 미연결) |
| Embed | **intfloat/multilingual-e5-base (768-d)** · 배치 CLI + API 쿼리 인코드 |
| LLM | OpenRouter / Fabrix (Phase 1 검색 필수 아님; health probe만) |
| Web | **정적 HTML** (`search.html` + nginx) — React 이관은 이후 옵션 |
| 평가 | `python -m app.eval.retrieval` · gate=**hit@3** |

---

## 3. 페이즈별 구현 계획

### Phase 0 — 스파이크 · 기반 → ✅ 완료

| 산출물 | 상태 |
|--------|------|
| Compose 5서비스 · health · 포트 8572–8575 | ✅ |
| Alembic 스키마 (+ vector 768 마이그레이션) | ✅ |
| GLM OpenRouter probe | ✅ |
| e5-base 오프라인 로드 경로 | ✅ |

---

### Phase 1 — 고정밀 검색 MVP → G0, G1 → ✅ 엔지니어링 완료

**목표:** 부서원이 “매일 켜는” 통합 검색. 생성 기능 없음.

#### 구현 완료 맵

| 영역 | 내용 | 상태 |
|------|------|------|
| Ingest | support_history, tech_repo, tuning_ai, confluence_docs, checkitem 어댑터 · idempotent · stats | ✅ |
| Index | 청크 + FTS · 배치 임베드 keyset 페이징 · 진행 로그 | ✅ |
| Taxonomy | path/work_type/domain 규칙 태그 (필터용) | ✅ 기본 |
| Hybrid API | FTS+vector RRF · exact boost · quality gate · source 등 필터 | ✅ |
| 쿼리 임베드 | API 컨테이너 torch+ST · degrade 시 FTS-only + `embed_error` | ✅ |
| Checkitems | `GET /v1/checkitems?area=&q=` · Linux FS 동의어 확장 | ✅ |
| Search UI | 필터·뱃지·스니펫 · live API | ✅ |
| Eval | gold≥50 · **hit@3=0.96** · unit tests **58** (전 페이즈 합) | ✅ |
| 문서 포털 | design / implementation-plan / query-catalog 웹 제공 | ✅ |

#### Phase 1 종료 게이트 G1

- [x] Retrieval gate ≥ 0.90 — **hit@3 = 0.96** (hybrid, `vector_used`; classic p@3 별도 기록)
- [x] 검색 p95 ≤ 700ms (리랭크 전, warm) — **≈ 376ms**
- [x] checkitem “Linux FS” 테이블/검색 응답
- [ ] 사용자 파일럿 5–10명 피드백 1회 — **Phase 1.5** (엔지니어링 게이트와 분리 · **자동 흡수 안 됨**)

#### Phase 1 잔여(비차단) — 처분 (자동으로 다 되는가?)

**결론: 아니다.** Phase 2/3을 진행해도 **일부만 흡수**되고, 나머지는 **명시적 별도 작업**이 필요하다.  
“비차단” = G1 출시에 막지 않음이지, **무시해도 됨이 아님**.

| 항목 | 흡수 여부 | 처분 | 현재 | 다음에 할 일 |
|------|-----------|------|------|--------------|
| **catalog 100 이관·eval** | **Phase 3에 흡수·완료** | 완료 | `query_catalog_100.json` + route/answer 110/110 | 회귀 유지; prod multi-query 이식은 P3 후속 |
| **기간·목록·집계 (P1 비범위)** | **Phase 3에 흡수·완료** | 완료 | tickets / analytics / planner | title_tokens·exhaustive 등 확장만 잔여 |
| **classic p@3 개선** | **흡수 안 됨 · 비목표에 가깝** | **별도 안 함** (명시) | p@3≈0.32 참고만 | multi-label gold 재설계 없으면 착수 금지 |
| **Entity seed (모니모 등)** | **별도 작업으로 완료 (v1.9)** | **완료** | entities **5** · document_entities **549** | 유지·확장 시드 JSON |
| **worker 실큐** | **흡수 안 됨** | **별도 (필요 시 P2+)** | heartbeat 스텁 | 비동기 잡(재임베드·frame job 큐) 도입 시 구현 |
| **git 커밋/원격 정리** | **흡수 안 됨** | **별도 (운영 위생)** | uncommitted 다수 | 의미 단위 커밋 · 원격 정책 합의 후 push |
| **파일럿 5–10명 (1.5)** | **흡수 안 됨** | **별도 (조직)** | 체크리스트만 존재 | `PHASE2_PILOT_CHECKLIST.md` 실행·사인 |

---

### Phase 2 — Trust QA + 그룹장 유사장애 (3–4주) → G2, G3 — **엔지니어링 강 / 사인 잔여**
**목표:** 근거 있는 답변 + 그룹장 War-room 브리핑.  
**원칙:** 검색 품질 게이트(G1) 통과 전 Deep RAG 확대 금지 — **현재 충족, RAG 착수 가능**.

#### W8 — Trust + RAG 코어
| 작업 | PR | 내용 | 상태 |
|------|-----|------|------|
| Trust engine | PR-08 | Retrieval/Evidence/Faithfulness → 다차원 배너 (단일 % 없음) | ✅ 1차 (`app/trust`) |
| 기권 규칙 | PR-08 | 임계 미달 ABSTAIN | ✅ 1차 |
| GLM client | PR-09 | complete + stream helper, packer | ✅ 1차 (`llm_chat`, `rag/packer`) |
| Citation | PR-09 | [C#] 강제 프롬프트 + 사용 인용 추출 | ✅ 1차 (재시도 루프는 후속) |
| Fast RAG API | PR-09/10 | `POST /v1/chat` mode=fast | ✅ |
| Deep mode | PR-09 | context 40k · top_k 16 · 구조화 프롬프트 | ✅ 1차 |
| SSE stream | PR-10 | `stream=true` → meta/token/done 이벤트 | ✅ 1차 |
| 챗 UI | PR-10 | Trust Banner · deep/SSE 토글 | ✅ (`/chat.html`) |
| Groundedness sample | PR-12 | 20문 자동 메트릭 러너 | ✅ (`app.eval.groundedness`) |
| Citation 강제 재시도 | PR-09 | 무인용 답변 rewrite + 자동 보강 | ✅ |
| Faithfulness LLM 재판정 | PR-09 | 1회 재생성 루프 | 후속 (overlap 휴리스틱) |

#### W9 — Issue Frame + 유사장애 Retrieve
| 작업 | PR | 내용 | 상태 |
|------|-----|------|------|
| Frame 추출 | PR-16 | rules_v2 · support_history **2,280 frames** · both-slot 소수 | ✅ |
| Frame API | PR-16 | extract / stats / by key | ✅ |
| SI retrieve | PR-28 | hybrid + frame quality + applicability | ✅ |
| SI UI | PR-29 | `/si.html` 4슬롯 + 적용성 | ✅ |
| 랭킹 고도화 | PR-28 | Resolved 가중 · dual embed | 후속 |

#### W10 — Applicability + 그룹장 UX
| 작업 | PR | 내용 | 상태 |
|------|-----|------|------|
| 적용성 스코어 | PR-29 | 가능/조건부/비권고/기권 | ✅ 휴리스틱 |
| 4슬롯 브리핑 | PR-29 | 1분 요약 + 유사 3카드 | ✅ |
| Bundle 시드 | PR-25 | linux-hang, network-timeout + 매칭 UI | ✅ 읽기 전용 |
| Bundle 쓰기 API | PR-25 | 온라인 편집·저장 | 잔여 |

#### W11 — 하드닝
| 작업 | 내용 | 상태 |
|------|------|------|
| SI eval | G01–G10 + false-apply | ✅ pass_rate=1.0 |
| 동시성 한도 | Deep 2 / Fast 6 | 잔여 (설정 강화) |
| 감사 로그 | query/answer 저장 | 잔여 |

**Phase 2 게이트 G2+G3**
- [x] Trust 배너·기권·Fast/Deep·SSE 엔지니어링
- [x] G01–G10 ≥ 8 pass — **10/10**
- [ ] Trust 4단 UI 파일럿 확인 · 기권 시 단정 조치 문구 0건 (현장)
- [ ] 그룹장 시나리오 워크스루 1회
---

### Phase 3 — Planner · Capacity · Analytics · 100문 → G4, G5

**목표:** 관리자 숫자 + 전량/예방 + **기간·목록형 질의** + gold 95%+.

> Phase 1에서 미구현: 「지난 주 지원건」 등 — hybrid 0건이 정상.  
> **PR-18 router + 기간 파서 + PR-23 tickets/analytics** 에서 해결.

#### W12 — Query Planner
| 작업 | PR | 내용 |
|------|-----|------|
| Router | PR-18 | factoid / synthesize / checklist / **time_scoped_list** / exhaustive / capacity / analytics / SI / prevention |
| 상대 기간 파서 | PR-18 | 지난 주·이번 달·최근 N일 → date_from/to (KST) |
| Exhaustive | PR-18 | 전량 스캔 · completeness 메타 |
| Prevention hop | PR-20 | frame → PISA/tech |
| Lexicon | PR-19 | GRO, monimo, SCP |

#### W13 — Capacity + Analytics + 기간 목록
| 작업 | PR | 내용 |
|------|-----|------|
| capacity_rules | PR-24 | FAQ 표 · 환산 |
| Analytics | PR-23 | year/component/entity counts |
| **기간 목록** | **PR-23** | `GET /v1/tickets?date_field=Created|Resolved&from=&to=` (metadata, not ingest `created_at`) |
| **time_scoped_list** | PR-18+23 | 키워드 없이 기간+소스 목록; 건수는 COUNT |
| 표 추출 | PR-27 | Confluence/FAQ 표 |

#### W14 — Gold 100
| 작업 | 내용 |
|------|------|
| Full eval | catalog 100 + G10 |
| 기간·목록 gold | 「지난 주 지원건」 등 5–10문 |
| 메트릭 | nDCG, list completeness, numeric accuracy |

**Phase 3 진행 (2026-07-20)**
- [x] 상대 기간 파서 KST (`app/query/time_range.py`) — 지난 주·이번 달·최근 N일 등
- [x] `GET /v1/tickets` — metadata Created/Resolved/Updated 필터 (ingest `created_at` 아님)
- [x] `POST /v1/query/route` · `POST /v1/query` — full planner
  (capacity → analytics → time_scoped_list → SI → checklist → entity_aggregate → hybrid)
- [x] UI `/tickets.html` · `/analytics.html` · `/capacity.html`
- [x] unit tests time_range · analytics · capacity · planner
- [x] analytics / capacity / tickets APIs
- [x] route gold 14 + catalog-100 **route pass_rate=1.0** (`app.eval.catalog_route`)
- [x] capacity rules seed (FAQ 1안) + estimate API
- [x] catalog **answer-level** ≥ 95% — **110/110** (`app.eval.catalog_answer`)
  - capacity/analytics/checklist/SI: 구조화 결과 검증
  - hybrid: multi-query (원질 + any + sample id) hit@10 / keyword
- [ ] exhaustive / prevention hop / title_tokens analytics
- [x] capacity_rules / pricing_rules **DB 시드** (10 + 4 rows · `POST /v1/capacity/seed`)
- [x] Entity seed + document links (`POST /v1/entities/seed` · monimo 48건 등)
- [x] production multi-query hybrid (`app/retrieval/multi_query.py` · `/v1/search` default on)
- [x] `GET /v1/analytics/title_tokens` · planner title_tokens 모드
- [x] prevention hop (SI + checkitems)
- [x] exhaustive multi-hybrid + completeness 메타

**Phase 3 게이트**
- [x] capacity 숫자 Rules 100% (`llm_used=false`) — JSON + **DB**
- [x] analytics LLM 계수 0 · entity_share **document_entities** 경로
- [x] catalog **routing** ≥ 95% (110/110)
- [x] catalog **answer-level** ≥ 95% (110/110)
- [x] 기간 목록 router→list
- [x] prod multi-query hybrid 이식
- [x] title_tokens / prevention / exhaustive 시드 구현
- [x] bundle 쓰기 API (POST/PUT/DELETE) · UI 저장 폼
- [x] lexicon_terms 시드 + search FTS 동의어 연동
- [x] chat multi-query 기본 on (`retrieval.multi_query`)
- [x] query/answer 감사 로그 (`queries`/`answers` · `GET /v1/queries/recent`)
- [x] worker Redis job queue (`POST /v1/jobs` · ping/seed handlers · heartbeat)
- [x] load smoke script (`scripts/load_smoke.py`)

---

### Phase 4 — Flywheel · 동기화 · 운영 → G6

| 작업 | PR | 내용 | 상태 |
|------|-----|------|------|
| Insight 승인 | PR-11 | draft → review → approved/rejected · reopen · optional promote Document | **✅** |
| Promote 인덱싱 | PR-11 | upsert draft → chunk+FTS → embed(document_id) · `POST …/reindex` | **✅** |
| Feedback | PR-11 | `POST /v1/feedback` (answer\|insight\|search · rating ±1) | **✅** |
| Insight UI | PR-11 | `/insights.html` 승인 보드 · Reindex | **✅** |
| API 증분 동기화 | PR-13 | Jira/Confluence (허용 시) | 미착수 |
| SSO·감사 | PR-14 | OIDC JWT · **mock IdP RS256** · login e2e · Keycloak 가이드 · RBAC | **✅ 엔지니어링** (실 IdP 서버 잔여) |
| 부하 테스트 | — | concurrent search + health + planner | **load_sla_report pass** (gate c=8 · stress c=20 info) |
| Persona UI | PR-26 | 전문가/관리자/War-room | 미착수 |

---

## 4. PR 의존성 DAG

```
PR-01 compose ✅
  └─ PR-02 schema ✅
       ├─ PR-03 ingest ✅ ──────────┬─ PR-06 taxonomy ✅(기본)
       │                             ├─ PR-15 entity        ← 다음 병행 가능
       │                             ├─ PR-16 frames        ← Phase 2
       │                             └─ PR-17 checkitems ✅(목록 API 포함)
       └─ PR-04 index/embed ✅
            └─ PR-05 hybrid search ✅ ──┬─ PR-07 search UI ✅
                 │                       ├─ PR-08 trust      ← Phase 2 NEXT
                 │                       │    └─ PR-09 rag ── PR-10 chat UI
                 │                       ├─ PR-19 lexicon
                 │                       └─ PR-12 eval ✅(partial gold-50)
                 │
                 PR-18 planner ◄── Phase 3
                 PR-28 SI ◄── PR-05✅,16
                 PR-23 analytics · PR-24 capacity ◄── Phase 3
                 PR-22 full gold · PR-11 insight · PR-13 sync · PR-14 ops
```

---

## 5. 스프린트 백로그

### Sprint 1–3 (Foundation · Ingest · Search MVP) — ✅ 완료
- [x] Compose + schema + alembic
- [x] 4+ adapters + full raw backfill + ingest metrics
- [x] Chunk + FTS + embed batch (100%)
- [x] Hybrid API + filters + gate + HTTP query embed
- [x] Search UI · Checkitem list API · hit@3=0.96
- [x] Entity monimo 등 정식 seed (5 entities · 549 links)
- [ ] 파일럿 온보딩 (Phase 1.5) — **별도** (조직)

### Sprint 4 (W7–8): Trust QA — ✅ 엔지니어링 / 사인 잔여
- [x] Trust + Fast/Deep RAG + SSE + chat UI + citation rewrite
- [x] Frame extraction (2,280 frames)
- [ ] Faithfulness LLM 재판정 루프 (후속)

### Sprint 5 (W9–10): Similar Incident — ✅ 엔지니어링 / 워크스루 잔여
- [x] SI pipeline + UI + bundles 읽기 + G01–G10 pass_rate=1.0
- [x] Groundedness 20문 러너
- [x] 번들 쓰기 API · UI
- [ ] 그룹장 워크스루 사인

### Sprint 6–7: Planner · Analytics · Gold 100 — ✅ 핵심 / 하드닝 잔여
- [x] list · analytics · capacity · full planner · UIs
- [x] catalog route+answer **110/110**
- [ ] Exhaustive · prevention · lexicon
- [x] capacity DB · Entity seed · multi-query · title_tokens · prevention · exhaustive

### Sprint 8: Ship
- [x] worker 실큐 (Redis list + job status)
- [x] load smoke (concurrent search)
- [x] Insight approve/reject/reopen + promote + feedback + UI
- [x] **promote → chunk/FTS/embed** · hybrid 검색 e2e · `POST …/reindex`
- [x] ops/status · pilot_tech_check · postgres backup
- [x] Auth scaffold (`AUTH_MODE` · roles · `/v1/auth`) · write-path RBAC
- [x] Formal **load/SLA report** (`scripts/load_sla_report.py` · `data/reports/`)
- [x] **OIDC** JWT validate (JWKS RS* + local HS256) · login/callback · login.html · dev mint
- [x] **Mock OIDC IdP** `/v1/mock-idp` RS256 · full login→callback e2e · `docs/OIDC_IDP_SETUP.md`
- [ ] Keycloak/Entra **실서버** 연동 검증 · 파일럿 도메인 사인
- [ ] git 원격 push (정책 승인 후)
---

## 6. 데이터 모델

| 순서 | 테이블 | Phase | 상태 |
|------|--------|-------|------|
| 1 | sources, documents, ingest_jobs | 0–1 | ✅ |
| 2 | document_sections, chunks, embeddings(768) | 1 | ✅ (~54k) |
| 3 | checkitems | 1 | ✅ (4,434) |
| 4 | entities, document_entities | 1–2 | ✅ **5 entities · 549 links** (`/v1/entities/seed`) |
| 5 | lexicon_terms | 1–2 | ✅ **10 terms** (`POST /v1/lexicon/seed` · FTS variants 연동) |
| 6 | issue_frames | 2 | ✅ **2,280행** 적재 (both-slot 품질은 코퍼스 한계) |
| 7 | capacity_rules, pricing_rules | 3 | ✅ **10 + 4 rows** · calculator prefers DB |
| 8 | queries, answers, feedback | 2–4 | ✅ queries/answers 감사 · **feedback POST** |
| 9 | insights | 4 | ✅ draft/review/approved/rejected · promote→documents |
| 10 | bundles | 2–3 | ✅ seed json + **POST/PUT/DELETE write API** (파일 기반) |

규칙: **확장만**, destructive는 expand-contract.

---

## 7. API 표면

### 7.1 Phase 1 (구현됨)

```
POST /v1/search
  {q, filters{source_type,domain,environment,work_type,path_l2,status}, top_k}
  → {results[{…, fts_rank, vec_rank}], trust_retrieval, vector_used, embed_degraded?}

GET  /v1/checkitems?area=&q=&category_1=&limit=&offset=
POST /v1/ingest/run
GET  /v1/ingest/stats · /v1/ingest/jobs
GET  /v1/health · /v1/health/llm
```

호스트 CLI (배치/평가):

```
python -m app.embed.cli [--batch-size N] [--limit N]
python -m app.eval.retrieval [--out path] [--no-vector]
```

### 7.2 Phase 2

```
# 구현됨 (Fast)
POST /v1/chat
  {q, mode: "fast", top_k, filters{}, max_context_tokens?}
  → {answer, abstained, trust{level,banner,retrieval,evidence,faithfulness,reasons,confidence_pct:null},
     citations[{id,title,external_id,snippet,…}], citations_used, retrieval{}, llm_error?}

# 구현됨 (SI / Frames)
POST /v1/similar-incident
  {symptom, environment?, product?, service?, top_k?}
  → {brief, cases[{what,cause,resolution,applicability,components,…}], actions, questions}
POST /v1/frames/extract  {source_type?, limit?, force?}
GET  /v1/frames/stats
GET  /v1/frames/{external_id}

# 계획
POST /v1/chat  {mode, multi_query, audit, stream}
GET  /v1/queries/recent?limit=
```

### 7.3 Phase 3

```
# 구현됨
POST /v1/query                    # full planner facade (execute default)
POST /v1/query/route              # same; execute=false → plan only
GET  /v1/tickets?…&relative=지난+주
GET  /v1/analytics/tickets?group_by=year|month|component|status|assignee|total
GET  /v1/analytics/entity_share?entity=모니모
GET  /v1/capacity/rules
POST /v1/capacity/estimate  {period_days, fields?, basis?}
```

Planner intents: `capacity` · `analytics`(+title_tokens) · `time_scoped_list` ·
`similar_incident` · `prevention` · `exhaustive` · `checklist` · `entity_aggregate` · `hybrid_search`.  
날짜·집계·공수 숫자는 metadata / Rules (LLM 계수·숫자 생성 금지).

```
GET  /v1/analytics/title_tokens?component=장애지원&top_k=20
```

### 7.4 Phase 4 (Insight · Feedback · Index) — ✅

```
POST   /v1/insights
GET    /v1/insights?status=&limit=&offset=
GET    /v1/insights/{id}
PATCH  /v1/insights/{id}          # draft|rejected only
POST   /v1/insights/{id}/submit   # → review
POST   /v1/insights/{id}/approve  # body: {reviewer?, promote?}
POST   /v1/insights/{id}/reject
POST   /v1/insights/{id}/reopen   # review|rejected → draft
POST   /v1/insights/{id}/reindex  # approved/promoted → re-chunk + embed
POST   /v1/feedback               # target_type answer|insight|search · rating ±1
```

상태 머신: `draft ⇄ review → approved` · `draft|review → rejected → draft(reopen)`.  
`promote=true` 시:
1. `DocumentDraft` upsert (`source_type=insight`, `evidence_grade=draft`)
2. section/chunk + FTS (`to_tsvector`)
3. `embed_pending_chunks(document_id=…)` — 응답 `index.{chunks,embeddings,embedded,model}`
4. hybrid `/v1/search` 로 즉시 검색 가능 (e2e unique-token 검증)

---


### 7.5 Auth / OIDC — ✅ 엔지니어링 (IdP 실서버 잔여)

```
GET  /v1/auth/me
GET  /v1/auth/status
GET  /v1/auth/login?return_to=&redirect=
GET  /v1/auth/callback?code=&state=
GET  /v1/auth/logout
POST /v1/auth/dev/token     # HS256 mint when OIDC_JWT_SECRET (dev)
POST /v1/auth/introspect
```

- `AUTH_MODE=off|apikey|oidc_stub|oidc` (default **off**)
- JWT: local **HS256** (`OIDC_JWT_SECRET`) · IdP **JWKS RS*** (discovery)
- Auth code: discovery → authorize → token exchange → fragment redirect to web
- Roles from claim `roles` / `realm_access.roles` / `groups` (+ kb-admin mapping)
- UI: `/login.html` (OIDC button · dev mint · localStorage Bearer)
- Enforced writes: insights author+/senior+ · jobs admin
- **Mock IdP** (dev): `/v1/mock-idp` discovery · authorize · token · jwks (RS256)
- Guide: `docs/OIDC_IDP_SETUP.md`

## 8. 평가 · 품질 운영

### 8.1 Gold

| 세트 | 용도 | 상태/빈도 |
|------|------|-----------|
| gold-50 retrieval | **hit@3** (주), p@3 (부) | ✅ hit@3=0.96 · p@3 참고만 (개선 비목표) |
| catalog-100 | 라우팅·답변 | ✅ route 110/110 · answer 110/110 (`catalog_route` / `catalog_answer`) |
| time_list_analytics_10 | 기간·집계·공수 라우팅 | ✅ 14문 pass_rate=1.0 |
| G01–G10 SI | 유사·적용성 | ✅ `app.eval.si_eval` pass_rate=1.0 |
| capacity numeric | Rules 일치 | ✅ unit test + estimate e2e (Linux 2주=40 등) |
| qa_groundedness_20 | RAG 근거 | ✅ 러너 존재 · 정기 회귀 권장 |

### 8.2 게이트 지표 정책

- Phase 1 retrieval 게이트 = **hit@3 ≥ 0.90** (단일 정답 라벨 친화)
- classic Precision@3는 베이스라인으로만 보고 (현재 ≈0.32)
- 임계 변경 시 gold 재측정 필수

### 8.3 메트릭 (목표)
- ingest lag, fail count  
- search latency · empty rate · `vector_used` 비율  
- RAG TTFT, tokens (Phase 2)  
- abstention / 👎 / SI applicability (Phase 2+)

---

## 9. 인력 · R&R

| 역할 | 책임 | 공수(권장) |
|------|------|------------|
| BE 리드 | API, retrieval, planner, SI | 1.0 |
| BE/데이터 | ingest, schema, analytics, capacity | 0.5–1.0 |
| FE | 검색·챗·War-room·관리자 | 1.0 |
| ML/서빙 | embed, GLM, 토큰 벤치 | 0.3–0.5 |
| 도메인 시니어 | gold, frame, capacity 시드, 인사이트 승인 | 0.2–0.4 |
| 그룹장 스폰서 | War-room 수용 테스트 | 마일스톤 시 |

---

## 10. 인프라 · 배포

### 10.1 환경

| 환경 | 용도 | 현재 |
|------|------|------|
| dev | compose on host | **가동 중** (`citec-kb`) |
| staging | 사내 VM 전량 raw | 미구축 |
| prod | 동일 스택 | 미구축 |

### 10.2 리소스 (prod 초기 권장)
- 8 vCPU / 16–24GB RAM / 80GB+ disk  
- API는 쿼리 임베딩용 RAM 여유 필요 (e5-base CPU)  
- GLM 동시성: Fast 6 / Deep 2 / SI 3  

### 10.3 포트 (dev)

| 포트 | 서비스 |
|------|--------|
| 8572 | web |
| 8573 | api |
| 8574 | postgres |
| 8575 | redis |

### 10.4 보안 체크리스트
- [ ] egress deny  
- [ ] secrets vault  
- [ ] audit log 90일  
- [ ] 관리 API 권한 분리  
- [ ] 프롬프트 근거 텍스트 untrusted 처리  

---

## 11. 리스크 등록부

| ID | 리스크 | 상태 | 완화 |
|----|--------|------|------|
| R1 | 임베딩 반입 지연 | **완화됨** | e5 오프라인 + 전량 임베드 완료 |
| R2 | 한국어 FTS 품질 | 진행중 | 동의어 확장·exact boost; lexicon Phase 2–3 |
| R3 | Frame 추출 품질 | 미착수 | 규칙 우선 · quality 강등 |
| R4 | 4bit 수치 오류 | 미착수 | citation · Trust |
| R5 | 적용성 오판 | 미착수 | false-apply 테스트 |
| R6 | 범위 팽창 | 감시 | Phase 게이트 엄수 · 기간질의 Phase3 고정 |
| R7 | SSO 지연 | 수용 | IP allow + 이후 SSO |
| R8 | 기간·목록 기대 불일치 | **문서화됨** | UX 안내 + Phase3 list/analytics |
| R9 | API 이미지 비대화 (torch) | 수용 | CPU wheel · 필요 시 사이드카 분리 검토 |

---

## 12. 의사결정 로그

| 결정 | 선택 | 일자 |
|------|------|------|
| 레포/디렉터리명 | **citec-kb** (`~/dev/citec-kb`) | 2026-07-18 |
| FE 초기 | **정적 HTML 검색 UI** (React 이후 옵션) | 2026-07 |
| Embed 모델 | **multilingual-e5-base 768** (BGE-M3 대신) | 2026-07 |
| Embed 실행 | 호스트 CLI 배치 + API 인프로세스 쿼리 인코드 | 2026-07-20 |
| Retrieval 게이트 | **hit@3 ≥ 0.90** (p@3 참고) | 2026-07-20 |
| 기간·목록 질의 | Phase3 정식 구현 (tickets/analytics/planner) | 2026-07-20 |
| catalog answer eval | multi-query hybrid 허용 (any+sample id) | 2026-07-20 |
| classic p@3 개선 | **비목표** (multi-label gold 없이 착수 금지) | 2026-07-20 |
| P1 비차단 처분 | 흡수 완료 vs 별도 작업 표로 고정 | 2026-07-20 |
| 파일럿 피드백 | G1과 분리 (Phase 1.5) · 자동 흡수 안 됨 | 2026-07-20 |
| 계획 현행화 | **매 작업 세션 종료 시 본 문서 갱신** | 2026-07-20 |
| SSO | Phase1 연기 | 유지 |
| 인사이트 승인자 | 시니어 필수 | 유지 |

---

## 13. 주간 운영 리듬

| 요일 | 활동 |
|------|------|
| 월 | 스프린트 목표 정렬 15m |
| 수 | 통합 데모 (검색 유지 회귀 + Phase2 진행) 20m |
| 금 | gold hit@3 스모크 + 리스크 리뷰 30m |
| 격주 | 도메인 시니어 실패 케이스 라벨링 1h |

---

## 14. 마일스톤 체크리스트

### M0 착수
- [x] 레포·compose·raw·embed 경로  
- [x] 본 계획 실행 (Phase 0–1)  
- [ ] 스폰서·상시 인력 확정 (조직)  

### M1 검색 MVP
- [x] G0, G1 엔지니어링  
- [ ] 파일럿 사용자 온보딩 (1.5) — **별도**  

### M2 지능 답변 + 유사장애
- [x] G2/G3 **엔지니어링** (chat·Trust·SI eval)  
- [ ] G2 현장 사인 · 그룹장 워크스루  

### M3 숫자·전량·gold
- [x] G4 시드 · G5 catalog route+answer eval  
- [ ] G4 DB 시드 · prod multi-query · exhaustive  

### M4 운영 이관
- [x] Insight flywheel (API·UI·feedback·promote·**chunk/embed index**)  
- [x] ops/status · pilot_tech_check · backup · worker  
- [x] Auth scaffold + formal load/SLA report  
- [x] OIDC JWT validation + login/callback UI  
- [x] Mock OIDC IdP full e2e + setup guide  
- [ ] 실 IdP 서버 검증 · 부서 공식 오픈 · 도메인 사인  

---

## 15. 즉시 다음 액션

### 별도 수행 (자동 흡수 안 됨)
1. **파일럿 1.5 도메인 사인** — 사람 워크스루 (기술 점검 `pilot_tech_check` 13/13 완료)  
2. **원격 push** — 로컬 커밋 후 정책에 따라 push  

### 제품 하드닝 (P4 잔여)
3. **Keycloak/Entra 실서버**에 OIDC_ISSUER 연결 검증 (가이드: `docs/OIDC_IDP_SETUP.md`)  
4. 파일럿 도메인 사인 · 원격 push  
5. Persona UI — 선택 · promote embed 비동기 — 선택  

### 완료 스냅샷 (2026-07-20 v1.20)
- **Mock OIDC IdP** RS256 JWKS · login→callback→Bearer e2e · unit tests **87**  
- Guide `OIDC_IDP_SETUP.md` · 선행: OIDC JWT · load/SLA · Insight · pilot 13/13  

### 현행화 체크 (매 작업 종료)
- [x] §0 현재 상태 표 수치/페이즈 갱신  
- [x] 해당 Sprint/Phase 체크박스 반영  
- [x] §15 다음 액션 재작성  
- [x] 문서 버전 + 갱신일  
- [x] `apps/web/public/docs/IMPLEMENTATION_PLAN.md` 동기화  

---

## 16. 참고 산출물 맵

| 산출물 | 경로 |
|--------|------|
| 설계서 v2.3 | `docs/CI-TEC_Knowledge_Platform_Design.html` · 웹 `/docs/design.html` |
| 질문 100 분석 | `docs/Query_Catalog_100_Analysis.md` · 웹 `/docs/query-catalog-analysis.html` |
| **본 구현 계획** | **`docs/IMPLEMENTATION_PLAN.md`** · 웹 `/docs/IMPLEMENTATION_PLAN.md` · `/docs/implementation-plan.html` |
| 운영 README | `README.md` |
| Phase1 증거(개발) | `/tmp/grok-goal-b30c1f3bc333/implementer/` (`GOAL_COMPLETE.md`, `retrieval_eval.json`, …) |

### 로컬 접속 (dev)

| URL | 용도 |
|-----|------|
| http://localhost:8572/search.html | 통합 검색 |
| http://localhost:8572/chat.html | Fast/Deep QA |
| http://localhost:8572/si.html | 유사장애 |
| http://localhost:8572/tickets.html | 기간 지원건 |
| http://localhost:8572/analytics.html | 집계 |
| http://localhost:8572/capacity.html | 공수·대수 |
| http://localhost:8572/bundles.html | 번들 |
| http://localhost:8572/insights.html | Insight 승인 |
| http://localhost:8572/login.html | Login / SSO |
| http://localhost:8572/docs/ | 설계·구현 문서 |
| http://localhost:8573/v1/health | API health |
| http://localhost:8573/docs | Swagger |

---

**문서 끝 (v1.20).**  
mock IdP e2e + OIDC + load/SLA + Insight · 잔여=도메인 사인·실 IdP·원격 push · **매 작업 현행화**.
