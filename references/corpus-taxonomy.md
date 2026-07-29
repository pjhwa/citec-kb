# CI-TEC KB 코퍼스 실측 Taxonomy (생성일: 2026-07-23, 대상 스냅샷: 2026-07-23 기준 `data/raw/`)

> 목적: `wiki-mcp`(`kb_search`/`kb_query`)의 `section`/`area`/`work_type` 등 필터 인자와 검색어 구성에 실제로 쓸 수 있도록,
> `data/raw/` 원문 파일을 직접 순회해서 얻은 실측 taxonomy와 엔티티/별칭 사전. `AI_AGENT_GUIDE.md`가 제시하는 필터 예시값
> (`area: os/dbms/network/cloud/storage`, `work_type: 기술지원/장애지원`)은 코퍼스 원문 필드명과 정확히 일치하지 않으므로,
> 아래 실측값을 필터/쿼리 구성 시 우선 참고할 것.

## 스캔 커버리지

| source_type | 디렉터리 | 파일 수 | 스캔 수 | 커버리지 |
|---|---|---:|---:|---|
| support_history | `data/raw/support_history/` | 2,288 | 2,288 | 100% |
| tech_repo | `data/raw/tech_repo/` | 2,709 | 2,709 | 100% |
| checkitems | `data/raw/checkitems/checkitem_list_KO_20260609.json` | 4,434 항목(단일 JSON) | 4,434 | 100% |
| tuning_ai | `data/raw/tuning_ai/` | 11 | 11 | 100% (표본이 작아 개별 나열, 집계표 생략) |
| confluence_docs | `data/raw/confluence_docs/` | 4 | 4 | 100% (표본이 작아 개별 나열) |
| vendor_docs, incident_reports | `data/raw/vendor_docs/`, `data/raw/incident_reports/` | 각 1 | 1 | 100% (건수가 1건뿐이라 taxonomy 집계 대상에서 제외) |

스캔 방법: Python 스크립트로 각 디렉터리 파일을 전수 순회하며 프론트매터/헤더 필드를 정규식으로 파싱, 값별 건수를 집계. 스크립트/원시 JSON 출력은 세션 스크래치패드에 보관.

---

## 1. Area/Component/Work_type 실측값

### 1.1 `support_history` — `Component` 필드 (건수 내림차순, 총 2,286건 필드 보유 / 2건은 필드 자체 없음)

`Component`가 `wiki-mcp`의 `work_type` 필터와 실질적으로 대응되는 유일한 실측 필드다. 코퍼스에 별도 `area`/`environment` 필드는 **존재하지 않는다** (§3 참고).

| Component (work_type 후보) | 건수 |
|---|---:|
| 기술지원 | 1,400 |
| 추진과제 | 216 |
| 장애지원 | 216 |
| 수명업무 | 165 |
| 상시업무 | 68 |
| 기획업무 | 68 |
| 진단컨설팅 | 61 |
| 역량강화 | 48 |
| 정기작업 | 42 |
| 기타(`-`, `위톡제외` 등 값 1건씩) | 2 |

참고 필드(부가 정보용, 필터로는 저활용 예상):
- `Status`: 닫힘 2,246 / 진행 중 38 / Rejected 2
- `Epic Link`: 대부분 `-`(2,176건, 미배정). 나머지는 `CITECTS-###` 형태의 상위 과제 티켓 링크(예: `CITECTS-819` 20건, `CITECTS-38` 16건) — 프로젝트/과제 단위 집계에 쓸 수 있음.

### 1.2 `tech_repo` — 프론트매터 `디렉토리` 브레드크럼 (총 2,709건 전수)

`구분`(전량 `컨플루언스`)·`공간명`(전량 동일 공간)은 필터 가치가 없다. 대신 `디렉토리` 필드의 계층 구조가 `area`/`category`에 해당하는 실질적 분류축이다.

**2단계 (대분류, `area` 후보):**

| 값 | 건수 |
|---|---:|
| 클라우드 운영 기술 | 1,827 |
| 클라우드 장애 대응 | 528 |
| 클라우드 테스트 시나리오 및 도구 | 118 |
| 교육 및 세미나 | 136 |
| 클라우드 CSP별 상품서비스 비교 | 98 |
| CI-TEC의 구름다리 | 2 |

**3단계 (중분류, 100건 이상만 개별 표시 / 미만은 "기타"로 묶되 개별 건수는 아래 각주에 보존):**

| 값 | 건수 |
|---|---:|
| ★★분야별 기술 자료★★ | 1,139 |
| 이중화 테스트 표준 시나리오 | 536 |
| 표준 장애대응 / SOP (상세 명령어 레벨) | 280 |
| 이슈 분석 지원 Tool 모음 | 98 (100 미만이지만 경계값이라 유지 표시) |
| 기술 세미나 | 122 |
| Hang 대응 방안 | 137 |
| 기타(아래 각주, 100건 미만 34개 항목 합계 397건) | 397 |

