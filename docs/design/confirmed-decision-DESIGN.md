# 사람 확정 페이지(Confirmed Decision) — Phase 0 설계

작성일 2026-11-05 · 지시문 `CITEC_KB_CONFIRMED_DECISION_PAGE_PROMPT_20261105.md` · 상태: **승인 대기 (코드·마이그레이션 변경 없음)**
목업: `docs/design/confirmed-decision.mock.html` (서버 없이 파일로 열기)

---

## 1. §2 사실의 코드 검증

| # | 지시문의 사실 | 실제 코드 | 판정 |
|---|---|---|---|
| 1 | promote한 insight 문서는 `evidence_grade="draft"` | `insights/service.py:128` `_insight_draft` → `evidence_grade="draft"`, `external_id=INSIGHT-<id[:8]>`, `source_uri=insight://<id>` | 일치 |
| 2 | 검색 순위는 등급을 보지 않는다. 유사 신호는 confluence_map 포인터 하향 하나 | `retrieval/search.py:443` `_pointer_factor()`(env `SEARCH_POINTER_FACTOR`, 기본 0.85), `:971` 이후 적용. `source_type=="confluence_map"`만 대상. `evidence_grade`를 점수에 쓰는 곳 없음 (`:897`은 SELECT만) | 일치 |
| 3 | insight `approved`는 종결 | `TRANSITIONS["approved"] = set()` (`service.py:18`) | 일치 |
| 4 | insight는 제목+자유 본문만 | `db/models.py:572` `Insight`: title, body_md, source_doc_ids(문자열 배열), status… | 일치 |
| 5 | `AUTH_MODE=off` → 전체 역할 | `auth/principal.py` `ANON_OPEN`(roles=ALL_ROLES, `auth_via="anonymous"`) | 일치. `Principal.auth_via`가 이미 있어 `decision_verdicts.auth_via`에 그대로 쓴다 |
| 6 | failure bucket에 Document 미러+해시 안정 | `failure_buckets/draft.py` `bucket_draft`: confidence 등 의미 외 필드를 본문에서 제외, `source_uri=failure_bucket://<id>`, `evidence_grade="machine"` | 일치 |
| 7 | Document에 `content_hash`, `version` | `models.py:83-84` | 일치 |
| 8 | 원문 핸들 `path/body_api/web_url` | `doc_access.py` `document_access()` (+ `source_url`) | 일치 |
| 추가 | 등급을 읽는 곳 | `graph/identity.py:17` `GRADE_RANK`(A,A-,B,C,machine,draft) / `graph/explore.py:16` `_GRADE_ORDER = GRADE_RANK` / `graph/pipeline.py`·`routers/graph.py`(전달만) / `routers/search.py:120`·`external_compat.py:675`(응답 노출) / `ingest/pipeline.py:178,213`(쓰기). **트러스트 엔진(`trust/engine.py`)은 등급을 읽지 않고 `evidence_eligible`만 쓴다** | 지시문 §6-1의 "트러스트 엔진이 등급을 읽는다"는 **불일치** — 등급→신뢰 연결은 새로 만들어야 한다 |
| 추가 | `GRADE_RANK`에 없는 등급은 rank 3(`explore.py:32`)/99(`identity.py:39`)로 떨어진다 | 새 등급 `H`를 추가하지 않으면 확정 문서가 `C`보다 낮게 취급될 수 있음 | C단계에서 `H=-1`(최우선) 추가 |

### 1-1. A1 — 세 숫자의 정체 (코드로 확정)

| 표시값 | 실제로 세는 것 | 위치 |
|---|---|---|
| `kb_stats insights=23` | `insights` 테이블 **전 상태** 행 수 (draft·review·rejected 포함) | `external_compat.py:557` `select(count()).select_from(Insight)` |
| `by_source_type insight: 3` | `documents` 중 `status='active'` 이고 `source_type='insight'` — 즉 **promote된 문서** 수 | `external_compat.py:544-549` |
| `kb_list_insights` 6건 | 기본 `status="approved"` 필터가 걸린 insight 행 | `external_compat.py:842` `status: str = "approved"` |

