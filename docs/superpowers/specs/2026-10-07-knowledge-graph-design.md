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
| **1순위 — 부서 산출물** | `tech_repo`(3,112) + `confluence_docs`(5,675) + `confluence_map WHERE space_key IN ('LOOKIN','TechRepo')`(미중복분만, 아래) + `checkitems`(8,989, PISA 체크리스트) | **반입 후 실측(§0.2)**: `LOOKIN` 8,497건 중 5,670건이 `confluence_docs`와 중복, **미중복 2,827건**. `TechRepo` 4,019건 중 3,110건이 `tech_repo`와 중복(+4건 confluence_docs 중복), **미중복 약 909건**. 즉 confluence_map에서 **총 약 3,736건**이 "아직 tech_repo/confluence_docs로 승격되지 않은 우리 부서 콘텐츠"다 |
| **2순위 — 장애 정보** | `incident_reports`(15,366, SWIM) | 장애 자체의 1차 기록. `issue_frames`의 `severity_tier`/`citec_domains` 충전이 이 소스에 집중(§1) |
| **3순위 — 근거 자료(타 공간/부서)** | `confluence_map WHERE space_key NOT IN ('LOOKIN','TechRepo')`(`ICLOUDUT` 15,953 / `Openstack101` 14,065 / `DevOps001` 10,661 / `CLDENG` 7,140 / `CATT` 5,058 / `EMCloud` 3,093 / `STORAGE` 2,806 / `SCPTechTree` 2,321 / `DFTRTS` 1,194 / `sysops` 1,068 / `ServiceExcellenceTeam` 883 / `GUID` 842 / `SI` 670 / `SPC` 2, 합계 65,756건) + `support_history`/`dept_archive`/`tuning_ai`/`insight` | 아키텍처 설계·운영 작업계획·장애 분석 등 **근거로 참고하되 1차 산출물은 아님**. 사용자 확인: 신규 공간(`CATT`/`STORAGE`/`SCPTechTree`/`sysops`/`SI` 등)도 전부 타부서/일반 인프라 — 1순위는 `LOOKIN`/`TechRepo`뿐 |

**이 우선순위가 그래프 설계에 미치는 영향**:
- `space_key`를 `(:Document)` 노드 속성으로 반드시 보존해야 한다(§3.2 수정) — 지금까지의
  설계엔 빠져 있었다. `source_type='confluence_map'`만으로는 1순위/3순위를 구분 못 한다.
- `evidence_grade`(A/B/C, §3.3)는 **답변 인용 등급**일 뿐 이 부서 우선순위와 다른 축이다 —
  confluence_map은 전부 C등급이지만 그중 LOOKIN/TechRepo 미중복분(약 3,736건, §0.2 실측)은 1순위다.
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

**운영 조사 결과(2026-10-07, `graph_prereq_survey.sh` 실행 결과 확인)**:
- 스키마 리비전 `20260930_0009` — dev와 동일, 마이그레이션 문제 없음.
- `ancestor_ids`가 실제로 채워져 있음을 확인: confluence_map 78,136/78,272(99.8%),
  confluence_docs 5,660/5,675(99.7%), tech_repo 3,108/3,112(99.9%) — §1에서 "코드가
  구현돼 있으면 운영엔 있다"고 가정했던 것이 실측으로 확인됐다.
- `issue_frames.body_hash`/`extractor_version`도 17,727/17,729(99.99%) 충전 — dev는
  0%였지만 운영은 재추출 잡이 이미 돌아 있었다. 반대로 `components`(53.7%)/
  `environment`(15.5%)/`root_cause`(62.3%) 충전율은 dev와 거의 동일 — 이건 환경차가
  아니라 실제 콘텐츠 현황이라는 §1의 판단이 맞았다.
