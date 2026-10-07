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
`FailureBucket`)으로 제한하고, 카디널리티가 낮은 분류값(`environment`)은 노드로 만들지
않고 `Document` 속성으로만 둔다 — 노드로 만들면 수천~수만 개 문서가 몰리는 허브 노드가
생겨 순회(최단경로/explain) 결과가 의미 없어진다. (`fb_domain`/`citec_domains`/
`severity_tier`는 애초에 Document 컬럼이 아니라 각각 FailureBucket/IssueFrame에만
존재하므로 "Document 속성 vs 노드" 선택지 자체가 없다 — §3.2 참고.)

별도 feature 브랜치에서 진행 가능하며, 기존 테이블·검색·MCP 도구는 전혀 건드리지 않는
**순수 추가(additive)** 작업이다. 기존 걸 허물고 재구축할 이유가 없다.

**검증 메모(2026-10-07)**: 개발 DB 실측 결과 `ancestor_ids`/`body_hash`/`extractor_version`가
전부 미충전 상태였으나, 이는 개발 DB 스냅샷의 시차일 뿐 — 코드에 구현된 기능은 운영에
반드시 존재한다는 전제로 설계를 유지한다(§1 읽는 법). 반면 `lexicon_terms`(10건),
`evidence_ref`의 상당수가 Document로 해석 불가(capture:/log:/legacy:), `issue_frames`
구조화 필드 충전율이 50%대인 점은 환경 차이가 아니라 **실제 콘텐츠 현황**이라 1단계
범위·기대치에 반영했다(§1, §3.3, §6).

### 0.1 데이터 우선순위 (사용자 지정, 2026-10-07)

CI-TEC(우리 부서) 산출물이 최우선이고, 장애 정보가 그다음이며, 나머지는 "근거 자료"로
활용한다. `confluence_map`의 `space_key`를 확인해 3단계로 나눈다:

| 우선순위 | 소스 | 근거 |
|---|---|---|
| **1순위 — 부서 산출물** | `tech_repo`(2,800) + `confluence_docs`(5,509) + `confluence_map WHERE space_key IN ('LOOKIN','TechRepo')`(미중복분만, 아래) + `checkitems`(8,989, PISA 체크리스트) | `LOOKIN` 공간 8,228건 중 5,504건은 이미 `confluence_docs`와 `external_id` 중복(= confluence_docs가 LOOKIN의 커스팅 부분집합), **나머지 2,724건은 LOOKIN에만 있는 미중복 CI-TEC 문서**. `TechRepo` 공간 3,990건 중 2,798건은 `tech_repo`와 중복, **나머지 1,192건이 TechRepo에만 있는 미중복 CI-TEC 문서**. 즉 confluence_map에서 **총 3,916건**이 "아직 tech_repo/confluence_docs로 승격되지 않은 우리 부서 콘텐츠"다 |
| **2순위 — 장애 정보** | `incident_reports`(15,349, SWIM) | 장애 자체의 1차 기록. `issue_frames`의 `severity_tier`/`citec_domains` 충전이 이 소스에 집중(§1) |
| **3순위 — 근거 자료(타 공간/부서)** | `confluence_map WHERE space_key NOT IN ('LOOKIN','TechRepo')`(`ICLOUDUT` 15,701 / `CLDENG` 3,949 / `DFTRTS` 950 / `ServiceExcellenceTeam` 872 / `DevOps001` 186 / `Openstack101` 174 / `EMCloud` 120 / `SPC` 2 / `GUID` 1, 합계 21,765건) + `support_history`/`dept_archive`/`tuning_ai`/`insight` | 아키텍처 설계·운영 작업계획·장애 분석 등 **근거로 참고하되 1차 산출물은 아님** |

**이 우선순위가 그래프 설계에 미치는 영향**:
- `space_key`를 `(:Document)` 노드 속성으로 반드시 보존해야 한다(§3.2 수정) — 지금까지의
  설계엔 빠져 있었다. `source_type='confluence_map'`만으로는 1순위/3순위를 구분 못 한다.
- `evidence_grade`(A/B/C, §3.3)는 **답변 인용 등급**일 뿐 이 부서 우선순위와 다른 축이다 —
  confluence_map은 전부 C등급이지만 그중 LOOKIN/TechRepo 미중복분(3,916건)은 1순위다.
  둘을 혼동하지 않는다(기존 §3.3 각주와 일관).
