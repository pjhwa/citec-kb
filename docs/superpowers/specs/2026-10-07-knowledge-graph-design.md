# citec-kb 지식그래프(Knowledge Graph) 설계 — 스키마 + 백필

- 작성일: 2026-10-07
- 배경: citec-kb는 현재 PostgreSQL FTS + pgvector 하이브리드 검색만으로 동작하며, 문서 간
  명시적 관계(계층, 컴포넌트, 근거, 유사도)를 질의할 방법이 없다. 이 문서는 전체 코퍼스
  (10개 `source_type`, 70,322건, 2026-10-07 기준)를 대상으로 지식그래프를 구축하는 1단계
  작업 — **스키마 설계와 백필 파이프라인** — 의 범위를 정의한다. 실시간 조회 API/MCP 도구
  노출은 이 문서의 범위 밖이며, 그래프가 안정화된 뒤 별도 설계로 진행한다.
- **이 문서를 작업할 개발환경은 이 문서가 관찰한 리포지토리 리비전과 다를 수 있다.** 아래
  "현재 상태" 절의 수치·파일 경로는 전부 재확인 후 다음 절의 변경을 적용할 것.

---

## 0. 결론 먼저

**Postgres를 source of truth로 유지하고, Neo4j를 파생(derived) 저장소로 추가한다.** 실시간
쓰기 경로는 만들지 않고, 배치 스크립트 하나(`scripts/graph_sync.py`)가 Postgres를 읽어
Neo4j에 멱등하게 반영한다. 노드는 4종(`Document`/`Component`/`BusinessEntity`/
`FailureBucket`)으로 제한하고, 카디널리티가 낮은 분류값(`environment`, `fb_domain`,
`citec_domains`, `severity_tier`)은 노드로 만들지 않고 `Document` 속성으로만 둔다 — 노드로
만들면 수천~수만 개 문서가 몰리는 허브 노드가 생겨 순회(최단경로/explain) 결과가 의미
없어진다.

별도 feature 브랜치에서 진행 가능하며, 기존 테이블·검색·MCP 도구는 전혀 건드리지 않는
**순수 추가(additive)** 작업이다. 기존 걸 허물고 재구축할 이유가 없다.

---

## 1. 현재 상태 관찰 (재확인 필수)

| 지점 | 위치 | 현재 동작 |
|---|---|---|
| 코퍼스 규모 | `documents` 테이블 | 70,322건. `confluence_map` 34,173 / `incident_reports` 15,349 / `checkitem` 8,989 / `confluence_docs` 5,509 / `tech_repo` 2,800 / `support_history` 2,365 / `dept_archive` 1,111 / `tuning_ai` 15 / `failure_bucket` 8 / `insight` 3 |
| 본문 비대칭 | `documents.body_md` | `confluence_map`은 **전 건이 breadcrumb만** 보유(평균 147자) — 운영서버엔 실제 본문이 있으나 현재 개발 DB엔 없음. `confluence_docs` 450건, `tech_repo` 84건도 본문 비어있음 |
| 구조화 필드(티켓) | `apps/api/app/db/models.py:320` (`IssueFrame`) | 17,632건. `symptom`/`root_cause`/`resolution`/`components[]`/`environment`/`citec_domains[]`/`severity_tier`/`body_hash`/`extractor_version` 보유 — 재추출 staleness 패턴 이미 존재 |
| 구조화 필드(체크아이템) | `apps/api/app/db/models.py:222` (`Checkitem`) | 8,989건. `area`/`category`/`category_1`/`subcategory` 보유 |
| failure_bucket 플라이휠 | `apps/api/app/failure_buckets/service.py` | `create_bucket`→`_index_bucket`→`embed_pending_chunks`, `refine_bucket`의 `signals_changed`/`environment_changed` 가드로 재인덱싱 스킵. `match_buckets()`가 이미 유사도 스코어 계산(`_DUPLICATE_SCORE_THRESHOLD=0.75`) |
| 동의어 사전 | `apps/api/app/db/models.py:303` (`LexiconTerm`) | `canonical`/`variants[]`/`priority` — 본문 기반 컴포넌트 매칭에 재사용 가능 |
| 비즈니스 엔티티 | `apps/api/app/db/models.py:258,286` (`Entity`/`DocumentEntity`) | 5건/549건뿐 — 구조만 있고 거의 비어있음 |
| Confluence 계층 | `apps/api/app/confluence/sync.py:137,163,216` / `map_sync.py:258-260` / `apps/api/app/ingest/adapters.py:43,232-237,294-298,351-355` | `ancestors` API 응답 → `Document.metadata_["ancestor_ids"]`. `confluence_map`/`confluence_docs`/`tech_repo` 3개 source_type 모두 채워짐. **`content_hash` 계산에서 `ancestor_ids`는 의도적으로 제외됨**(`adapters.py:43-47`, 페이지 이동 시 불필요한 재청크 방지) |
| 인프라 포트 | `docker-compose.yml` | 조직 할당 `8572–8580` 중 `8572`(web)/`8573`(api)/`8574`(postgres)/`8575`(redis)/`8576`(keycloak)/`8577`(mcp) 사용 중. `8578`/`8579`/`8580` 미사용 |
| 에어갭 패키징 | `scripts/out.sh:92` (`CORE_IMAGES`) | api/worker/nginx/redis/pg 이미지만 번들. Neo4j 추가 시 여기 포함 필요 |

