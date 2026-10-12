# 지식그래프 정합성 수정 — 실패 주입/검증 원시 로그 (2026-10-12)

scratch Neo4j + scratch Postgres에서 실행. 상세 설명은 docs/features/graph-evidence.md.

## generation_mint_and_skip.sh
```
=== run 1: no marker yet -> mint + sync ===
2026-10-12 13:59:00,412 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Document) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_3a9f7910 FOR (e:Document) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:00,413 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_9ccdf9b2 FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:FailureBucket) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:00,414 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_713a57df FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Component) REQUIRE n.canonical_name IS UNIQUE'
2026-10-12 13:59:00,416 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_b77cbcfe FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:BusinessEntity) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:00,417 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_e2524304 FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:UnresolvedPage) REQUIRE n.external_id IS UNIQUE'
{"run_id": "4ec7cfdc-3557-439d-af78-50edd84f6296", "generation": "2026-10-12.3.8d1f4893", "documents": {"synced": 84, "skipped": 0, "failed": 0}, "failure_buckets": {"synced": 0, "failed": 0}, "hub_recompute_ok": true}
=== run 2: same marker -> all skip ===
2026-10-12 13:59:04,509 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Document) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_3a9f7910 FOR (e:Document) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:04,510 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_9ccdf9b2 FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:FailureBucket) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:04,513 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_713a57df FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Component) REQUIRE n.canonical_name IS UNIQUE'
2026-10-12 13:59:04,514 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_b77cbcfe FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:BusinessEntity) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:04,515 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_e2524304 FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:UnresolvedPage) REQUIRE n.external_id IS UNIQUE'
{"run_id": "2843c9da-e8f3-48c9-ae5c-dd19d215c446", "generation": "2026-10-12.3.8d1f4893", "documents": {"synced": 0, "skipped": 84, "failed": 0}, "failure_buckets": {"synced": 0, "failed": 0}, "hub_recompute_ok": true}
=== delete marker, run 3: new mint -> full resync again ===
2026-10-12 13:59:07,325 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Document) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_3a9f7910 FOR (e:Document) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:07,326 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_9ccdf9b2 FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:FailureBucket) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:07,328 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_713a57df FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Component) REQUIRE n.canonical_name IS UNIQUE'
2026-10-12 13:59:07,329 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_b77cbcfe FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:BusinessEntity) REQUIRE n.id IS UNIQUE'
2026-10-12 13:59:07,330 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_e2524304 FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:UnresolvedPage) REQUIRE n.external_id IS UNIQUE'
{"run_id": "50d99d1a-0b06-4e57-b105-ef73576bbb23", "generation": "2026-10-12.3.2da8c2ed", "documents": {"synced": 84, "skipped": 0, "failed": 0}, "failure_buckets": {"synced": 0, "failed": 0}, "hub_recompute_ok": true}
```

## hub_recompute_failure_injection.py
```
2026-10-12 14:09:36,120 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Document) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_3a9f7910 FOR (e:Document) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE'
2026-10-12 14:09:36,122 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_9ccdf9b2 FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:FailureBucket) REQUIRE n.id IS UNIQUE'
2026-10-12 14:09:36,124 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_713a57df FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Component) REQUIRE n.canonical_name IS UNIQUE'
2026-10-12 14:09:36,125 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_b77cbcfe FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:BusinessEntity) REQUIRE n.id IS UNIQUE'
2026-10-12 14:09:36,126 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_e2524304 FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:UnresolvedPage) REQUIRE n.external_id IS UNIQUE'
2026-10-12 14:09:36,911 ERROR citec.graph.sync_cli recompute_hub_flags failed
Traceback (most recent call last):
  File "/home/citec/dev/citec-kb/apps/api/app/graph/sync_cli.py", line 140, in main
    recompute_hub_flags(client)
  File "/home/citec/dev/citec-kb/apps/api/../../scripts/verify/hub_recompute_failure_injection.py", line 12, in boom
    raise RuntimeError("simulated hub recompute failure (verify script)")
RuntimeError: simulated hub recompute failure (verify script)
exit code: 1
{"run_id": "2e7210ac-4eb9-4621-a494-2b1a30b41d2e", "generation": "2026-10-12.3.2da8c2ed", "documents": {"synced": 0, "skipped": 84, "failed": 0}, "failure_buckets": {"synced": 0, "failed": 0}, "hub_recompute_ok": false}
```