**(개정 지시문 사실 11로 확정: 결함이 아니라 정의 차이. A1은 없다.)** 세 값은 서로 다른 것을 센다(23 전체 / 6 승인 / 3 승인+promote된 활성 문서). 결함은 **이름 없이 같은 "insight"로 노출한 것**. 폐기로 소멸. 새 `kb_stats.decisions`는 `{"by_status": {...}, "searchable_active_documents": n, "definition": "..."}` 구조로 **세 값을 이름을 붙여 분리**하고 정의를 응답에 넣는다.

### 1-2. A2 — `kb_ask` 인용 번호가 출처 목록에 없는 원인 (개정 지시문 사실 12 확인 · 앞선 초안의 진단은 틀렸음)

**정정:** 이 문서의 이전 판은 원인을 "LLM이 출처 밖 번호를 지어냄(`_finalize`가 본문을 거르지 않음)"으로 적었다. 개정 지시문과 `mcp-server/ask_fold.py`를 대조해 **틀린 진단**임을 확인했다.

- 실제 원인: `fold_ask_events`가 `citations`를 `path`(문서) 단위로 `seen_paths`에서 중복 제거하고 **문서의 첫 청크 번호 하나만** 출력한다. 같은 문서의 다른 청크를 가리키는 `[C11]`, `[C15]`는 `rag/pipeline.py`에서 정상 부여된 번호인데 출처 목록에는 나타나지 않는다. 환각이 아니라 표시 계층 결함이다. `rag/pipeline.py`는 `used_cites`를 `packed` 안으로 이미 제한하므로 수정 불필요.
- 수정안: `ask_fold.py`에서 path별로 해당 문서를 가리키는 **모든 인용 번호를 병기**(`C1, C11, C15`). 출력 형식은 `- C1, C11, C15 <title>` 한 줄 — 스킬이 `- C# …` 줄을 파싱하는지는 A 착수 시 확인한다(아직 미확인). 회귀 시험은 `mcp-server/test_ask_fold.py`에 추가(동일 path 3청크 → 출처 한 줄에 3번호, 답변 본문의 모든 `[C#]`가 출처 목록에 존재).
- 불변식 시험 추가: 답변 본문의 `[C\d+]` 집합 ⊂ 출처 목록 번호 집합.

---

## 2. insight 폐기 계획 (§2-1) — 인벤토리 (코드 확인 완료)

| 구분 | 대상 | 파일 |
|---|---|---|
| DB | `insights` 테이블, `feedback.target_type='insight'` 행 | `db/models.py:572`, `routers/insights.py` `FeedbackBody`, `external_compat.py:944-947` |
| API 라우터 | `/v1/insights*`(생성·조회·patch·submit·approve·reject·reopen·reindex), `/v1/feedback`(insight 타깃 부분) | `routers/insights.py`, `main.py:39,119` |
| 서비스 | `insights/` 패키지 전체 | `insights/service.py`, `insights/__init__.py` |
| wiki-qa 호환 | `GET /api/synthesis`, `/api/synthesis/{slug}`, `/api/wiki-stats`의 `synthesis`·`insights` 필드, 템플릿 별칭 `synthesis` | `external_compat.py:77-79, 93, 450-451, 557-570, 839-885, 943-947, 1015-1016` |
| 작업 | `insight_reindex`, `embed_document`의 `insight_id` 분기 | `jobs/queue.py:30`, `apps/worker/app/main.py:90-114` |
| 경로 별칭 | `_PATH_SOURCE_ALIASES`(insight 항목) | `external_compat.py:447` |
| 검색 | `source_type=insight` + 별칭 `insights`·`synthesis` | `retrieval/filter_args.py` (`KNOWN_SOURCE_TYPES`, `SOURCE_TYPE_ALIASES`) |
| MCP | `kb_list_insights`, `kb_get_insight`, `wiki_list_synthesis`, `wiki_get_synthesis`, 도움말(`kb_tools_help`) 문구 `[티켓·Insight·상태]`, `kb_stats` 출력 `insights=` | `mcp-server/server.py:463-520, 540, 1628-1630, 2217`, `mcp-server/test_smoke.py:54` |
| UI | `insights.html`, 내비 항목, 홈·Admin 링크 | `apps/web/public/insights.html`, `js/nav.js:45`, `index.html:226`, `admin.html:702` |
| 권한 안내 | `/v1/auth` 엔드포인트 요약 문구 | `routers/auth.py:62-64` |
| 테스트 | `tests/test_insights_unit.py`, `test_jobs_unit.py:7`, `test_external_compat.py:24,71`, `test_mock_idp_e2e.py`(insight 부분), `mcp-server/test_smoke.py`의 insight 부분 | |
| 문서 | `AI_AGENT_GUIDE.md`, `MCP.md`, `EXTERNAL_API.md`, `CITEC_KB_MCP_SKILL.md`, `AUTH_ENABLEMENT.md`, `DEPLOY.md`, `scripts/pilot_domain_signoff.py:214`("Insight 승인 플로우 시니어 리뷰"), `docs/` 하위 HTML 재생성 | |
| **이름만 같고 폐기 대상 아님** | `insight-ops@v…`(시스템 등록자 이름), failure bucket 근거 접두사 `insight:`(`failure_buckets/service.py:247-279`) | 건드리지 않는다. 단 `insight:` 접두사는 폐기 후 의미가 사라지므로 **열린 질문 Q9** |