- 백필 실행 순서(§4.1)는 `source_type` 단일 루프가 아니라, confluence_map을
  `space_key IN ('LOOKIN','TechRepo')`와 그 외로 **먼저 분할**해 1순위 그룹을
  `tech_repo`/`confluence_docs`/`checkitems`와 같은 배치에 포함한다.

**운영→개발 데이터 반입(선행 작업, 2026-10-07)**: 개발 서버는 Confluence/운영 네트워크와
완전히 분리돼 있어 ancestor_ids/본문을 직접 당겨올 수 없다(§1). 대신 기존
`scripts/sync_manifest.sh`/`sync_export.sh`/`sync_diff.py`/`sync_apply.sh`(운영→개발
incremental DB sync, USB 등 승인된 경로로 파일만 운반)에 `entities`/`document_entities`/
`lexicon_terms` 3테이블을 추가하고, `failure_buckets` INSERT가 `fb_domain`/`environment`/
`evidence_ref`를 빠뜨리던 기존 버그(멀티플러그인 확장 이후 미반영분)를 고쳐 이 그래프
설계가 쓰는 9개 테이블 전체를 실어 나를 수 있게 했다. 운영에서 먼저
`scripts/graph_prereq_survey.sh`(읽기 전용)를 돌려 스키마 리비전·충전율·용량을 확인한
뒤 반입 여부를 판단한다. 이 세 스크립트는 "그래프를 만드는" §4의 `graph_sync.py`와는
별개 레이어다 — sync_*는 "Postgres에 운영과 같은 데이터가 있게 만드는" 선행 작업이고,
`graph_sync.py`는 그렇게 채워진 Postgres를 읽어 Neo4j를 만드는 작업이다.

---

## 1. 현재 상태 관찰 (재확인 필수)

**읽는 법**: 이 절의 수치는 **개발 DB(`citec-kb-postgres-1`) 스냅샷**이다. 코드에 구현된
기능(예: `ancestor_ids`, `body_hash`/`extractor_version` 적재)은 운영에서는 정상 동작·존재
한다고 간주한다 — 개발 DB에서 비어있는 건 배포/동기화 시점 차이일 뿐 설계 결함이 아니다.
반대로 데이터 볼륨 자체(사전 등록 건수, failure_bucket 내용 등)는 코드 유무와 무관한
실제 콘텐츠 현황이라 환경 차이로 설명되지 않는다 — 이 둘을 구분해서 읽을 것.

