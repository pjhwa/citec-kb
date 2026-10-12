# 그래프 정합성 수정 — G01~G05 설계 결정 (2026-10-12)

이 문서는 `/tmp/CLAUDE_CODE_PROMPT.md`(2026-10-12 분석 핸드오프, 분석 커밋
`39fd1237580da9db70441b15a654f825d79a8dcf`)의 G01~G11 중 P0 항목(G01~G05)을
구현하기 전에 확정한 설계 결정이다. 분석 프롬프트 자체를 승인된 스펙으로 보고,
이 문서는 "어떻게 구현하는가"의 세부 결정만 다룬다.

## 범위

이번 커밋 묶음은 P0(G01~G05)와, 구현 중 자연히 같이 고쳐지는 P1 일부(G09 등급
순서, G06 비파괴 lexicon upsert + NetApp 별칭)만 다룬다. G07/G08/G10/G11의
나머지(페이지 이동, MCP page 이동 UI, failure_bucket resolution URL 파싱 등)는
`docs/features/graph-evidence.md`에 미완료로 남긴다.

## Step 0 확인 결과

- HEAD는 분석 커밋과 동일(`39fd123`) — 이미 해결된 항목 없음.
- 저장소 루트에 `AGENTS.md`/`CLAUDE.md` 없음 — 별도 지침 없음, 확인 완료.
- `apps/api/app` 은 bind mount라 코드 수정이 `docker compose restart api`만으로
  반영된다. `mcp-server/server.py`도 bind mount.
- confluence-mcp, github-mcp 세션 도구 없음 → **BLOCKED**: 라이브 Confluence
  재검증과 10월 9일 zip 재다운로드는 이번 세션에서 미검증으로 남긴다. 코드
  근거(evidence/source-excerpts.md)와 개발 DB 조회로 대체한다.
- PG 감사: `595824602`/`604365026`/`604365046` 세 페이지 모두 `confluence_map`
  단일 source로만 존재(중복 없음) — TGW 케이스는 cross-source 중복 문제가
  아니라 순수 self-비교/direction 버그다. 전체 corpus엔 external_id 중복 그룹이
  8,780개 존재(주로 confluence_map ↔ confluence_docs/tech_repo) — 대표 선택
  규칙은 범용으로 필요.
- evidence_grade 분포: A(35210)/A-(16)/B(1421)/C(78272)/machine(8)/draft(3).
  기존 `explore.py:_GRADE_ORDER`가 "A-"를 빠뜨려 C와 동일 취급되는 버그를 G09로
  확인(코드 근거로 재현, 수정은 이번 묶음에 포함).
- Neo4j 감사(dev, 이전 500건 샘플 상태): Document 1330개 중 830개가
  title/source_type NULL(라벨은 Document) — G01이 설명하는 "raw id가 별도
  Document로 생성" 버그의 직접 증거. self-loop PARENT_OF는 0건이지만, 이는
  self 비교가 애초에 실패해 self가 체인에서 안 빠지고 **접두사 없는 raw id로
  별도 노드**가 생성되기 때문(진짜 self-loop가 아니라 유령 노드가 생김) — 분석
  문서의 설명과 일치.
- 격리 테스트 인프라 없음(`GRAPH_NEO4J_TEST_URI`/`GRAPH_TEST_DATABASE_URL` 미설정,
  별도 scratch Neo4j 컨테이너 없음) → 이번 작업에서 임시 Neo4j 컨테이너 1개를
  별도 포트로 띄우고, Postgres는 기존 컨테이너 안에 scratch DB를 만들어 사용한다
  (운영/dev 메인 DB에는 쓰지 않음).

## G01 — 식별자 resolver