- **confluence_map이 dev보다 훨씬 크다**: 78,272건(dev 34,173의 2.3배). space_key도
  dev에 없던 공간(`CATT` 5,058 / `STORAGE` 2,806 / `SCPTechTree` 2,321 / `sysops` 1,068 /
  `SI` 670 등)이 다수 추가됐고 `DevOps001`은 186→10,661로 급증. **사용자 확인: 이
  신규 공간들도 전부 3순위(타부서/일반 인프라) — 1순위는 여전히 `LOOKIN`/`TechRepo`만**
  (§0.1 우선순위 표는 변경 없음, 다만 LOOKIN 8,228→8,497, TechRepo 3,990→4,019로 소폭
  증가 — 실제 반입 후 tech_repo/confluence_docs와의 중복 건수는 재계산 필요).
- `lexicon_terms`(10건)/`entities`(5건)는 운영도 dev와 완전히 동일 — 환경차가 아니라
  정말 이만큼만 등록돼 있다(§6의 사전 확충 과제는 그대로 유효).
- `failure_buckets`(8건)도 운영과 dev가 동일 — 이미 양쪽이 같은 데이터.
- **전송 용량**: 9테이블+raw_files 전체 약 2.8GB(`chunks` 2GB가 대부분). 그래프 백필엔
  chunks/document_sections/raw_files가 불필요해, `sync_manifest.sh`/`sync_export.sh`에
  `--profile graph`를 추가했다(약 700MB로 축소) — documents(623MB)가 대부분이고
  나머지(issue_frames/checkitems/failure_buckets/entities/document_entities/
  lexicon_terms)는 수십 MB 이하. 사용자 선택: **graph 프로파일로 반입**.

### 0.2 반입 완료 + 실데이터 재검증 (2026-10-07)