| 지점 | 위치 | 현재 동작 |
|---|---|---|
| 코퍼스 규모 | `documents` 테이블 | 70,322건. `confluence_map` 34,173 / `incident_reports` 15,349 / `checkitem` 8,989 / `confluence_docs` 5,509 / `tech_repo` 2,800 / `support_history` 2,365 / `dept_archive` 1,111 / `tuning_ai` 15 / `failure_bucket` 8 / `insight` 3 |
| 본문 비대칭 | `documents.body_md` | `confluence_map`은 **전 건이 breadcrumb만** 보유(평균 147자) — 운영서버엔 실제 본문이 있으나 현재 개발 DB엔 없음. `confluence_docs` 450건, `tech_repo` 84건도 본문 비어있음 |
| **confluence_map의 실질 역할** | `metadata->>'space_key'` 분포 + 제목 샘플 | **"참고용 포인터"가 아니라 장애/기술이슈의 1차 근거 자료.** space 분포: `ICLOUDUT` 15,701 / `LOOKIN` 8,228 / `TechRepo` 3,990 / `CLDENG` 3,949 / `DevOps001` 186 등. 제목 샘플: "4. SCP 아키텍처 Space", "[2023.05] Placement Group 설계서", "■ 운영계반영-작업계획, 2023년 05월", "[04/28] 상암 PP 스토리지 장애", "5/9 물산패션 HANA BW 장애 분석" — 아키텍처 설계서·운영 작업계획·장애 분석 기록이 실제로 들어있다. **더 결정적으로, confluence_map 34,173건 중 25,871건(76%)은 `tech_repo`/`confluence_docs`에 대응 문서가 전혀 없다**(`external_id` 기준 join 결과 8,306건만 중복) — 즉 이 76%에 대해서는 confluence_map이 **유일한 소스**이고, 운영에서 본문이 채워지면(§ 본문 비대칭) 다른 어떤 소스로도 대체되지 않는다. `evidence_grade="C"`(아래 §3.3)는 **답변 인용 우선순위**(A등급 중복 문서가 있을 때 그걸 우선)를 낮추는 용도일 뿐, **그래프 추출(컴포넌트/참조) 우선순위와는 별개 축**이다 — 혼동하면 1단계 설계에서 가장 내용이 풍부해질 소스를 "낮은 등급이니 나중에" 식으로 잘못 후순위화하게 된다 |
| 구조화 필드(티켓) | `apps/api/app/db/models.py:320` (`IssueFrame`) | 17,632건이지만 **필드별 충전율이 다름**: `symptom` 99.9%(17,622), `root_cause` 59%(10,426), `resolution` 60%(10,627), `components[]` 50%(8,891), `citec_domains[]` 62%(10,928), `environment` **14%뿐**(2,477). `body_hash`/`extractor_version`는 `apps/api/app/frames/job.py`에 정확히 배선돼 있음(재추출 스킵 판단에 둘 다 사용) — 개발 DB는 전체 17,632건이 NULL(이 재추출 잡이 개발 DB에서 아직 안 돌았을 뿐, 코드 결함 아님) — §4의 `graph_sync_state.input_hash`는 이 두 컬럼이 아직 NULL인 상태에서도(= 현재 개발 DB 상태에서도) 동작해야 하므로, `body_hash IS NULL`을 "값 없음"이 아니라 "content_hash로 폴백"으로 처리한다 |
| 구조화 필드(체크아이템) | `apps/api/app/db/models.py:222` (`Checkitem`) | 8,989건 전체 `area`/`category`/`category_1` 100% 충전, `subcategory` 99%(8,927). `area` distinct 65종(벤더/제품명 단위: `3PAR`,`Cisco_IOS`,`NetApp` 등), `category_1` distinct 10종 |
| failure_bucket 플라이휠 | `apps/api/app/failure_buckets/service.py` | `create_bucket`→`_index_bucket`→`embed_pending_chunks`, `refine_bucket`의 `signals_changed`/`environment_changed` 가드로 재인덱싱 스킵. `match_buckets()`가 이미 유사도 스코어 계산(`match.py:48-68`, `score = 0.6*signal_ratio + 0.4*confidence`, 0~1 범위, `_DUPLICATE_SCORE_THRESHOLD=0.75`) — **단, 이 점수는 두 버킷의 순수 신호 유사도가 아니라 대상 버킷의 기존 confidence가 40% 섞여 있음**, SIMILAR_TO 가중치로 쓸 때 참고 |
| 동의어 사전 | `apps/api/app/db/models.py:303` (`LexiconTerm`) | **10건뿐.** 본문 기반 컴포넌트 매칭의 1차 사전으로 쓰기엔 커버리지가 극히 낮음 — "재사용"이 아니라 사실상 신규 구축에 가까움(§4.2 보강 필요) |
| 비즈니스 엔티티 | `apps/api/app/db/models.py:258,286` (`Entity`/`DocumentEntity`) | `entities` 5건: `sys:monimo`(business_system), `sys:scp`(platform), `sys:redis`/`sys:oracle`(**type=component**), `sys:gro`(tech_term). **`type=component`인 행이 이미 있어 §3.2의 `Component`/`BusinessEntity` 분리와 개념이 겹침** — "Redis"가 `entities`(BusinessEntity 경로)와 `issue_frames.components`(Component 경로) 양쪽에서 들어올 수 있어, 그대로 두면 같은 실체가 노드 2개로 쪼개짐. `document_entities` 549건 |
| Confluence 계층 | `apps/api/app/confluence/sync.py:137,163,216` / `map_sync.py:258-260,327-331` / `apps/api/app/ingest/adapters.py:43,232-237,294-298,351-355` | 코드상 `ancestors` API 응답 → `Document.metadata_["ancestor_ids"]`로 정확히 구현돼 있음. **개발 DB엔 confluence_map/confluence_docs/tech_repo 42,482건 중 0건이 `ancestor_ids`를 가짐**(`metadata ? 'ancestor_ids'` 전수 0, 소스 `data/raw/*/\*.md`에 "조상ID목록" frontmatter 줄 자체가 없음 — `grep -l 조상ID목록 data/raw/tech_repo/*.md` → 0/2711) — **그러나 운영 서버에는 이미 채워져 있음(사용자 확인)**. 즉 본문과 같은 성격의 dev/운영 비대칭이며(§1 본문 비대칭과 동일 클래스), PARENT_OF 추출기는 운영에서는 즉시 동작하고 개발 DB에서만 공집합을 반환한다 — §4.3의 해시 기반 자동 재동기화 메커니즘이 그대로 적용된다(최근 머지된 `map-backfill-rps-env`/`map-backfill-source-ids` PR은 개발 DB를 운영과 맞추는 별개 작업). confluence_map은 `get_page_meta()`(`client.py:199-211`, `ancestors`만, 본문 無)로 채워지므로 본문 없이도 PARENT_OF 가능; confluence_docs/tech_repo는 `get_page_full()`(본문+ancestors 동시)이라 두 추출기가 같은 시점에 함께 가능해짐. **`content_hash` 계산에서 `ancestor_ids`는 의도적으로 제외됨**(`adapters.py:43-47`) |
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
| `(:Document)` | `documents` 전 건 | `id`(=Postgres id), `source_type`, `external_id`, `path`, `web_url`, **`environment`**(Document 컬럼, 70k 전체에 실존 — 충전율은 source_type별 상이, §1), **`space_key`**(confluence_map만 `metadata->>'space_key'`에서 추출, 그 외 source_type은 NULL), **`priority_tier`**(1/2/3, §0.1 표 그대로 계산해 저장 — 매번 쿼리로 재계산하지 않고 동기화 시점에 고정) |
| `(:Component)` | `issue_frames.components[]` + `checkitems.area/category*` + `entities`(`type IN ('component','tech_term')`) + `lexicon_terms` 매칭 | `canonical_name` (lexicon 통해 정규화). **`entities.type='component'`(Redis/Oracle)을 흡수** — 별도 BusinessEntity로 중복 생성하지 않는다 |
| `(:BusinessEntity)` | `entities`(`type IN ('business_system','platform')`)/`document_entities` 미러 | `id`, `canonical_name`, `type`. **`type='component'|'tech_term'`인 행은 여기 포함하지 않고 Component로 라우팅**(위 행 참고) |
| `(:FailureBucket)` | `failure_buckets` | `Document`와 별도 레이블 — 필드가 풍부해 전용 유지. `fb_domain`은 이 레이블의 속성(FailureBucket 전용 컬럼, Document엔 없음) |