**G 단계 순서(지시문 §2-1 그대로, 구체화):** ① 읽기 전용으로 23건 전체(approved 6·draft 10·review 6·rejected 1) 제목·본문 확인·보고(모두 시험용 제목이라는 지시문 관측 포함) → ② MCP·HTTP를 안내 스텁으로 교체(`kb_list_insights`/`kb_get_insight`/`wiki_*_synthesis`는 "폐기됨, `kb_list_decisions`/`kb_get_decision` 사용" 문자열 반환, `/v1/insights*`·`/api/synthesis*`는 410 + 같은 안내) → ③ 필터 별칭 제거(알 수 없는 값은 기존 400+허용 목록) → ④ 작업·UI·서비스·라우터·테스트·`kb_stats` 필드 제거 → ⑤ 마이그레이션 `0022`: insight 문서를 `archived`로 돌린 뒤 청크·임베딩·그래프 행 정리, `insights` 테이블 삭제, `downgrade`는 빈 테이블과 컬럼 복원(데이터 복구는 불가 — 백업 필요) → ⑥ 운영 적용 절차 문서(영향·순서·롤백). 운영 직접 적용 없음.

**운영 적용 절차 초안(G6에서 별도 문서화):** (1) 백업 `scripts/backup_postgres.sh` (2) 읽기 전용으로 insight 문서·청크 수 기록 (3) 승인 후 `alembic upgrade` (4) `kb_stats`·검색 스모크 (5) 롤백 = `alembic downgrade -1` + 백업에서 insights 행 복원. 영향: 3개 검색 문서 제거, `/api/synthesis` 클라이언트(wiki-qa 호환) 410.

**영향 받는 스킬:** `citec-mcp-workbench`(`kb_list_insights`/`kb_get_insight`), wiki-qa 호환 클라이언트. 스텁 한 릴리스 동안 깨지지 않는다. 패치 제안은 지시문 §11 표를 그대로 보고서에 싣는다.

---

## 3. 최종 데이터 모델

지시문 §3-2안을 채택하되 아래를 확정/보강한다. (`insights`와 무관.)

### 3-1. `decisions`
`id`(uuid) · `question`(text, ≤300자) · `options`(JSONB `[{key,label,summary}]`, 예약 `NEI`·`OTHER`는 서버가 자동 삽입) · `topic` · `domain` · `stakes`(low|mid|high) · `status`(proposed|confirmed|needs_recheck|superseded|retired) · `status_before_retire`(restore용) · `current_verdict_id`(FK→verdicts, nullable) · `superseded_by`(FK self) · `review_interval_days`(기본 180, null=무기한) · `created_by_kind`(mcp|server_job|human) · `created_by` · `created_at`/`updated_at`.
**보강:** `ai_recommendation_key`는 판정이 아니라 **제안 레코드**의 속성이므로 `decisions`에 둔다(판정 행은 사본 `ai_recommendation_key`만 스냅샷). 인덱스 `(status)`, `(topic)`, `(domain)`.