기타 세부 내역(값 → 건수, 전부 보존): 3. 표준 템플릿 39, 04. Storage 비교 33, 2. 부하 테스트 툴 43, 4. 기술 스택 별 검증 사례 32, ★★ SCP 기술 자료 ★★ 35, 권고 패치 공지(PRB) 55, 19. Multi AZ 비교 10, 08. Database 비교 8, 01. CSP Overview 비교 5, 표준 모니터링 항목(MRB) 11, PIXEL(엑셀기반 PISA진단) 19, 장애 사례 분석 8, 기술 동영상(CIC TechTube) 12, ★★ DRCC 기술 자료 ★★ 28, 03. Compute 비교 10, 09. Security 3, 벤더협의체(Old) 3, 17. DR(Disaster Recovery) 비교 4, 07. Container 비교 3, 12. Management 비교 2, 02. IaaS 비교 1, 10. AI Service 비교 2, 05. Networking 비교 1, 06. PaaS 비교 1, 13. API 서비스 비교 1, 14. Reference 비교 1, 16. Hybrid + Multicloud 서비스 2, 18. Edge Computing 비교 2, 15. Migration 비교 7, CSP별 기술 자료 2, 사업부 현장기술 교육 1, 분야별 진단 교육 자료 1, 이중화 인정 기술 1, 이중화 아키텍처 개선(Failover Failure Zero) 1, 데이터센터 장애 대응 1, 시스템 성능 저하 사례기고문 2건(각 1), 1. 성능/가용성/안정성 검증 개요 4.

**`라벨` 필드:** 105건이 `temp`(임시), 38건이 `kb-troubleshooting-article`(정식 트러블슈팅 문서 마킹). 나머지는 자유 태그 나열(쉼표구분, 예: `scp, bm, iscsi`)로 문서당 1회성 값이 많아 집계 가치가 낮음 — 검색어 확장용 키워드 풀로는 활용 가능.

### 1.3 `checkitems` (PISA, JSON 4,434건 전수)

`Area` 필드가 코퍼스 내에서 유일하게 명확한 기술 도메인 taxonomy다. `wiki-mcp`의 `area` 인자(`Linux`, `Oracle`, `Windows` 등 예시)가 실제로 이 필드에 대응한다.

**`Area` (건수 내림차순, 전체 61종):**

| Area | 건수 | | Area | 건수 |
|---|---:|---|---|---:|
| Oracle | 286 | | Tibero | 97 |
| VMware | 206 | | Apache | 75 |
| Linux | 180 | | Wildfly | 70 |
| AIX | 162 | | Veritas | 68 |
| DB2 | 156 | | ST_General | 67 |
| HANA | 101 | | Kafka | 64 |
| HP-UX / Solaris | 122 / 122 | | SKE_K8S | 61 |
| MySQL | 85 | | Symmetrix | 50 |
| Windows | 94 | | MSA | 50 |
| SDS_PaaS | 88 | | Gx00_Fx00 | 50 |
| Weblogic | 79 | | VNX_Unity | 52 |
| JBoss | 81 | | TMAX | 52 |
| Tomcat | 74 | | HPE_Comware / NetApp_Cluster / HPE_AOS-CX | 59 각 |
| JEUS | 72 | | Cisco_NXOS | 89 |
| OpenStack | 72 | | Isilon | 43 |
| NSX_T | 74 | | Brocade | 42 |
| NSX_V | 71 | | HNAS | 41 |
| SQL Server | 83 | | MDS | 46 |
| 3PAR | 71 | | NetApp_E_EF | 45 |
| PostgreSQL | 70 | | WebtoB | 44 |
| Cisco_IOS | 69 | | IIS | 54 |

전체 61개 값(예: L2/L3 23, L4/L7 19, Firewall 19, Arista 55, Extreme 58, Alteon 32, F5 32, Citrix 31, A10 34, Secui 24, NetApp 62, ZS 53, VSP 53, Ceph 50, Fortinet 25, F_OTHERS 2, S_OTHERS 2 등)은 원본 스캔 JSON에 보존되어 있으며, 위 표는 상위/대표값 요약이다. **누락 없음** — 전체 표는 필요 시 스크립트 재실행으로 재생성 가능.

**`Category_1` (점검 성격 분류, `work_type`에 가까움):**

| 값 | 건수 |
|---|---:|
| 구성 | 1,209 |
| 운영 | 979 |
| 가용성 | 860 |
| 결함 및 오류 | 715 |
| 성능 및 용량 | 671 |

**`중요도`:** 중 2,064 / 상 1,248 / 하 1,122 — 리스크 기반 필터링에 활용 가능.