---

## 2. 저장소 선택

**Neo4j** (FalkorDB 대안 검토했으나 기각 — GDS 라이브러리로 커뮤니티 탐지/최단경로를 직접
구현 없이 쓸 수 있고, 생태계·Python 드라이버·Docker 이미지가 가장 성숙).

- `docker-compose.yml`에 서비스 추가: 포트 `8578:7474`(브라우저), `8579:7687`(Bolt).
- `scripts/out.sh`의 `CORE_IMAGES`/`scripts/in.sh`에 Neo4j 이미지 추가 — 에어갭 번들에
  실제로 포함되는 변경이라 이번 작업 범위에 포함한다.
- Postgres에 조회용 FK를 만들지 않는다. Neo4j는 전량 Postgres에서 재생성 가능한 파생
  데이터이므로 유실돼도 복구 가능 — 이게 "Neo4j를 지워도 안전하다"는 보장의 근거다.

---

## 3. 스키마

### 3.1 Postgres 쪽 추가분 (마이그레이션 1개)

```
graph_sync_state (NEW)
├ document_id          str PK, FK documents.id ON DELETE CASCADE
├ input_hash            str(64)   -- sha256(content_hash + ancestor_ids + components
│                                     + evidence_ref + area/category* + …)
├ graph_extractor_version  str(32)
├ synced_at             timestamptz
└ last_error            text NULL   -- 마지막 실패 사유 (성공 시 NULL로 초기화)
```

기존 테이블은 **스키마 변경 없음**. `issue_frames`/`failure_buckets`/`checkitems`/
`entities`/`document_entities`/`lexicon_terms`를 읽기 전용으로만 참조한다.

**`input_hash` 설계 주의점**: 기존 `content_hash`는 `ancestor_ids`를 의도적으로 제외한다
(§1). 그래프용 `input_hash`는 **반대로 반드시 포함**해야 한다 — 안 그러면 Confluence
페이지 이동 시 `PARENT_OF` 엣지가 갱신되지 않는다. 즉 `content_hash`를 그대로 재사용하지
않고 별도 계산한다.

### 3.2 Neo4j 쪽 노드 (4종)

| 레이블 | 소스 | 핵심 속성 |
|---|---|---|
| `(:Document)` | `documents` 전 건 | `id`(=Postgres id), `source_type`, `external_id`, `path`, `web_url`, `environment`, `fb_domain`, `severity_tier`, `citec_domains`(배열 속성) |
| `(:Component)` | `issue_frames.components[]` + `checkitems.area/category*` + `lexicon_terms` 매칭 | `canonical_name` (lexicon 통해 정규화) |
| `(:BusinessEntity)` | `entities`/`document_entities` 미러 | `id`, `canonical_name`, `type` |
| `(:FailureBucket)` | `failure_buckets` | `Document`와 별도 레이블 — 필드가 풍부해 전용 유지 |

`environment`/`fb_domain`/`citec_domains`/`severity_tier`는 노드로 만들지 않는다(§0 근거).

### 3.3 Neo4j 쪽 엣지

| 관계 | 방향 | 태그 | 소스 | 비고 |
|---|---|---|---|---|
| `PARENT_OF` | Document→Document | EXTRACTED | `metadata_["ancestor_ids"]` | confluence_map+confluence_docs+tech_repo 42,482건 커버, 본문 불필요 |
| `HAS_COMPONENT` | Document→Component | EXTRACTED | `issue_frames.components[]` / `checkitems.area*` | 구조화 필드, 즉시 가능 |
| `HAS_COMPONENT` | Document→Component | INFERRED | 본문 + `lexicon_terms` 매칭 | tech_repo/confluence_docs/dept_archive 본문 있는 문서만 |
| `MENTIONS_ENTITY` | Document→BusinessEntity | EXTRACTED | 기존 `document_entities` | 그대로 미러링 |
| `HAS_EVIDENCE` | FailureBucket→Document | EXTRACTED | `evidence_ref` 접두어 파싱(`citects-`/`confluence:`/`capture:`/…) | 8건, 전수 처리 |
| `SIMILAR_TO` | FailureBucket↔FailureBucket | INFERRED | 기존 `match_buckets()` 점수 ≥ 0.75 | 8건, 전수 계산 가능 |
| `REFERENCES` | Document→Document | EXTRACTED | 본문 내 `CITECTS-\d+` 패턴 / 명시적 링크 | 본문 없는 문서(현재 confluence_map 전부)는 자동으로 빈 결과 — 정상 동작 |

