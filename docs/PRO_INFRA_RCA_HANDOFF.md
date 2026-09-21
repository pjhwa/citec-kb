# citec-kb `fb_domain` 신설 처리 완료 — pro-infra-rca 연동 가이드

| 항목 | 내용 |
|---|---|
| 수신 | 민연홍 (yeonhong.min@samsungsds.com) |
| 발신 | 박재화 (citec-kb 저장소 소유) |
| 처리일 | 2026-09-21 |
| 원 요청 | `~/tmp/citec-kb-failure-domain.md` (2026-09-17) |
| 대상 저장소 | `code.sdsdev.co.kr/jooksan/citec-kb` |

## 0. 처리 결과 요약

요청하신 `fb_domain` 5개(`dbms`/`linux`/`virtualization`/`middleware`/`storage`)를
`references/failure-bucket-domains.md`와 `apps/api/app/taxonomy.py`(`_FB_DOMAIN_TO_CORPUS_DOMAIN`)에
등록 완료했습니다. 아래 3가지 검증도 통과합니다(§6).

**원 요청 §5 판단 요청 3건 — 결정 내용:**

1. **`linux` 이름** → 1안(`linux`) 그대로 확정. `unix`로 바꾸지 않습니다.
2. **OpenStack 소속** → `virtualization`에 포함(문서 제안대로). `cloud` fb_domain은 이번에 신설하지
   않습니다.
3. **기존 값 3개(`network`/`cluster`/`windows`) 소유 플러그인 열에 `pro-infra-rca(공용)` 병기** →
   병기하지 않습니다. `references/failure-bucket-domains.md` 표는 원래 3개 소유 플러그인
   (packet-capture-rca/pacemaker-tools/windows-tools)만 남아 있고, pro-infra-rca는 이 3개 값을
   공용으로 그대로 쓰시면 됩니다(동작에는 영향 없음).

아래 §1~§6은 스킬 쪽 `references/citec-kb-integration.md`로 그대로 옮겨 쓰실 수 있게 채운
내용입니다(`docs/FAILURE_BUCKET_PLUGIN_GUIDE.md` §7 스켈레톤 형식). 이 커버 표(§0)만 빼고
복사하시면 됩니다.

---

## 1. `fb_domain` 값 — pro-infra-rca가 쓰는 8개 중 6개

각 값의 설명·대표 신호는 citec-kb `references/failure-bucket-domains.md`가 유일한 정본입니다 —
여기서는 목록만 인용합니다(정본과 다른 목록을 스킬 쪽에 또 유지하지 않기 위해).

| fb_domain | 코퍼스 domain | 비고 |
|---|---|---|
| `dbms` | `dbms` | 신설 |
| `linux` | `os` | 신설. AIX 포함. Pacemaker/Corosync HA 자체는 `cluster`로 분리 |
| `virtualization` | `virtualization` | 신설. OpenStack 포함 |
| `middleware` | `middleware` | 신설 |
| `storage` | `storage` | 신설 |
| `network` | `network` | 공용 — packet-capture-rca와 동일 레지스트리 |
| `windows` | `os` | 공용 — SQL AlwaysOn은 근본원인이 WSFC 계층일 때만 |
| `cluster` | `os` | 공용 — Pacemaker/Corosync 펜싱·쿼럼·DRBD·iSCSI 공유스토리지 |

정본은 citec-kb `references/failure-bucket-domains.md`입니다 — 이 표가 그것과 어긋나면 그 파일이
맞습니다.

## 2. `fb_domain` 결정 규칙 — 트리거가 아니라 근본원인의 계층

**인시던트를 처음 촉발한 증상이 아니라, 근본원인이 실제로 놓인 계층을 기준으로 고릅니다.**
(원 요청서 §1의 규칙 그대로.)

- SQL Server AlwaysOn 페일오버라도:
  - 근본원인이 엔진 내부 메모리 grant 경합(Error 8645) → `dbms`
  - 근본원인이 WSFC 하트비트·쿼럼 손실 → `windows`
- Oracle RAC 노드 evicted라도:
  - 근본원인이 CRS/네트워크 하트비트 → `cluster` 또는 `network`(신호에 따라)
  - 근본원인이 인스턴스 내부(세마포어/래치 경합) → `dbms`
- 클러스터가 스토리지 이상에 *반응*해 펜싱했다면 → `cluster`(반응 계층), 근본원인이 어레이·경로
  자체면 → `storage`(원인 계층)

**기존 공용 값을 먼저 확인합니다.** 새 진단 영역처럼 보여도 이미 있는 값(`network`/`cluster`/
`windows`)이 실제로 근본원인 계층과 일치하면 새 값 대신 그 값을 씁니다 — `dbms`/`linux`/
`virtualization`/`middleware`/`storage` 5개는 기존 3개로 커버되지 않는 계층에서만 씁니다.