- 새 모듈 `apps/api/app/graph/identity.py`:
  - `GRADE_RANK`: `{"A":0,"A-":1,"B":2,"C":3,"machine":4,"draft":5}` — 기존
    `failure_bucket._GRADE_RANK`와 동일한 값을 단일 소스로 통합하고,
    `explore.py`/`failure_bucket.py`가 여기서 import하도록 바꾼다(G09 수정 겸).
  - `build_page_identity_index()`: Postgres에서
    `(id, external_id, status, evidence_grade, source_type, metadata->>'space_key')`를
    전부 읽어 `external_id(소문자 strip) -> list[candidate]`로 묶는다. 기존
    `build_external_id_index()`(id/external_id/grade만)보다 넓은 상위집합이라,
    `build_external_id_index()`는 이 함수의 결과에서 두 필드만 뽑는 얇은 래퍼로
    바꾼다(쿼리 중복 제거).
  - `resolve_representative(candidates) -> dict | None`: 결정적 tie-break —
    1) `status == "active"` 우선 2) `GRADE_RANK` 작은 값 우선
    3) source tier(`app.graph.pipeline.compute_priority_tier` 재사용, tier
    낮은 숫자 우선) 4) `document_id` 오름차순(최종 동률 방지). 후보가 비면 `None`.
  - `best_candidate_id(candidates) -> str | None`: `resolve_representative`의
    `document_id`만 뽑는 래퍼 — 기존 `failure_bucket.best_candidate`와
    `routers/graph._resolve_document_anchor_value`의 "임의 첫 행" 로직을 모두
    이 함수로 교체한다(동일 정책 재사용 원칙).
- `extract_hierarchy(doc, *, page_identity_index)`로 시그니처 변경(순수 함수
  유지 — DB 접근은 pipeline이 만든 인덱스를 인자로 받을 뿐):
  1. self 제거: `doc.get("external_id")`(raw page id, 소문자 strip)와 ancestor
     체인의 각 원소를 비교해 제거. 기존의 `doc["id"]`(접두사 포함) 비교도
     안전망으로 유지(다른 source 타입 대비).
  2. 직계 부모만 사용: 남은 체인의 **마지막 원소 1개**만 부모 후보로 쓴다(전체
     조상 edge 생성 중단 — G02).
  3. 부모 후보를 `page_identity_index`로 조회해 대표 Document를 찾는다.
     - 해석 성공: `Edge(rel_type="PARENT_OF", target_label="Document",
       target_key="id", target_value=<대표 document_id>, tag="EXTRACTED")`.
     - 해석 실패(코퍼스에 없음): `Edge(rel_type="PARENT_OF",
       target_label="UnresolvedPage", target_key="external_id",
       target_value=<raw id>, tag="EXTRACTED")` — `UnresolvedPage`는 Document가
       아닌 별도 레이블이라 API/MCP의 `"Document" in labels` 분기에 절대
       걸리지 않는다(= "title/source 없는 노드를 Document처럼 보여주지 않는다"
       요구를 라벨 수준에서 보장).

## G02 — PARENT_OF 방향

- `Edge.target_value`는 "내 부모"를 의미하도록 유지(extract_hierarchy 호출자
  관점에서 직관적). 그러나 실제 Neo4j에 쓸 때는 **물리적으로 parent→child**가
  되어야 하므로, `Neo4jClient._merge_edges`에 레이블-무관 특수 케이스를 추가:
  `rel_type == "PARENT_OF"`일 때만 MERGE의 source/target을 뒤집어
  `(parent)-[:PARENT_OF]->(child)`로 쓴다. 다른 rel_type은 그대로
  `(source)-[:rel]->(target)`.
- `Neo4jClient.explore()`는 `-[r]-`(무방향) 패턴이라 이 변경으로 2-hop 결과
  집합 자체는 바뀌지 않는다 — 오직 "직계 부모 1개만" 효과(조상 전체 fan-out
  제거)와 "그래프를 직접 열어본 사람에게 보이는 방향이 맞다"는 것만 바뀐다.

## G03 — 재동기화 시 교체(replace-on-resync)

- 멱등 재동기화를 위해 `Neo4jClient.merge_document`/`merge_failure_bucket`가
  edges를 쓰기 **전에** 해당 엔티티가 "소유"하는 managed relationship을 모두
  지운다. 소유 방향은 rel_type별로 고정:
  - `PARENT_OF`: 자신이 **타깃**(자신은 child) → `(self)<-[r:PARENT_OF]-()` 삭제.
  - `HAS_COMPONENT`/`REFERENCES`/`MENTIONS_ENTITY`: 자신이 **소스**(Document) →
    `(self)-[r:...]->()` 삭제.
  - `HAS_EVIDENCE`/`SIMILAR_TO`: 자신이 **소스**(FailureBucket) →
    `(self)-[r:...]->()` 삭제.
  - 항상 managed rel_type 전체를 지우고 새 edges로 재생성 — 이번 추출에서
    사라진 멘션(예: 본문에서 빠진 컴포넌트)도 자연히 제거된다.