---

## 4. 백필 파이프라인

### 4.1 스크립트 구조 (`scripts/graph_sync.py`)

기존 `map_backfill` 류 스크립트와 동일한 CLI 관례(`--source-ids`, `--dry-run`, 배치 크기
옵션)를 따른다.

```
for source_type in [confluence_map, incident_reports, checkitem, confluence_docs,
                     tech_repo, support_history, dept_archive, tuning_ai,
                     failure_bucket, insight]:
    for batch in documents.where(source_type=...).batched(size=500):
        for doc in batch:
            input_hash = compute_graph_hash(doc)
            state = graph_sync_state.get(doc.id)
            if state and state.input_hash == input_hash:
                continue                      # 변경 없음 — 스킵
            edges = extract_edges(doc)        # §4.2 추출기 체인, 문서 단위 격리
            neo4j.merge(doc, edges)            # MERGE — 멱등
            graph_sync_state.upsert(doc.id, input_hash)
    prune_orphans(source_type)                # Postgres에서 삭제된 id의 Neo4j 노드 정리
```

### 4.2 추출기 체인 (독립 함수, 하나 실패해도 나머지 안 막힘)

1. `extract_hierarchy(doc)` — `ancestor_ids` → `PARENT_OF`
2. `extract_structured_components(doc)` — `issue_frames.components[]` 또는
   `checkitems.area/category*` → `HAS_COMPONENT`(EXTRACTED)
3. `extract_business_entities(doc)` — `document_entities` 미러 → `MENTIONS_ENTITY`
4. `extract_evidence(doc)` — `failure_buckets.evidence_ref` 파싱 → `HAS_EVIDENCE`
5. `extract_lexicon_components(doc)` — 본문 있는 문서만, `lexicon_terms` 매칭 →
   `HAS_COMPONENT`(INFERRED)
6. `extract_references(doc)` — 본문 내 티켓ID/링크 매칭 → `REFERENCES`

각 함수는 "Document 입력 → 엣지 목록 출력"의 순수 함수로, Neo4j 연결 없이 단위 테스트
가능해야 한다.

### 4.3 dev→운영 비대칭 처리

운영에서 `confluence_map` 본문이 채워지면 `content_hash`가 바뀌고 → `input_hash`도
바뀌므로 → 같은 백필 스크립트를 운영에서 재실행하면 5·6번 추출기가 자동 재실행된다.
별도 마이그레이션 스크립트나 "운영 전용 처리"는 만들지 않는다.

### 4.4 멱등성/에러 처리

- 문서 단위 격리: 실패 시 `graph_sync_state.last_error`만 기록하고 다음 문서로 진행,
  `input_hash`는 갱신하지 않아 다음 실행에서 자동 재시도 대상이 된다.
- `neo4j.merge()`와 `graph_sync_state.upsert()`는 같은 단위로 묶어 둘 다 성공해야
  다음 문서로 진행 — 부분 성공이 "성공"으로 기록되는 것을 막는다.
- MERGE 키는 `Document.id = documents.id` 그대로 — 새 UUID 생성 안 함.
- 참조 대상이 아직 동기화 안 된 문서일 때: 최소 스텁 노드(`id`만 채움)를 먼저 MERGE하고,
  나중에 실제 동기화 시 같은 키로 속성이 채워지도록 한다 — 순서 의존성 제거.
- Neo4j 연결 장애 시 배치 전체 중단(해당 배치는 커밋 안 됨).

---

## 5. 테스트 전략

- 추출기 단위 테스트: 6개 함수 각각 Neo4j 없이 순수 함수로 검증.
- 멱등성 테스트: 같은 문서 2회 동기화 → 2차 실행에서 추출 자체가 스킵되는지.
- 해시 민감도 회귀 테스트: `ancestor_ids`만 바뀐 입력에서 `content_hash`는 그대로인데
  `input_hash`는 바뀌는지 — 가장 틀리기 쉬운 지점.
- 통합 테스트: CI용 경량 Neo4j 컨테이너 + source_type별 5~10건 샘플로 전체 파이프라인
  1회 실행, 노드/엣지 수 검증.
- dev/prod 비대칭 회귀 테스트: breadcrumb만 있는 confluence_map 샘플에서
  `extract_references`가 빈 결과로 정상 종료하는지, 본문이 채워진 fixture를 별도로
  추가해 같은 함수가 정상 동작하는지.

---

## 6. 범위 밖 (2단계 이후)

- 실시간 조회 API/MCP 도구(`kb_graph_path` 등) 노출
- failure_bucket 플라이휠(`create_bucket`/`refine_bucket`) 훅에 그래프 갱신 연동
- `citec_domains`의 노드 승격 여부 재검토(실사용 패턴 확인 후)
- 티켓-티켓 임베딩 기반 `SIMILAR_TO` 확장(현재는 failure_bucket 간만)
- LLM 기반 의미 추출 보강(로컬 lexicon 매칭 커버리지 부족 시)
