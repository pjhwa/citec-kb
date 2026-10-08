# citec-kb 지식그래프 백필 — 실데이터 전수 검증 보고 (2026-10-08)

이 문서는 `docs/superpowers/plans/2026-10-08-knowledge-graph-backfill.md`(14개
태스크 구현 계획)와 `docs/superpowers/specs/2026-10-07-knowledge-graph-design.md`
(설계)에 따라 구현한 `app.graph` 패키지를, **메인 체크아웃의 실제 운영 미러
DB(114,930건 문서)를 대상으로 전수 실행**해 검증한 기록이다. 코드 구현(14개
태스크 + 리뷰에서 나온 7건의 버그수정)은 이 PR의 다른 커밋들에 이미 포함돼
있고, 이 문서는 그 코드를 **작은 테스트 픽스처가 아니라 실제 전체 데이터로
돌려본 결과**만 별도로 기록한다.

## 0. 범위와 전제

- **그래프 전환이 아니라 파생 저장소 추가**: 기존 PostgreSQL FTS+pgvector
  하이브리드 검색은 그대로 운영 중이고, 이번 백필은 Neo4j를 별도로 채우는
  작업이다. 실시간 조회/MCP 노출은 설계 스펙 §6에 따라 2단계로 남아있다.
- **메인 체크아웃 DB에 적용한 변경(사용자 승인 후 실행)**: `graph_sync_state`
  테이블 1개 추가(`alembic upgrade head`, 마이그레이션
  `20261008_0010_graph_sync_state`) — 기존 테이블은 전혀 건드리지 않았다.
- **검증용 Neo4j**: 메인 체크아웃 docker-compose 스택과는 별개로, 이번
  검증 전용 컨테이너(`graph-realdata-neo4j`, 포트 18578/18579)를 새로 띄워
  거기에만 썼다. 메인 스택의 다른 컨테이너는 건드리지 않았다.

## 1. 사전 확인

```
scripts/graph_sync.sh 의 실제 로직(python -m app.graph.sync_cli)을
메인 체크아웃 citec-kb-postgres-1(포트 8574)에 직접 연결해 --dry-run 실행:
{"documents": 114930, "failure_buckets": 8}
```
설계 스펙 §0.2가 예측한 정확한 건수(114,930건 문서, 8건 failure_bucket)와
일치함을 확인했다.

## 2. 500건 실데이터 샘플 사전 테스트

전체 백필 전에, confluence_map/incident_reports/checkitem/tech_repo/
confluence_docs를 섞은 실제 문서 500건 + failure_bucket 8건 전수로 먼저
실행했다.

| 항목 | 결과 |
|---|---|
| 소요 시간 | 35.7초 |
| 실패 | 0건 |
| 생성된 노드 | Document 1,583 / Component 71 / FailureBucket 8 |
| 생성된 엣지 | PARENT_OF 1,950 / HAS_COMPONENT 949 / REFERENCES 14 / HAS_EVIDENCE 1 |

실제 컴포넌트 매칭 샘플(발췌):

```
"참고) CSP별 스토리지 암호화 기능"           -> NetApp
"성능/용량부문 진단"                         -> NetApp
"04.3 NetApp Hands-on Labs 사용 가이드"      -> NetApp
```

Neo4j 유니크 제약 4개(Document.id/FailureBucket.id/Component.canonical_name/
BusinessEntity.id) 전부 정상 생성 확인.

## 3. 전체 114,930건 백필

최초 실행(`-v` 포함)은 Neo4j bolt 프로토콜 디버그 로그가 초당 수백 KB씩
쌓여(20초에 5MB) 비현실적인 로그 용량이 될 것으로 판단해 중단(1,330건 처리된
상태)하고, `-v` 없이 재시작했다 — 이미 처리된 1,330건은 해시 일치로 재실행 시
자동으로 스킵되어 멱등성이 그대로 증명됐다.