`severity_tier`/`citec_domains`(IssueFrame 전용 컬럼)는 노드로도, Document 속성으로도 만들지 않는다 — IssueFrame이 있는 문서 서브셋(17,632/70,322)에만 의미가 있어 전체 Document에 걸치는 속성이 아니다. 필요해지면 `(:Document)-[:HAS_FRAME]->(:IssueFrame)` 식 별도 보조 노드를 2단계에서 검토한다(§6).

### 3.3 Neo4j 쪽 엣지

| 관계 | 방향 | 태그 | 소스 | 비고 |
|---|---|---|---|---|
| `PARENT_OF` | Document→Document | EXTRACTED | `metadata_["ancestor_ids"]` | confluence_map+confluence_docs+tech_repo 42,482건 커버, 본문 불필요. 운영에는 이미 채워져 있음(§1) — 개발 DB는 공집합 반환, 정상 동작 |
| `HAS_COMPONENT` | Document→Component | EXTRACTED | `issue_frames.components[]`(50% 충전, §1) / `checkitems.area*`(100%) | 구조화 필드, 즉시 가능하되 티켓 쪽은 절반만 커버 |
| `HAS_COMPONENT` | Document→Component | INFERRED | 본문 + `lexicon_terms` 매칭 | 본문 있는 문서만(현재 tech_repo/confluence_docs/dept_archive). **운영 동기화 후의 confluence_map `LOOKIN`/`TechRepo`(1순위, §0.1)가 이 추출기의 최우선 대상** — 나머지 공간(3순위)도 유의미하지만 부서 산출물보다 후순위. 사전이 10건뿐이라 1단계는 전체적으로 저조한 recall 예상(§4.2) |
| `MENTIONS_ENTITY` | Document→BusinessEntity | EXTRACTED | 기존 `document_entities`(`entities.type` business_system/platform만, §3.2) | 그대로 미러링, 549건 |
| `HAS_EVIDENCE` | FailureBucket→Document | EXTRACTED | `evidence_ref` 접두어 파싱(`citects-`/`confluence:`/`capture:`/`log:`/`legacy:`/…) | 8건 전수 파싱 시도하되, `documents.external_id`로 실제 해석 가능한 건 `confluence:`류뿐 — `capture:`/`log:`는 pcap/로그 파일이라 애초에 Document가 아님(§4.2), `legacy:pre-migration`은 대상 자체가 없음. **엣지 생성은 8건 중 소수(현재 샘플 기준 ~2건)만** — 나머지는 엣지 없이 `evidence_ref` 원문을 FailureBucket 속성으로만 보존 |
| `SIMILAR_TO` | FailureBucket↔FailureBucket | INFERRED | 기존 `match_buckets()` 점수 ≥ 0.75 | 8건, 전수 계산 가능 |
| `REFERENCES` | Document→Document | EXTRACTED | 본문 내 `CITECTS-\d+` 패턴 / 명시적 링크 | 본문 없는 문서(현재 개발 DB의 confluence_map 전부)는 자동으로 빈 결과 — 동작 자체는 정상이지만, **운영 동기화 전까지는 이 엣지의 가장 큰 잠재 커버리지(confluence_map 34,173건, 그중 1순위 `LOOKIN`/`TechRepo` 3,916건 포함)가 비어 있는 상태라는 걸 인지하고 있을 것** (§0.1) |