**경계가 애매하면 등록을 보류하고 조건부로 보고합니다** (`docs/FAILURE_BUCKET_PLUGIN_GUIDE.md`
§1-5 원칙 그대로) — 근본원인 계층이 실제로 확정되지 않았는데 `fb_domain`을 추정해서 채우지
않습니다.

## 3. 도메인별 `evidence_ref` 형식

| 진단 영역 | 원자료 | 형식 예시 |
|---|---|---|
| dbms (SQL Server) | ERRORLOG/XEL | `log:sqlserver/ERRORLOG.1#L882` |
| dbms (Oracle) | alert.log / CRS trc / AWR | `log:oracle/alert_orcl.log#L4021` |
| linux | syslog/journald/dmesg/sar/kdump vmcore | `log:node1/messages#L1290` |
| virtualization (VMware) | vmkernel.log/vobd.log | `log:esxi01/vmkernel.log#L5510` |
| virtualization (OpenStack) | nova/neutron/cinder oslo.log | `log:nova-compute.log#L2210` |
| middleware | catalina.out / BEA diagnostic / Nginx error.log | `log:catalina.out#L340` |
| storage | multipath/iSCSI/Ceph 로그 | `log:ceph/osd.12.log#L88` |
| 지원 티켓/장애번호 | — | `CITECTS-2481` / `swim:INC00123456` |

자유 서술("확인함" 등)은 citec-kb가 거부합니다 — `docs/FAILURE_BUCKET_PLUGIN_GUIDE.md` §4를
그대로 따릅니다.

## 4. 이 스킬의 phase에 매핑

`docs/FAILURE_BUCKET_PLUGIN_GUIDE.md` §3의 일반 템플릿을 그대로 쓰되, `fb_domain`은 §2 규칙에
따라 매 인시던트마다 확정한 값을 넣습니다(스킬 전체에 고정값 하나를 쓰지 않습니다). phase
이름은 pro-infra-rca 자체 워크플로(인테이크 → 도메인 확정 → 원자료 분석 → 보고)에 맞춰
채웁니다.

- **인테이크 종료 시점** — 도메인이 아직 확정 전이면 증상만으로 `kb_search`/`kb_similar_incident`로
  넓게 먼저 훑습니다. 도메인이 확정되면 `kb_match_failure_bucket(fb_domain=<확정값>, ...)`로
  좁힙니다.
- **분석 진행 중** — 새 신호를 확인할 때마다 `kb_match_failure_bucket`을 갱신된
  `observed_signals`로 재호출합니다(`fb_domain`은 그 사이 바뀔 수 있습니다 — 근본원인 계층이
  분석 중 재확인되면 그에 맞춰 조정합니다).
- **보고 후** — 정확히 하나: `kb_register_failure_bucket(..., fb_domain=<확정값>,
  source_plugin="pro-infra-rca@<version>")` 또는 `kb_refine_failure_bucket(confirm=true/false)`.

## 5. 하지 말아야 할 것

`docs/FAILURE_BUCKET_PLUGIN_GUIDE.md` §6에 더해:

- 근본원인 계층이 아니라 **처음 보고된 증상/트리거**로 `fb_domain`을 고르는 것 (§2 위반).
- 근본원인이 명확하지 않은데 `fb_domain`을 추정해서 등록하는 것 — 보류하거나 조건부로 보고합니다.
- 이미 커버되는 계층(`network`/`cluster`/`windows`)인데 새 값을 만들어 쓰는 것.
- `dbms`에 Redis Enterprise를 등록할 때 키워드 규칙(`Redis`→`middleware`)과 충돌한다고 착각하는
  것 — `infer_domain()`은 `fb_domain` 명시 매핑을 키워드 규칙보다 먼저 적용하므로 충돌 없습니다
  (`apps/api/app/taxonomy.py` 참고).

## 6. 처리 후 확인 방법 — 이미 확인됨

wiki-mcp가 연결된 세션에서 아래 3가지를 확인했습니다:

1. `kb_register_failure_bucket(..., fb_domain="dbms", ...)` 응답에 "없는 새 값" 경고가 붙지 않음 ✅
2. `kb_list_failure_buckets(fb_domain="storage")`가 오류 없이 (버킷 0건이라도) 빈 목록 반환 ✅
3. 등록된 `dbms` 버킷이 `kb_search(area="dbms", section="failure_bucket")`에 노출 ✅

첫 실사용 등록은 요청서에서 말씀하신 대로 SQL Server AlwaysOn 케이스(Error 8645 → failover)로,
`source_plugin="pro-infra-rca@1.32.0"`으로 등록해 주시면 됩니다.

## 7. 참고

- citec-kb 정본: `references/failure-bucket-domains.md` · `docs/FAILURE_BUCKET_PLUGIN_GUIDE.md`
  (§2-1이 여러 도메인을 다루는 플러그인의 `fb_domain` 결정 규칙을 부서 공용 지침으로 명문화)
- 선례 구현: `packet-capture-rca` 스킬의 `references/citec-kb-integration.md`
- 문의: 박재화 (citec-kb 저장소 소유)