## crash_mid_run_injection.py
```
2026-10-12 14:09:38,278 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Document) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_3a9f7910 FOR (e:Document) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE'
2026-10-12 14:09:38,280 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_9ccdf9b2 FOR (e:FailureBucket) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:FailureBucket) REQUIRE n.id IS UNIQUE'
2026-10-12 14:09:38,281 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_713a57df FOR (e:Component) REQUIRE (e.canonical_name) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:Component) REQUIRE n.canonical_name IS UNIQUE'
2026-10-12 14:09:38,282 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_b77cbcfe FOR (e:BusinessEntity) REQUIRE (e.id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:BusinessEntity) REQUIRE n.id IS UNIQUE'
2026-10-12 14:09:38,283 INFO neo4j.notifications Received notification from DBMS server: {severity: INFORMATION} {code: Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists} {category: SCHEMA} {title: `CREATE CONSTRAINT IF NOT EXISTS FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` has no effect.} {description: `CONSTRAINT constraint_e2524304 FOR (e:UnresolvedPage) REQUIRE (e.external_id) IS UNIQUE` already exists.} {position: None} for query: 'CREATE CONSTRAINT IF NOT EXISTS FOR (n:UnresolvedPage) REQUIRE n.external_id IS UNIQUE'
2026-10-12 14:09:38,305 ERROR citec.graph.sync_cli sync_cli run failed unexpectedly
Traceback (most recent call last):
  File "/home/citec/dev/citec-kb/apps/api/app/graph/sync_cli.py", line 113, in main
    result = sync_document(
             ^^^^^^^^^^^^^^
  File "/home/citec/dev/citec-kb/apps/api/../../scripts/verify/crash_mid_run_injection.py", line 17, in boom
    raise RuntimeError("simulated catastrophic failure (verify script)")
RuntimeError: simulated catastrophic failure (verify script)
last run status: failed
```

## lexicon_matcher_diff_2000docs.py (old vs new 매처, 실제 코퍼스 2000건 샘플)
```
sample size: 2000

3PAR                     15     15   1.00
A10                      16     16   1.00
AIX                      38     39   1.03
Alteon                    7      7   1.00
Apache                   25     26   1.04
Arista                    8      9   1.12
BFD                       1      1   1.00
Brocade                   3      3   1.00
Ceph                     88     90   1.02
Cisco_IOS                 3      4   1.33
Cisco_NXOS                5      7   1.40
Citrix                   12     12   1.00
Cluster                 248    282   1.14
DB2                      20     20   1.00
ESXi                     74     76   1.03
Extreme                  10     10   1.00
F5                       14     14   1.00
Firewall                158    168   1.06
Fortinet                 17     17   1.00
GRO                       2      3   1.50
HANA                     47     48   1.02
HAProxy                  18     19   1.06
HNAS                      3      3   1.00
HP-UX                    21     21   1.00
HPE_AOS-CX                3      3   1.00
HPE_Comware               1      1   1.00
IIS                      11     11   1.00
Isilon                    6      6   1.00
JBoss                    19     19   1.00
JEUS                     13     15   1.15
Kafka                    23     24   1.04
Kubernetes              153    158   1.03
L2/L3                     7     10   1.43
L4/L7                     5      5   1.00
Linux                   139    149   1.07
MDS                      13     13   1.00
MSA                      10     13   1.30
MTU                      23     24   1.04
MySQL                    61     62   1.02
NFS                      48     48   1.00
NSX_T                    37     41   1.11
NSX_V                     7      7   1.00
NetApp                   66     68   1.03
NetApp_Cluster            2      3   1.50
NetApp_E_EF               3      3   1.00
Network                 426    453   1.06
Nginx                    27     28   1.04
OpenStack               381    384   1.01
Oracle                  131    138   1.05
PostgreSQL               55     55   1.00
Redis                    31     31   1.00
SCP                     670    735   1.10
SDS_PaaS                  7     11   1.57
SKE_K8S                   4      5   1.25
SQL Server               19     42   2.21  <-- FLAG
Secui                    16     16   1.00
Solaris                  15     15   1.00
Storage                 409    434   1.06
Symmetrix                 4      4   1.00
TMAX                      3      4   1.33
Tibero                   27     27   1.00
Tomcat                   23     23   1.00
VMware                  151    159   1.05
VNX_Unity                 2      2   1.00
VSP                       5      6   1.20
Veritas                  10     10   1.00
Weblogic                 15     16   1.07
WebtoB                   10     11   1.10
Wildfly                  12     12   1.00
Windows                 126    132   1.05
ZS                        7      7   1.00
filesystem               85    109   1.28
hang                     95     75   0.79
monimo                   33     36   1.09
timeout                  67     74   1.10

--- flagged: context around first new-only match ---

=== SQL Server ===
  [confluence_map:241683065] ...EPAS   　 4 (PaaS) MySQL   　 5 (PaaS) MS SQL Server    　 6 (PaaS) Tibero   　 7 (PaaS) Redis...
  [confluence_map:2336268586] ... Linux 10   Windows 3   DBMS Oracle 2   SQL Server (Azure) 3   Redis 1   미들웨어 Tomcat 2   J...
  [confluence_map:2491855512] ...racle Linux Oracle Linux 7 ~ 9.x 　   DB SQL Server Windows 용 2016 ~ 2025 (x86_32, x86_64) ...
  [confluence_docs:1885050530] ...racle Database의 경쟁력: IBM DB2, Microsoft SQL Server와의 비교   시장 지위 및 경쟁 환경   Oracle Database는...
```