---

## 4. 백필 파이프라인

### 4.1 스크립트 구조 (`scripts/graph_sync.py`)

기존 `map_backfill` 류 스크립트와 동일한 CLI 관례(`--source-ids`, `--dry-run`, 배치 크기
옵션)를 따른다.

실행 순서는 `source_type` 하나짜리 리스트가 아니라 §0.1의 우선순위 그룹을 그대로 따른다
— confluence_map은 `space_key`로 먼저 쪼개 1순위/3순위 그룹에 각각 배분한다.

```
PRIORITY_GROUPS = [
    # 1순위 — 부서 산출물
    [("tech_repo", None), ("confluence_docs", None), ("checkitem", None),
     ("confluence_map", ["LOOKIN", "TechRepo"])],
    # 2순위 — 장애 정보
    [("incident_reports", None)],
    # 3순위 — 근거 자료
    [("confluence_map", "EXCLUDE:LOOKIN,TechRepo"), ("support_history", None),
     ("dept_archive", None), ("tuning_ai", None), ("failure_bucket", None),
     ("insight", None)],
]

for group in PRIORITY_GROUPS:
    for source_type, space_filter in group:
        for batch in documents.where(source_type=..., space_key_filter=space_filter).batched(size=500):
            for doc in batch:
                input_hash = compute_graph_hash(doc)
                state = graph_sync_state.get(doc.id)
                if state and state.input_hash == input_hash:
                    continue                      # 변경 없음 — 스킵
                edges = extract_edges(doc)        # §4.2 추출기 체인, 문서 단위 격리
                neo4j.merge(doc, edges)            # MERGE — 멱등
                graph_sync_state.upsert(doc.id, input_hash)
        prune_orphans(source_type, space_filter)   # Postgres에서 삭제된 id의 Neo4j 노드 정리
```

그룹 순서가 결과를 바꾸는 건 아니다(멱등 MERGE라 어느 순서로 돌아도 최종 그래프는 동일) —
다만 **백필이 중간에 멈추거나 시간 제약으로 일부만 돌릴 때, 1순위부터 반영되도록** 순서를
정했다.

### 4.2 추출기 체인 (독립 함수, 하나 실패해도 나머지 안 막힘)