### 3-2. `decision_evidence`
지시문 필드 그대로. 추가: `UNIQUE(decision_id, option_key, document_id, pinned_content_hash, quote_hash)`(중복 근거 방지), `quote_hash`. 내부 근거는 `document_id` FK, 외부는 `origin='external'`이며 `document_id`는 null. 근거는 **삭제하지 않는다**(잘못 달았으면 `stance='context'` 새 행이 아니라 `retracted_at` 표시 — **열린 질문 Q8**; 기본안: 근거 수정은 retracted 표시 + 새 행).

### 3-3. `decision_verdicts` (추가만 가능)
지시문 필드 그대로 + `decided_via`(`ui`) + `nonce_id`. **DB 수준 강제:** `BEFORE UPDATE OR DELETE` 트리거가 예외를 던진다(앱 계층 규칙만으로는 우회 가능). 마이그레이션 `downgrade`만 트리거를 제거한다. 시험: 직접 SQL UPDATE/DELETE도 실패.

### 3-4. 검색 미러
Document `source_type="confirmed_decision"`, `external_id="DEC-<id[:8]>"`, `source_uri="decision://<id>"`, `evidence_grade="H"`, `status` = `active`(confirmed·needs_recheck) / `superseded`·`archived`(retired) — `Document.status`는 이미 `superseded` 허용. 본문(해시 대상): `# 질문` / `결론: <선택지 라벨>` / `확정일` / `확정자(자기 신고)` 또는 `확정자` / `변경 이유(rationale)` / `이전 기준 링크` / `근거 path 목록` / `선택하지 않은 선택지와 이유`.
**해시 안정:** `needs_recheck` 여부, `review_interval_days`, 지표, 큐 점수, `time_to_decide_ms`는 본문에서 제외 → 상태 변화가 재임베딩을 일으키지 않는다. `needs_recheck`는 본문이 아니라 `Document.metadata`(해시 제외 키)에 둔다. **주의:** 확정자가 바뀌지 않는 "유지" 판정은 `확정일` 문구가 바뀌므로 해시가 바뀐다 → `확정일`은 `effective_from` 기준 최초 확정일이 아니라 **최신 판정일**로 표시하되, 이것은 의미 변화로 보고 재임베딩을 허용한다(청크 1~2개, 비용 미미). 대안은 Q7.

---

## 4. 상태 전이

```
proposed ──verdict──▶ confirmed ──(근거 hash 변경 | review_interval 경과)──▶ needs_recheck
needs_recheck ──verdict(유지/변경)──▶ confirmed
confirmed | needs_recheck ──supersede(새 결정)──▶ superseded
any ──retire──▶ retired ──restore──▶ status_before_retire
```
- `proposed` 상태 판정이 `NEI`면 `confirmed`가 아니라 **`proposed` 유지 + 판정 이력 기록**(NEI는 "아직 모른다"이므로 기준이 되지 않는다). 검색 미러도 만들지 않는다. **열린 질문 Q6.**
- "유지" 버튼 = 같은 `option_key`로 새 판정 행(`supersedes_verdict_id` = 이전), `evidence_snapshot` 갱신, `pinned_*`를 현재 버전으로 재고정(재고정 근거는 `decision_evidence` 새 행, 이전 행은 보존).
- `superseded`/`retired`는 검색 기본 제외, `include_superseded=true`(추가 인자, 기본 false)로 포함.

## 5. 서버 강제 규칙