## 포커스 체크 before/after (전체 재백필 승인 전, dev `citec-kb-neo4j-1`)

대상: TGW 3문서(595824602/604365026/604365046), `confluence_docs:1117787168`,
구 샘플의 유령 소유자 `tech_repo:1153334044`.

실행:
```
docker compose exec -T api python -m app.graph.sync_cli \
  --source-ids "confluence_map:595824602,confluence_map:604365026,confluence_map:604365046,confluence_docs:1117787168,tech_repo:1153334044" \
  -v
```
결과: `{"documents": {"synced": 5, "skipped": 0, "failed": 0}, "failure_buckets": {"synced": 8, "failed": 0}, "hub_recompute_ok": true}`
(`--generation` 미지정 — dev Neo4j에 마커가 없어 새로 발급됨: `2026-10-12.3.d2f3f9ca`)

Before:
- `tech_repo:1153334044`의 unstamped outgoing PARENT_OF(유령) 수: **6**
- TGW 페이지(`confluence_map:604365046`)의 HAS_COMPONENT: `Network, SCP, Firewall, hang`(hang은 오탐)

After:
- `tech_repo:1153334044`의 unstamped outgoing PARENT_OF(유령) 수: **0**
- TGW 페이지의 HAS_COMPONENT: `Network, SCP, Firewall`(hang 사라짐)
- TGW 페이지의 직계 부모: `confluence_map:604365026`(1건, 정확)
- 루트(595824602)에서 내려가는 방향: `595824602 -[:PARENT_OF]-> 604365026`(1건, 정확)
- GraphMeta 마커: `2026-10-12.3.d2f3f9ca`(저장 확인됨)

교차 소스 external_id 충돌 조회:
```sql
SELECT external_id, array_agg(DISTINCT source_type) AS source_types
FROM documents
GROUP BY external_id
HAVING count(DISTINCT source_type) > 1
   AND NOT (array_agg(DISTINCT source_type)::text[] <@ ARRAY['confluence_map','confluence_docs','tech_repo']);
```
결과: **0 rows**.

## Pre-run 실제 kb_graph_explore MCP 호출 (코드 배포 후, 전체 재백필 전 — stale "hang" 확인)

`mcp__wiki-mcp__kb_graph_explore(anchor_type="document", anchor_value="confluence_map:604365046")`
(이 세션의 실제 MCP 호출, python 스크립트 아님):

```
as_of: 2026-10-12
해석된 앵커: confluence_map:604365046

연관 문서 (50건+):
  - [1hop/PARENT_OF] 20. 성능(상품별) 산출물 (confluence_map, evidence_grade=C) id=confluence_map:604365026
  (... 중략 ...)

연관 컴포넌트 (4건):
  - [1hop/HAS_COMPONENT] Network
  - [1hop/HAS_COMPONENT] SCP
  - [1hop/HAS_COMPONENT] Firewall
  - [1hop/HAS_COMPONENT] hang

연관 과거 장애 (0건):
```

`hang`은 오탐(이 문서 본문에 "hang"/"hung"/"행"/"시스템 멈춤" 전부 없음) — 이 시점
dev Neo4j는 여전히 수정 전 코드(한 글자 한글 변형어 "행"이 "진행"/"수행" 등에서
오탐)로 동기화된 과거 데이터였기 때문. 코드는 이미 수정·배포됐지만, 그 문서가
새 generation으로 재동기화되기 전까지는 그래프에 과거 결과가 남아있다 — G03의
"재동기화해야만 교체된다" 설계와 일치하는 정상 동작. 전체 재백필 완료 후
동일 호출을 재실행해 `hang`이 사라지는지 확인한다(아래 "포커스 체크" 섹션에서
`tech_repo:1153334044` 등 5개 문서만 재동기화한 결과로는 이미 사라짐을 확인했고,
전체 재백필 완료 후 전체 코퍼스 기준으로 다시 확인한다).

## 전체 재백필 완료 결과 (2026-10-12 05:00:52 ~ 07:02:01 UTC, ~2h1m)

```
{"run_id": "074c710a-4f59-4528-a5ff-af14d0f3702a", "generation": "2026-10-12.3.d2f3f9ca",
 "documents": {"synced": 114925, "skipped": 5, "failed": 0},
 "failure_buckets": {"synced": 8, "failed": 0}, "hub_recompute_ok": true}
```
`graph_runs.status = completed`.