1. `extract_hierarchy(doc)` — `ancestor_ids` → `PARENT_OF`(없으면 빈 리스트 반환, 에러 아님)
2. `extract_structured_components(doc)` — `issue_frames.components[]` 또는
   `checkitems.area/category*` → `HAS_COMPONENT`(EXTRACTED)
3. `extract_business_entities(doc)` — `document_entities` 미러 → `MENTIONS_ENTITY`
4. `extract_evidence(doc)` — `failure_buckets.evidence_ref` 파싱 →
   접두어가 `documents.external_id`로 해석 가능한 경우만(`confluence:`/`citects-`) `HAS_EVIDENCE`
   생성, 나머지(`capture:`/`log:`/`legacy:`)는 엣지 생략하고 로그만 남김. 같은 `external_id`가
   여러 `source_type`에 존재하면(예: confluence_docs+confluence_map 동시, §1) `evidence_grade`가
   높은 쪽(`A` > `B` > `C`)을 우선 선택
5. `extract_lexicon_components(doc)` — 본문 있는 문서만, `lexicon_terms` 매칭 →
   `HAS_COMPONENT`(INFERRED)
6. `extract_references(doc)` — 본문 내 티켓ID/링크 매칭 → `REFERENCES`

각 함수는 "Document 입력 → 엣지 목록 출력"의 순수 함수로, Neo4j 연결 없이 단위 테스트
가능해야 한다.

### 4.3 dev→운영 비대칭 처리

운영에서 `confluence_map`/`confluence_docs`/`tech_repo`의 `ancestor_ids`와 본문이 채워지면
(이미 운영엔 존재, §1) `content_hash`/`ancestor_ids` 둘 다(또는 둘 중 하나) 바뀌므로 →
`input_hash`도 바뀌어 → 같은 백필 스크립트를 재실행하면 1·5·6번 추출기가 자동 재실행된다.
별도 마이그레이션 스크립트나 "운영 전용 처리"는 만들지 않는다 — **개발 DB에서 지금 이
스크립트를 돌려도(1·5·6번이 당장은 빈 결과를 내더라도) 안전하고, 운영에 배포된 뒤 같은
스크립트 재실행만으로 자동 보강된다**는 것이 이 설계의 핵심 전제다. confluence_map 전체를
"본문 없는 저가치 소스"로 취급하지 않는다는 건 이미 §0.1/§4.1에서 `space_key` 기준으로
반영했다 — `LOOKIN`/`TechRepo`는 1순위 그룹에서 다른 부서 산출물과 같은 배치로 돈다.

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
  `extract_references`가 빈 결과로 정상 종료하는지, **본문이 채워진 confluence_map
  fixture(아키텍처 설계서/운영 작업계획/장애 분석 문서 형태 — §1 제목 샘플 참고)를 별도로
  추가해 같은 함수가 정상 동작하는지**. 이 소스타입의 content-fixture를 빠뜨리면 1단계
  구현이 "실제 가장 중요해질 케이스"를 테스트 없이 넘어가게 된다.

---

## 6. 범위 밖 (2단계 이후)

- 실시간 조회 API/MCP 도구(`kb_graph_path` 등) 노출
- failure_bucket 플라이휠(`create_bucket`/`refine_bucket`) 훅에 그래프 갱신 연동
- `citec_domains`/`severity_tier`의 노드(또는 `HAS_FRAME` 보조 노드) 승격 여부 재검토(실사용 패턴 확인 후)
- 티켓-티켓 임베딩 기반 `SIMILAR_TO` 확장(현재는 failure_bucket 간만)
- LLM 기반 의미 추출 보강(로컬 lexicon 매칭 커버리지 부족 시)
- **`lexicon_terms` 사전 확충**(현재 10건) — 1단계 `HAS_COMPONENT`(INFERRED) recall이 낮게
  나오는 주원인이므로, 실제 recall을 측정한 뒤 확충 범위를 정한다
- `issue_frames.components`(광범주: Network/Storage/Cluster)와 `checkitems.area`(벤더/제품:
  3PAR/Cisco_IOS/NetApp)를 잇는 상하위 매핑 테이블 — 1단계는 **정확히 같은 문자열일 때만**
  연결(Linux/Redis/Oracle/VMware/Network/Storage/SCP 7종 한정)하고, 상하위 추론은 하지 않는다