1. **인용 검증:** `quote`를 NFC 정규화 + 공백 런 1칸 축약(양끝 trim) 한 문자열이 같은 정규화를 거친 `pinned_version` 본문에 **부분 문자열**로 존재해야 한다. 고정 버전 본문은 `Document.version == pinned_version` 일 때만 직접 조회 가능(버전 본문 이력은 저장하지 않음) → 현재 `content_hash == pinned_content_hash`인 경우에만 검증하고, 해시가 이미 달라졌으면 400 "현재 버전을 다시 고정하라". 존재하지 않는 `document_id` 400. 시험: NFD 한글 입력 통과, 존재하지 않는 인용 400.
2. **외부 근거:** 형식(URL 스킴 http/https, snapshot 길이 상한 4,000자, `retrieved_at` ISO)만 검증. 서버는 외부 호출하지 않는다.
3. **낡음 감지:** 배치 `decision_staleness_scan`(jobs 큐의 허용 타입에 추가, 기존 `jobs/queue.py` 패턴) + 조회 시 지연 계산(`GET /v1/decisions*`에서 고정 해시 ≠ 현재 해시이면 응답 `effective_status="needs_recheck"`로 표시하고 저장은 스캔이 한다).
4. **확정자 기록:** `auth_via=anonymous`이면 `decided_by`는 입력값(자기 신고). 인증이 켜지면 `principal.name`으로 덮어쓰고 `senior|admin`만.
5. **AI 판정 불가:** `POST …/verdict`는 UI가 `GET /v1/decisions/{id}/nonce`로 받은 **단회·60초·decision 한정** nonce를 요구하고 `decided_via=ui`. MCP에 판정 도구 없음. **한계(문서에 명시):** `AUTH_MODE=off`에서는 curl로 nonce를 받아 판정할 수 있다 → 방지책이 아니라 "의도 없는 AI 판정 방지"일 뿐. 근본 해결은 인증 ON (Q2).

## 6. API 스펙 (지시문 §5 + 확정)

| 엔드포인트 | 권한 | 비고 |
|---|---|---|
| `POST /v1/decisions` | author+ | 선택지 린트: ① `NEI`/`OTHER` 키 사용자 정의 금지 ② key 중복 ③ 라벨 정규화 후 중복 ④ 선택지 2~8개 ⑤ 질문에 "및/그리고/and" 등 복합 접속 의심 시 **경고만**(차단 아님, `warnings[]`) |
| `POST /v1/decisions/{id}/evidence` | author+ | §5-1,2 게이트 |
| `GET /v1/decisions` | 공개 | `status`,`topic`,`domain`,`sort=priority|recent`,`include_superseded`,`limit/offset` |
| `GET /v1/decisions/{id}` | 공개 | 카드 페이로드 |
| `GET /v1/decisions/{id}/nonce` | senior+ | 단회 nonce |
| `POST /v1/decisions/{id}/verdict` | senior+ | 근거 0건이면 400(단 `NEI`는 허용). 외부 근거만이면 `external_only_confirm=true` 필요 |
| `POST /v1/decisions/{id}/supersede` | senior+ | 새 decision id를 받는다(없으면 본문으로 생성) |
| `POST /v1/decisions/{id}/retire`, `/restore` | senior+ | |
| `GET /v1/decisions/metrics` | 공개 | §3-6 지표 |

**충돌 규칙(코드·테스트로 명시):** 충돌 = (a) 서로 다른 두 선택지에 `supports` 근거가 각각 ≥1건, 또는 (b) supports 최다 선택지에 `refutes` ≥1건. 근거 `retracted`는 제외.
**큐 점수:** `score = w_c·conflict + w_s·stakes_weight + w_a·min(age_days/review_interval, 2) + w_ch·min(changed_pins, 3)`, 가중치는 `config/decision_queue.json`(없으면 기본값 1.0/1.0/0.5/0.5, 운영값은 측정 후 조정). 카드에 "충돌 + 위험 높음 + 변경 2건" 한 줄.

## 7. 등급·순위·신뢰 소비처 (C단계)