### 1.4 `tuning_ai` (11건, 집계표 대신 전체 나열)

`ISS-YYYYMMDD-NNN.md` 형식 8건 + `PING-2026-05-28.md` 1건 + `wiki_sample_*` 접두 샘플 파일 2건. 건수가 작아 `area`/`work_type` 통계적 집계는 무의미. 파일명 패턴 자체가 `이슈번호-일자` 식별자 역할.

---

## 2. 엔티티/별칭 사전

기존 `data/seeds/entities/core.json`(5종: 모니모, SCP, Redis, Oracle, GRO)·`data/seeds/lexicon/core.json`(10종)이 이미 있으므로, 아래는 그 값을 실측 코퍼스로 검증하고 **신규 후보를 추가**한 것이다. "등장 건수"는 `support_history` 제목(2,286건) 및 `tech_repo` 프론트매터 제목/디렉토리 마지막 세그먼트에서 문서당 1회만 센 값이며, **본문 전체 검색 기준이 아니라 제목/메타 기준 하한값**이다(스키마 편차 메모 §3 참고).

| 별칭 | 정식명칭 | 동의어/변형 | 등장 건수(제목 기준) | 대표 문서 | 근거 |
|---|---|---|---:|---|---|
| SCP | Samsung Cloud Platform | 삼성클라우드 | 375 (support_history 제목) + 9 (tech_repo) | CITECTS-100, CITECTS-1007 | seed 기존값, 코퍼스 재확인 |
| FRB | Failure Review Board | - | 103 (support_history 제목) | CITECTS-1159, CITECTS-1222 | 본문에 명시적 정의: "FRB(Failure Review Board)" |
| PRB | Patch Review Board | - | (라벨/디렉토리 55건 별도 집계, §1.2) | tech_repo "권고 패치 공지(PRB)" | 본문에 명시적 정의 |
| MRB | Monitoring Review Board | - | (디렉토리 11건, §1.2 "표준 모니터링 항목(MRB)") | - | 본문에 명시적 정의 |
| SRB | Support Review Board (w/ Vendors) | - | 미집계(제목 노출 없음) | 정의만 확인 | 본문에 명시적 정의, 문서 수는 별도 확인 필요 |
| Lookin | PISA(Professional Infrastructure System Assessment) 방법론 기반 진단 체크리스트 자동화 시스템 | - | 50 (support_history 제목) | CITECTS-1005, CITECTS-1042 | 본문에 명시적 정의 |
| PISA | Professional Infrastructure System Assessment | - | checkitems 전체(4,434건)가 이 방법론 산출물 | checkitem_list_KO | 본문에 명시적 정의, `PISAOLNX_*` 코드 패턴과 일치 |
| 모니모 | 모니모(삼성 금융 앱, 비즈니스 시스템) | monimo, Monimo | 41 (support_history 제목) | CITECTS-1044, CITECTS-1118 | seed 기존값, 코퍼스 재확인 |
| Oracle | Oracle DBMS | 오라클 | 50 (support_history) + 23 (tech_repo) | CITECTS-1027, CITECTS-1041 | seed 기존값, 코퍼스 재확인 |
| Redis | Redis | 레디스 | checkitems Area 34건, 제목 노출은 낮음(상세 미집계) | - | seed 기존값, 코퍼스 재확인(checkitems Area로 재확인) |
| BM | Bare Metal(베어메탈, 물리 서버) | - | 50 (support_history 제목) | CITECTS-1021, CITECTS-1030 | 본문 문맥: "BM Edge 클러스터" — 추정이지만 근거 높음 |
| ProbeONE | 사내 분석/대시보드 도구명으로 추정 | - | 56 (support_history 제목) | CITECTS-1000, CITECTS-1001 | **추정** — 제목 외 본문 정의 미발견, 기능 설명("분석 요약/Dashboard 화면 추가")만 확인 |
| GSAT | 삼성 그룹 직무적성검사(일반 공지 정보, 코퍼스 내 정의 없음) | - | 24 (support_history 제목) | CITECTS-1284, CITECTS-1459 | **추정** — 코퍼스는 "GSAT 예비소집/본시험 현장 대기" 문맥만 제공, 정식 정의는 외부 지식 |
| N-ERP | 정식명칭 미확인(사내 ERP 시스템으로 추정) | - | 23 (support_history 제목) | CITECTS-1324, CITECTS-1394 | **추정** — 본문에 "N-ERP 운영 안정화" 문맥만 있고 전개어 없음. 확인 필요 |
| SDI | 정식명칭 미확인(프로젝트/사업장 코드로 추정, checkitems에도 위치 태그로 등장) | - | 48 (support_history 제목) | CITECTS-1054, CITECTS-1081 | **추정** — "[SDI]천안 NW장비" 등 지역/사업 태그로 쓰임. Samsung SDI 여부 코퍼스만으로 확정 불가 |
| Knox | 정식명칭 미확인 | - | 29 (support_history 제목) | CITECTS-1100, CITECTS-1534 | **추정** — 본문 근거 "Knox Meeting" 1건뿐, 정의 문장 미발견. Samsung Knox 여부 확인 필요 |
| NAS | Network Attached Storage | - | 34 (support_history 제목) | CITECTS-1079, CITECTS-1280 | 업계 표준 약어, 코퍼스 문맥과 일치(스토리지 관련 제목) |
| LB | Load Balancer | - | 32 (support_history 제목) | CITECTS-1014, CITECTS-1022 | 업계 표준 약어 |
| NSX | VMware NSX (네트워크 가상화) | - | 22 (support_history 제목) | CITECTS-1005, CITECTS-1045 | checkitems Area(NSX_V/NSX_T)와 일치 |
| PureScale | IBM DB2 pureScale | - | 10 (tech_repo) | confluence_410625006 등 | 업계 표준 제품명, checkitems DB2 Area와 연계 |
| Greenplum / gpdb | Greenplum(분산 DW, GPDB) | gpdb | 3 (tech_repo) | confluence_784421517 등 | 본문에 "Greenplum" 명시 |
| IRS | 정식명칭 미확인 | - | 미확정(자동 추출 근거 문서 오검출로 재확인 필요) | - | **추정 불가 — 근거 부족, 별도 재조사 필요로 표기만 남김** |