### Stamp check (레거시 unstamped edge 잔존 여부)
```
MATCH ()-[r]->() WHERE r.extractor_version IS NULL OR r.extractor_version <> '2026-10-12.3'
RETURN type(r), count(*)
```
결과: **빈 리스트** — 전체 코퍼스에 구 버전/unstamped edge가 하나도 남지 않음.

### 고립/잔존 노드
- `source_type IS NULL`인 Document(= 수정 전 raw-id 유령): **827개**, 전부
  완전히 고립(어떤 edge도 없음) — 참조하는 쪽이 전부 재동기화되며 자연히
  끊어졌다. 백업 후 삭제는 별도 승인 대상(삭제하지 않음).
- `UnresolvedPage`(새 설계의 정상 미해결 참조 표현): **50개**.
- Neo4j `Document`(source_type NOT NULL) 수 == PG `documents` 전체 행 수:
  **114930 == 114930**, PG에서 사라진 문서가 Neo4j에 "정상 Document"로
  남아있는 경우: **0건**.

### Hub 재확인
`Network`/`SCP`/`Storage`/`Linux` 모두 `is_hub=true`. 전체 hub 목록(13개):
Ceph, Cluster, Firewall, Kubernetes, Linux, Network, OpenStack, Oracle, SCP,
Storage, VMware, Windows, timeout — 실측 degree 기준(Phase 1의 하드코딩
13종 목록이 SCP/Kubernetes를 놓쳤던 문제가 근본적으로 해결됨을 재확인).

### G10 적용 (api/mcp 재시작 후, `--source-ids`로 버킷 재동기화)
```
{"documents": {"synced": 0, "skipped": 1, "failed": 0}, "failure_buckets": {"synced": 8, "failed": 0},
 "evidence_reasons": {"resolved": 2, "external_artifact": 6, "legacy_missing": 4,
                       "target_not_ingested": 0, "unresolved_reference": 0}}
```
Pacemaker 버킷(`6ea28d59-...`) REFERENCES 확인:
```
MATCH (b:FailureBucket {id:"6ea28d59-e09b-4eb2-a42e-cef46d0a9d3f"})-[:REFERENCES]->(d:Document)
RETURN d.id, d.title
-> confluence_map:2542097469, "2026-09-16 SCP v2 수원 DBaaS 장애 분석: 원인·영향·복구 및 재발 방지"
```
핸드오프가 명시한 "분석 원문"과 정확히 일치.

## G08 실제 MCP 라이브 트레이스 — 방향/경로 표시 검증 (2026-10-12, api+mcp 재시작 후)

`mcp__wiki-mcp__kb_graph_explore(anchor_type="document", anchor_value="confluence_map:595824602")`

예측과 정확히 일치:
- `338017591`(부모): `[1hop ↑PARENT_OF]`
- `604365026`(자식): `[1hop ↓PARENT_OF]`
- `327519070`(조부모): `[2hop ↑PARENT_OF confluence_map:338017591 ↑PARENT_OF]`
- `338017596`(형제): `[2hop ↑PARENT_OF confluence_map:338017591 ↓PARENT_OF]`
- `604365046`(TGW, 손자): `[2hop ↓PARENT_OF confluence_map:604365026 ↓PARENT_OF]`

`status: found`가 첫 줄에 기계가 읽을 수 있는 형태로 출력됨. 컴포넌트 결과도
방향 표시(`→HAS_COMPONENT`) 포함.

## G08 collapse — 실측 (component anchor "NetApp", 2026-10-12, api/mcp 재시작 후)

- `documents_total`: collapse 전 7,745 -> collapse 후 **7,058** (687건 접힘).
- 실제 NetApp 2hop 결과 안에서 `documents_offset=100`부터 수십 건의
  confluence_docs/confluence_map 쌍이 즉시 발견됨(예:
  `confluence_docs:1008130713` -> `copies: ["confluence_map:1008130713"]`).
  `_EN` 접미사(checkitem) 문서는 전혀 섞이지 않음(collapsible 소스가 아님).
- `document` 앵커 재현: `confluence_map:970662798`로 걸면
  `anchor.resolved_id == "tech_repo:970662798"`(tier 1 대표) — G01 resolver와
  G08 collapse가 같은 대표를 고름(일치 확인).
- 타이밍(재측정, api/mcp 재시작 후): NetApp component 1.9~2.26초(이전
  1.8~2.0초와 동급 — enrichment 쿼리를 JSONB 두 키만 선택하도록 줄인 효과로
  collapse 로직이 추가됐음에도 유의한 회귀 없음), symptom_text 1.8~2.13초,
  document(595824602) 0.09~0.12초. 전부 MCP 30초 타임아웃에 전혀 근접하지 않음.