| 소비처 | 변경 |
|---|---|
| `graph/identity.py:17` `GRADE_RANK` | `"H": -1` 추가 (최우선 대표 선택) |
| `graph/explore.py:16` | `GRADE_RANK` 공유라 자동 반영 |
| `retrieval/search.py` | 사후 점수 단계에 **일반 규칙**: `source_type=="confirmed_decision"` ∧ `Document.status=="active"` ∧ 미낡음 → 점수 × `SEARCH_CONFIRMED_FACTOR`(기본 1.0=꺼짐, 평가로 값을 정한다). `needs_recheck`는 올리지 않고 `hit.recheck=True`. 필터 기본 `status='active'`(`:508`)가 `superseded`·`retired` 제외를 이미 처리 → `include_superseded=true`는 `status in (active,superseded)`로 확장 |
| `retrieval/filter_args.py` | `KNOWN_SOURCE_TYPES += confirmed_decision`; 별칭 `decision`,`decisions`,`confirmed`; (G단계에서 `insight` 제거) |
| `rag/pipeline.py`/`packer.py` | 확정 문서 청크를 컨텍스트 최상단에 배치, 컨텍스트 블록에 `[확정 DEC-xxxx 확정자·확정일·재확인 여부]` 태그; 프롬프트에 "확정 출처가 있으면 먼저 인용하고 확정자·확정일을 적어라"; A2 불변식 |
| `trust/engine.py` | 등급을 읽지 않으므로 **`evidence_eligible=True`가 기본** → `H` 추가 자체로는 변화 없음. `needs_recheck` 인용 시 `reasons`에 "재확인 필요" 추가 |
| `routers/search.py:120`, `external_compat.py:675` | `evidence_grade:"H"` 그대로 노출 + `decision_status`/`recheck` 필드(검색 응답에 추가) |
| `docs/AI_AGENT_GUIDE.md §6.1` | `H` 행 추가 |
| 평가 | `scripts/verify/eval_retrieval_gate.py`로 전후 측정. 기존 지표 후퇴 시 머지 금지. 기준선은 확정 문서가 0건이면 순위에 영향이 없어야 한다 → **무영향 증명이 첫 게이트**(확정 문서 없는 상태에서 전후 동일) |

## 8. 마이그레이션 계획

- `0021_decisions`: 세 테이블 + 인덱스 + `decision_verdicts` 불변 트리거 + `downgrade`(트리거·테이블 제거).
- `0022_drop_insights`(G단계): insight Document→`archived`→청크·임베딩·그래프 정리, `insights` 테이블 삭제, `feedback` insight 행 정리, `downgrade`.
- 최신 리비전은 `20261105_0020_api_access_log.py`(개정 지시문 사실 14는 `20261014_0016`이라 적었으나 이 저장소의 실제 head는 0020 — **차이**). 새 마이그레이션은 0021부터, 구현 시점의 head를 다시 확인한다.
- dev 왕복 시험: `scripts/scratch_db_test.sh` + `alembic upgrade head → downgrade -1 → upgrade head`.

## 9. 테스트 계획 (지시문 §9 대응)

| §9 | 시험 |
|---|---|
| 1,2 | 기준선 `kb_search("PIXEL 제공 기한 PISA Station")` 기록 → 판정 후 상위 3 (하드코딩 없음 — 점수 규칙은 source_type/status/등급만 봄. 코드에서 `PIXEL` 등 grep 시험 추가) |
| 3 | `kb_ask` 통합: 확정자·확정일 표기, 모든 `[C#]`⊂출처 목록 (LLM 스텁 단위 시험 + 실 LLM 스모크) |
| 4 | 평가 게이트 전후 |
| 5 | 인용 검증 단위(정상·부재·NFD·공백 변형·존재하지 않는 document_id) |
| 6 | 직접 SQL UPDATE/DELETE 실패 |
| 7 | `content_hash` 변경 → needs_recheck → 순위 상승 중지 → "유지" 1회 → confirmed |
| 8 | supersede 후 기본 검색 제외 / `include_superseded=true` 포함 |
| 9 | 목업·UI 점검: 판정까지 클릭 1회, 근거 같은 화면 |
| 10 | G단계 스텁·왕복 마이그레이션 |
추가: 선택지 린트, 충돌 규칙 표 기반 시험, nonce 단회성·만료, 외부 근거만 확인 플래그, `AUTH_MODE` on/off 양쪽 권한, 미러 해시 안정(메타만 바꾸면 재임베딩 없음).