- EXTRACTED/INFERRED 태그 충돌(같은 (document, Component) 쌍에 구조화+본문
  추출이 둘 다 edge를 내는 경우)은 pipeline에서 edges 리스트를 만들 때
  `(target_label, target_key, target_value, rel_type)` 키로 dedup하면서
  EXTRACTED를 INFERRED보다 우선시켜 해결한다(Neo4j 쓰기 전에 끝내 — merge
  단계에서는 이미 충돌이 없다).
- 이 설계는 "owner 속성을 relationship에 저장"하는 대안보다 단순하다 — PARENT_OF
  를 제외한 모든 managed rel_type은 source=owner라 방향 자체가 소유권이고,
  PARENT_OF는 방향을 뒤집어 쓰되 "자신이 타깃"이라는 규칙 하나만 추가하면
  충분하다. 여러 다른 문서가 같은 물리적 edge를 동시에 주장하는 경우는 현재
  데이터 모델에 존재하지 않는다(각 edge는 정확히 하나의 owner 문서/버킷에서
  나온다).

## G04 — 제외 필터(이번 묶음 범위)

- `graph_explore`/`kb_graph_explore`에 `exclude_page_ids`/`exclude_subtree_ids`/
  `exclude_source_types`를 추가하되, 이번 묶음에서는 **candidate 생성 이후,
  dedup/정렬/cap 이전**에 적용하는 최소 구현만 한다(중간 경로 bridge 차단,
  페이지 분할 등 G07/G08급 고도화는 P1 잔여로 남긴다). `exclude_subtree_ids`는
  `app.retrieval.search`와 동일하게 `ancestor_ids`(Postgres 메타데이터, 1단계
  때 이미 PG에 저장돼 있음)로 판정한다.

## G05 — 동기화 세대/신선도

- `graph_sync_state`에 `graph_generation` 컬럼 추가(알렉빅 마이그레이션).
  `sync_document()`는 `generation` 인자를 받고, 기존 hash 비교에
  `state["graph_generation"] == generation`도 추가해야 skip한다 — 새 Neo4j로
  옮겼는데 PG state가 이전 그래프를 가리키는 상황(이번 세션에서 dev/운영
  백필 때 실제로 겪음)을 재발 방지.
- `graph_runs` 테이블 신규: run_id(uuid pk), started_at, completed_at,
  extractor_version, generation, lexicon_version, documents_synced/skipped/failed,
  buckets_synced/failed, unresolved_references, hub_recompute_ok, status
  (`"running"|"completed"|"failed"`). `sync_cli.py`가 시작 시 1행 insert,
  종료 시(성공/실패 모두) update.
- 기존 `get_latest_synced_at()`(as_of)는 하위 호환을 위해 유지하되, 이번
  `graph_runs`가 "마지막 완료 배치"의 더 정확한 신호를 제공한다는 것을
  `docs/features/graph-evidence.md`와 AI_AGENT_GUIDE.md에 문서화한다. 새 REST
  엔드포인트(`/v1/graph/status` 등)는 P1 잔여로 남긴다(이번 범위는 CLI
  출력+DB 테이블까지).

## 비파괴 원칙

- `scripts/verify/`의 통합 테스트는 전용 scratch Neo4j 컨테이너 + scratch
  Postgres DB에서만 실행한다. dev 메인 스택의 Postgres/Neo4j는 **읽기 전용
  감사**에만 쓴다.
- dev 메인 스택에 대한 실제 재백필(새 generation 전체 재동기화)은 이 설계가
  스크립트/테스트로 검증된 후, 사람 승인 없이도 수행 가능(운영이 아니라 dev,
  파괴적 삭제 없음 — MERGE/치환만) — 단 실행 전 현재 Neo4j 건수를 기록해
  rollback 참고용으로 남긴다.