**최종 결과**:

```
{"documents": {"synced": 113600, "skipped": 1330, "failed": 0},
 "failure_buckets": {"synced": 8, "failed": 0}}
```

113,600 + 1,330 = 114,930건 — **전체 100% 처리, 실패 0건**.

실행 시간: 약 1시간 40분(08:27~10:0x, 백그라운드 실행 중 2회 중간 진행률 확인:
08:27 시작 → 41.7%(47,951건) → 75.6%(86,919건) → 완료).

## 4. 최종 그래프 통계

### 노드

| 레이블 | 건수 | 비고 |
|---|---|---|
| Document | 193,122 | 실제 동기화 문서 114,930 + PARENT_OF로만 참조된 조상/스텁 노드 78,192 |
| Component | 79 | 사전 확충분(75종, 2026-10-07) 포함 |
| FailureBucket | 8 | |
| BusinessEntity | 2 | `entities` 5건 중 business_system/platform 타입만 |

### 엣지

| 관계 | 태그 | 건수 |
|---|---|---|
| PARENT_OF | EXTRACTED | 562,353 |
| HAS_COMPONENT | INFERRED | 260,219 |
| HAS_COMPONENT | EXTRACTED | 3,073 |
| REFERENCES | EXTRACTED | 2,042 |
| MENTIONS_ENTITY | EXTRACTED | 473 |
| HAS_EVIDENCE | EXTRACTED | 1 |
| SIMILAR_TO | — | 0 (failure_bucket 8건 간 유사도가 임계값 0.75 미만 — 실제로 서로 다른 장애 패턴들이라 정상) |

### §0.1 우선순위 티어 분포 — 설계 예측치와 정확히 일치

| Tier | 건수 | 교차검증 |
|---|---|---|
| 1 (부서 산출물) | 30,292 | tech_repo(2,800)+confluence_docs(5,509)+checkitem(8,989)+confluence_map(LOOKIN/TechRepo, 12,516) 합과 정확히 일치 |
| 2 (장애정보) | 15,366 | incident_reports 건수와 정확히 일치 |
| 3 (근거자료) | 69,272 | |

### Component 허브 확인 (§6 "범용어는 허브가 된다" 예측이 실측으로 확인됨)

```
SCP 39,184 / Network 28,054 / Storage 26,892 / OpenStack 20,655 /
Cluster 14,705 / Kubernetes 10,027 / Firewall 9,800 / VMware 9,605 ...
```

2026-10-07 사전 확충 작업 당시 §6에 "이 13종은 `is_hub` 플래그로 나중에
걸러낼 수 있게 해야 한다"고 적어둔 우려가, 114,930건 전체 기준 실측으로
그대로 재현됐다 — 설계 문서의 예측이 정확했음을 확인하는 동시에, `is_hub`
작업이 실제로 필요하다는 근거가 됐다.

## 5. 결론

- 코드(14개 태스크 + 7건 리뷰 수정)가 실제 운영 데이터 전량(114,930건)에
  대해 **실패 0건으로 완주**한다는 걸 확인했다.
- 멱등성(중단 후 재실행 시 정확히 스킵)이 실제로 동작함을 별도 테스트가
  아니라 이번 실행 중 우연히, 그러나 정확하게 증명했다.
- 설계 스펙의 정량적 예측(코퍼스 규모, 우선순위 티어 분포, 허브 컴포넌트)이
  전부 실측과 일치했다.
- 메인 체크아웃에는 `graph_sync_state` 테이블 추가만 반영됐고(순수 추가,
  기존 데이터 무영향), 실제 Neo4j 그래프 데이터는 이번 검증 전용 컨테이너에만
  존재한다 — 메인 스택에 그래프 서비스를 실제로 편입하는 건 이 PR이 머지된
  뒤 `docker compose up -d neo4j` + `scripts/graph_sync.sh`를 메인 스택에서
  실행하는 별도 단계다.