`sync_export.sh --profile graph` → `sync_apply.sh`로 실제 반입을 진행하며 스크립트
버그 2건을 더 발견해 고쳤다(둘 다 "최근 마이그레이션 컬럼이 sync 스크립트에 반영 안
됨" 같은 유형):
- `sync_export.sh`: `key_expr_for()` 기본값이 `tbl.` 한정자 없이 `id`만 반환해
  `_sync_ids`와의 JOIN에서 "ambiguous column" 에러 — 운영 1차 실행에서 실제로 발생.
- `sync_apply.sh`: `issue_frames` INSERT가 `citec_domains`/`severity_tier`/`body_hash`/
  `extractor_version` 4개 컬럼을 빠뜨리고 있었음(failure_buckets 때와 같은 유형).
  1차 반입 후 `body_hash`가 0건으로 남아있어 발견, 고친 뒤 같은 번들을 재적용해 복구.
- **사고 1건**: `sync_apply.sh`의 `documents.csv` 처리가 `chunks.csv`/
  `document_sections.csv`가 같은 번들에 없어도 기존 chunk를 무조건 비활성화하는
  로직이라, `--profile graph`(둘 다 안 담음) 적용 시 로컬 테스트에서 이 dev DB의
  활성 chunk가 178,310→0이 됐다(검색 불가 상태). `embeddings.chunk_id`로 정확히
  복구하고, cleanup을 `chunks.csv` 존재 여부로 게이팅해 재발 방지.

**반입 후 dev DB는 documents/issue_frames에 한해 운영과 동일하다** (checkitems/
failure_buckets/entities/document_entities/lexicon_terms는 반입 전부터 이미 운영과
동일했음 — diff 0). 이를 근거로 §1의 수치를 실측치로 전부 갱신한다(아래).

**이전 추정 대비 중요한 정정**:
- confluence_map 유일 소스 비율이 **76% → 88.8%(69,492/78,272)**로 더 높다 — dev
  스냅샷 기준 추정이 과소평가였다.
- 1순위(`LOOKIN`+`TechRepo`) 중 아직 tech_repo/confluence_docs로 승격 안 된 분량도
  재계산: LOOKIN 8,497건 중 confluence_docs 중복 5,670건 → 미중복 **2,827건**.
  TechRepo 4,019건 중 tech_repo 중복 3,110건(+confluence_docs 중복 4건) → 미중복
  **약 909건**. 합계 **약 3,736건**(이전 추정 3,916건과 비슷한 규모, 오차 범위 내).
- `lexicon_terms`(10건) 실제 매칭 커버리지를 본문 전체에 대해 직접 측정: confluence_map
  +confluence_docs+tech_repo+dept_archive(88,170건, 본문 있는 문서) 중 10개 용어
  중 하나라도 포함된 문서는 **3,147건(3.6%)뿐**. §6의 "저조한 recall 예상"이
  구체적 수치로 확인됐다 — `HAS_COMPONENT`(INFERRED) 1단계는 이 정도 커버리지로
  시작한다는 뜻이고, 사전 확충이 선행돼야 체감 가능한 수준이 된다.

---

## 1. 현재 상태 관찰 (재확인 필수)

**읽는 법 (2026-10-07 갱신)**: §0.2의 운영→개발 반입 이후, `documents`/`issue_frames`는
**개발 DB가 운영과 동일한 실측치**다(diff 0 확인). `checkitems`/`failure_buckets`/
`entities`/`document_entities`/`lexicon_terms`는 반입 전부터 이미 운영과 같았다.
`chunks`/`document_sections`만 `--profile graph`로 의도적으로 가져오지 않아 여전히
구(舊) 데이터다(그래프 설계엔 안 쓰이므로 무해). 아래 수치는 전부 이 반입 후 실측.

| 지점 | 위치 | 현재 동작 |
|---|---|---|
| 코퍼스 규모 | `documents` 테이블 | 114,930건. `confluence_map` 78,272 / `incident_reports` 15,366 / `checkitem` 8,989 / `confluence_docs` 5,675 / `tech_repo` 3,112 / `support_history` 2,378 / `dept_archive` 1,111 / `tuning_ai` 16 / `failure_bucket` 8 / `insight` 3 |
| 본문 비대칭 (해소됨) | `documents.body_md` | **반입 후 confluence_map도 실제 본문**(평균 4,958자, 최대 1.3MB, 58,820/78,272건이 500자 초과)으로 확인됨 — 더 이상 breadcrumb-only 아님. `confluence_docs` 454/5,675(8%), `tech_repo` 301/3,112(9.7%)는 **운영에도 실제로 본문이 빈 문서**(환경차 아님, 콘텐츠 자체의 공백) |
| **confluence_map의 실질 역할** | `metadata->>'space_key'` 분포(반입 후 실측) | **"참고용 포인터"가 아니라 장애/기술이슈의 1차 근거 자료.** space 분포: `ICLOUDUT` 15,953 / `Openstack101` 14,065 / `DevOps001` 10,661 / `LOOKIN` 8,497 / `CLDENG` 7,140 / `CATT` 5,058 / `TechRepo` 4,019 / `EMCloud` 3,093 / `STORAGE` 2,806 / `SCPTechTree` 2,321 / `DFTRTS` 1,194 / `sysops` 1,068 / `ServiceExcellenceTeam` 883 / `GUID` 842 / `SI` 670 / `SPC` 2. **confluence_map 78,272건 중 69,492건(88.8%)은 `tech_repo`/`confluence_docs`에 대응 문서가 전혀 없다**(이전 dev 스냅샷 기준 추정 76%보다 높음) — 이 88.8%에 대해 confluence_map이 **유일한 소스**다. `evidence_grade="C"`(§3.3)는 답변 인용 등급일 뿐 그래프 추출 우선순위와는 별개 축(§0.1) |
| 구조화 필드(티켓) | `apps/api/app/db/models.py:320` (`IssueFrame`) | 17,729건(반입 후), **필드별 충전율**: `symptom` 100%(17,729), `root_cause` 62.3%(11,043), `resolution` 65.1%(11,539), `components[]` 53.7%(9,516), `citec_domains[]` 61.6%(10,928), `environment` **15.5%뿐**(2,752). `body_hash`/`extractor_version`는 반입 후 **99.99%(17,727) 충전 확인**(운영 재추출 잡이 이미 돌아 있었음) — §4의 `graph_sync_state.input_hash`는 이제 실제로 채워진 값을 보게 된다 |
| 구조화 필드(체크아이템) | `apps/api/app/db/models.py:222` (`Checkitem`) | 8,989건 전체 `area`/`category`/`category_1` 100% 충전, `subcategory` 99%(8,927). `area` distinct 65종(벤더/제품명 단위: `3PAR`,`Cisco_IOS`,`NetApp` 등), `category_1` distinct 10종 |
| failure_bucket 플라이휠 | `apps/api/app/failure_buckets/service.py` | `create_bucket`→`_index_bucket`→`embed_pending_chunks`, `refine_bucket`의 `signals_changed`/`environment_changed` 가드로 재인덱싱 스킵. `match_buckets()`가 이미 유사도 스코어 계산(`match.py:48-68`, `score = 0.6*signal_ratio + 0.4*confidence`, 0~1 범위, `_DUPLICATE_SCORE_THRESHOLD=0.75`) — **단, 이 점수는 두 버킷의 순수 신호 유사도가 아니라 대상 버킷의 기존 confidence가 40% 섞여 있음**, SIMILAR_TO 가중치로 쓸 때 참고. 8건, 운영과 반입 전부터 동일 |
| 동의어 사전 | `apps/api/app/db/models.py:303` (`LexiconTerm`) | **10건뿐, 운영도 동일.** 본문 전체(confluence_map+confluence_docs+tech_repo+dept_archive, 88,170건)에 대해 직접 측정한 매칭률은 **3,147건(3.6%)** — "저조한 recall"이 추정이 아니라 실측으로 확정됨(§0.2). 1차 사전으로 쓰기엔 극히 낮아 "재사용"이 아니라 사실상 신규 구축에 가까움(§4.2 보강 필요) |
| 비즈니스 엔티티 | `apps/api/app/db/models.py:258,286` (`Entity`/`DocumentEntity`) | `entities` 5건(운영 동일): `sys:monimo`(business_system), `sys:scp`(platform), `sys:redis`/`sys:oracle`(**type=component**), `sys:gro`(tech_term). **`type=component`인 행이 이미 있어 §3.2의 `Component`/`BusinessEntity` 분리와 개념이 겹침** — "Redis"가 `entities`(BusinessEntity 경로)와 `issue_frames.components`(Component 경로) 양쪽에서 들어올 수 있어, 그대로 두면 같은 실체가 노드 2개로 쪼개짐. `document_entities` 549건(운영 동일) |
| Confluence 계층 (해소됨) | `apps/api/app/confluence/sync.py:137,163,216` / `map_sync.py:258-260,327-331` / `apps/api/app/ingest/adapters.py:43,232-237,294-298,351-355` | **반입 후 실측 확인**: confluence_map 78,136/78,272(99.8%), confluence_docs 5,660/5,675(99.7%), tech_repo 3,108/3,112(99.9%)가 `ancestor_ids`를 가짐 — PARENT_OF 추출기가 이제 이 dev DB에서도 바로 동작한다(더 이상 공집합이 아님). `content_hash` 계산에서 `ancestor_ids`는 의도적으로 제외됨(`adapters.py:43-47`) — §3.1의 `graph_sync_state.input_hash`가 `content_hash`와 별도로 `ancestor_ids`를 포함해야 한다는 설계는 그대로 유효(페이지 이동 시 재감지용) |
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

`severity_tier`/`citec_domains`(IssueFrame 전용 컬럼)는 노드로도, Document 속성으로도 만들지 않는다 — IssueFrame이 있는 문서 서브셋(17,729/114,930, §0.2 실측)에만 의미가 있어 전체 Document에 걸치는 속성이 아니다. 필요해지면 `(:Document)-[:HAS_FRAME]->(:IssueFrame)` 식 별도 보조 노드를 2단계에서 검토한다(§6).

### 3.3 Neo4j 쪽 엣지

| 관계 | 방향 | 태그 | 소스 | 비고 |
|---|---|---|---|---|
| `PARENT_OF` | Document→Document | EXTRACTED | `metadata_["ancestor_ids"]` | **반입 완료(§0.2) — 이제 개발 DB도 채워져 있음**: confluence_map+confluence_docs+tech_repo 87,059건 중 86,904건(99.8%)이 보유. 본문 불필요 |
| `HAS_COMPONENT` | Document→Component | EXTRACTED | `issue_frames.components[]`(50% 충전, §1) / `checkitems.area*`(100%) | 구조화 필드, 즉시 가능하되 티켓 쪽은 절반만 커버 |
| `HAS_COMPONENT` | Document→Component | INFERRED | 본문 + `lexicon_terms` 매칭 | 이제 confluence_map도 본문 보유(§1 해소됨). confluence_map `LOOKIN`/`TechRepo`(1순위, §0.1)가 이 추출기의 최우선 대상. **실측 recall 3.6%(3,147/88,170건, §0.2)** — 사전 10건으로는 1단계가 의미 있는 커버리지를 내기 어렵다는 게 추정이 아니라 확정됐다. 사전 확충을 먼저 하거나, 1단계 결과물을 "낮은 recall로 시작, 점진 확충"으로 명시하고 가야 함 |
| `MENTIONS_ENTITY` | Document→BusinessEntity | EXTRACTED | 기존 `document_entities`(`entities.type` business_system/platform만, §3.2) | 그대로 미러링, 549건 |
| `HAS_EVIDENCE` | FailureBucket→Document | EXTRACTED | `evidence_ref` 접두어 파싱(`citects-`/`confluence:`/`capture:`/`log:`/`legacy:`/…) | 8건 전수 파싱 시도하되, `documents.external_id`로 실제 해석 가능한 건 `confluence:`류뿐 — `capture:`/`log:`는 pcap/로그 파일이라 애초에 Document가 아님(§4.2), `legacy:pre-migration`은 대상 자체가 없음. **엣지 생성은 8건 중 소수(현재 샘플 기준 ~2건)만** — 나머지는 엣지 없이 `evidence_ref` 원문을 FailureBucket 속성으로만 보존 |
| `SIMILAR_TO` | FailureBucket↔FailureBucket | INFERRED | 기존 `match_buckets()` 점수 ≥ 0.75 | 8건, 전수 계산 가능 |
| `REFERENCES` | Document→Document | EXTRACTED | 본문 내 `CITECTS-\d+` 패턴 / 명시적 링크 | **반입 후 confluence_map도 실제 본문을 가짐(§1 해소됨)** — 이제 이 추출기가 전체 코퍼스(특히 confluence_map 78,272건)에 대해 실제로 동작할 수 있다. 실제 링크/티켓ID 언급 빈도는 아직 측정 안 함 — 구현 단계에서 확인 필요 |

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

### 4.3 dev→운영 비대칭 처리 (§0.2에서 이미 해소됨)

이 절은 원래 "개발 DB에 ancestor_ids/본문이 없어도 안전하게 동작해야 한다"는 설계
요구였다. §0.2의 운영→개발 반입(`sync_apply.sh`)으로 **이미 실제로 해소됐다** —
개발 DB의 `documents`/`issue_frames`가 운영과 동일하다. 다만 설계 원칙 자체는 유지한다:
앞으로 운영에서 추가로 생기는 변경(신규 confluence 페이지, 재추출 등)도 `content_hash`/
`ancestor_ids`가 바뀌면 `input_hash`도 바뀌어 1·5·6번 추출기가 **같은 sync_apply.sh +
graph_sync.py 재실행만으로** 자동 반영돼야 한다 — 매번 특수 처리를 만들지 않는다.
confluence_map 전체를 "본문 없는 저가치 소스"로 취급하지 않는다는 건 §0.1/§4.1에서
`space_key` 기준으로 반영했다 — `LOOKIN`/`TechRepo`는 1순위 그룹에서 다른 부서
산출물과 같은 배치로 돈다.

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
- 본문 없음/있음 회귀 테스트: `body_md=""`인 입력에서 `extract_references`/
  `extract_lexicon_components`가 빈 결과로 정상 종료하는지(에러 아님), 본문이 있는
  입력(§1의 confluence_map 샘플 — 아키텍처 설계서/운영 작업계획/장애 분석 형태)에서
  정상 동작하는지 둘 다 fixture로 고정한다. **§0.2 반입 이후 이 dev DB엔 더 이상
  breadcrumb-only 빈 본문 confluence_map이 없으므로**(전부 실제 본문), 빈 본문 케이스는
  합성(synthetic) fixture로 따로 만들어야 한다 — 실 데이터에서 재현 안 됨.

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