---

## 3. 스키마 편차 메모

- **`area`/`environment`/`work_type` 필드는 raw 문서에 그대로 존재하지 않는다.** `AI_AGENT_GUIDE.md` §4.2가 제시하는 `area`(`os`/`dbms`/`network`/`cloud`/`storage`)·`environment`(`csp`)·`work_type`(`기술지원`/`장애지원`) 값은 API/색인 계층에서 **파생·정규화된 필터**로 보이며, 코퍼스 원문 헤더 필드명과 1:1 대응하지 않는다. 실측상 가장 근접한 원문 필드는:
  - `work_type` ↔ `support_history`의 `Component` (§1.1)
  - `area` ↔ `checkitems`의 `Area` (§1.3, 61종 기술 도메인), 또는 `tech_repo`의 `디렉토리` 2단계 breadcrumb (§1.2)
  - `environment` 대응 필드는 raw 문서 어디에도 명시적으로 없음 (seed entity의 `env_hints`처럼 별도 매핑 테이블로 추정하는 방식으로 보임)
- `support_history`는 8개 헤더 필드(`Issue Key`, `Epic Link`, `Component`, `Assignee`, `Status`, `Created`, `Updated`, `Resolved`)로 스키마가 매우 일관적이다(2,286/2,288건 필드 완비, 2건은 헤더 파싱 실패 — 별도 포맷 이슈로 재확인 필요).
- `tech_repo`는 프론트매터 필드명이 한국어(`구분`, `공간명`, `디렉토리`, `Page ID`, `제목`, `URL`, `최종수정일`, `라벨`)로 `support_history`(영문)와 전혀 다른 스키마를 쓴다. `제목` 필드는 **대다수 문서에서 빈 값**이라 실질적 제목은 본문 첫 H1 또는 `디렉토리` 마지막 세그먼트에서 유추해야 한다(신뢰도 낮음 — 일부 문서는 원본 로그/명령어 덤프가 본문 최상단에 위치해 H1 추출이 왜곡됨, 예: `confluence_2114380990.md`).
- `checkitems`는 md 파일이 아닌 **단일 JSON**(+ xls 원본)이며, 필드명이 영문/한글 혼용(`Area`, `Category`, `Category_1`, `Subcategory`, `Subject`, `점검방법`, `점검기준`, `중요도` 등)이다. `support_history`/`tech_repo`와 완전히 다른 스키마.
- `confluence_docs`(4건)는 `tech_repo`와 동일한 프론트매터 스키마를 쓰지만 별도 디렉터리로 분리되어 있다 — `source_type` 값이 나뉘는 이유는 코퍼스만으로 확인 불가(운영 히스토리 문제로 추정).
- 엔티티 등장 건수는 **제목/메타 필드 기준**이라 본문 전용 언급은 반영되지 않음 — 실사용 검색량과 다를 수 있어 하한값으로만 참고할 것.

---

## 갱신 주기

코퍼스는 매주 월요일 아침 갱신됨. 본 문서는 1회성 산출물이 아니라 **월요일 업로드 이후 재실행을 기본**으로 한다. 재실행 시 위 "생성일/대상 스냅샷"과 표 값을 갱신할 것.