## 10. 구현 순서·커밋 (승인 후)

A2 → B1 → B2 → C1 → C2 → D1 → D2 → D3 → E1 → E2 → F → G(1~6). 항목당 1커밋 `feat(decision): B1 …`, 버전 `YYYY-MM-DD.N`. 푸시·PR·머지 없음.

## 11. PIXEL 시범에 대한 주의 (dev 데이터)

`kb_search`/`kb_get_document`로 dev에서 pageId 80022224 존재 여부를 **F 직전에** 확인하고, 없으면 같은 구조의 시험 문서로 대체하며 보고한다(지시문 §9).

## 12. 열린 질문 (사용자 결정 필요)

**이미 결정됨(다시 묻지 않음):** insight 폐기, 인증은 시범 이후에 켠다.

개정 지시문 §10 (4건, 번호 정리됨): 권고안을 그대로 쓸지 확인 부탁드립니다.

| # | 결정 | 권고 |
|---|---|---|
| 1 | 누가 확정하나 | 주제별 큐레이터 지정 |
| 2 | 초안 작성 주체 | MCP 클라이언트로 시작, 서버 작업은 시범 뒤 |
| 3 | 맹검 모드 기본값 | 꺼짐 (측정 후 결정) |
| 4 | 외부 근거만으로 확정 | 경고 + 명시 확인 후 허용 |

설계 중 생긴, 내가 정하지 않은 사항:

| # | 질문 | 권고 |
|---|---|---|
| Q6 | `NEI` 판정은 기준이 되는가? | 아니오. `proposed` 유지 + 이력만 기록, 검색 미러 없음 |
| Q7 | "유지" 판정으로 판정일이 바뀌면 미러 해시가 바뀌어 재청크·재임베딩(1~2청크)이 일어난다. 허용? | 허용. 싫다면 본문에는 최초 확정일만 넣고 최신 확인일은 메타로 |
| Q8 | 잘못 단 근거 처리 | 삭제 없이 `retracted` 표시 + 새 근거 |
| Q9 | failure bucket 근거 접두사 `insight:`는 폐기 후 의미 상실 | `decision:` 추가, `insight:`는 과거 근거 보존용으로 허용 유지 |

## 13. 인증 전환 설계 (개정 §4-4, §9 #11)

- 라우트 의존성은 처음부터: 판정·대체·retire·restore·nonce = `require_roles("senior","admin")`, 제안·근거 추가 = `require_roles("author","senior","admin")`. `AUTH_MODE=off`에서는 통과, 켜면 익명 401·역할 부족 403(`auth/deps.py`).
- 서비스 계층 규칙: `principal.auth_via != "anonymous"`이면 요청 본문의 `decided_by`를 **무시**하고 `principal.name`/`auth_via`를 기록. `anonymous`이면 `decided_by` 본문 필수(없으면 422), 미러 본문에 "확정자(자기 신고)".
- 시험(§9 #11): `AUTH_MODE=apikey`/`oidc_stub`에서 익명 401, viewer 403, senior 통과, `decided_by` 위조 무시, `off` 복귀 시 자기 신고 동작. `tests/test_mock_idp_e2e.py` 패턴 사용.
- 환경 변수·`AUTH_MODE` 기본값은 건드리지 않는다.

## 14. 개정 지시문과 이 저장소의 차이 (확인 결과)

| 항목 | 지시문 | 실제 |
|---|---|---|
| Alembic head | `20261014_0016` | **`20261105_0020`** |
| AGENTS.md/CLAUDE.md | 없음 | 없음 (일치) |
| `ask_fold.py`, `test_ask_fold.py`, `test_mock_idp_e2e.py` | 존재 | 존재 (일치) |
| A2 원인 | 표시 계층 | 일치 (이전 초안 진단 정정) |
