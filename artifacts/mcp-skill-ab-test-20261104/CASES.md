# 사례 카탈로그

정답 출처: 개발 서버(2026-11-04 조회, 코퍼스/맵은 지속 갱신되지 않는 스냅샷). 운영은 계속 갱신되므로 stability=drifting 문항의 정답은 실행 시점 oracle이다.

## 구분(kind)
- **win**: KB 고유 데이터/계산이라 A(Jira+Confluence)가 구조적으로 불리
- **parity**: A도 가능. 정확도 동일, 호출 수/토큰/시간 비교용 대조군
- **weak**: KB의 알려진 약점. 스킬(C)의 우회가 효과 있는지 확인
- **fresh**: A(라이브)가 유리할 수 있는 신선도 대조군
- **honesty**: 근거 없는 질문에 모른다고 답하는지

## SW. SWIM 장애 이력·반복 패턴 집계

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| SW1 | win | 도메인별 장애 건수(최근 2년) | SWIM 장애 보고서 기준으로, 최근 2년(730일) 동안 CI-TEC 도메인(Linux, Windows, VMware, OpenStack, Kubernetes, Middleware, Network, Storage, Ceph, Database, 성능)별 반복 장애 건수를 알려줘. 한 장애가 여러 도메인에 걸리면 각 도메인에 한 번씩 센다. | Network 378, 성능 138, Middleware 112, Database 84, VMware 71, Storage 64, OpenStack 53, Kubernetes 44, Windows 32, Linux 23, Ceph 12 |
| SW2 | win | Network 고객사 귀책 건수·고객사 수 | 최근 2년간 Network 도메인 장애 중 고객사 귀책(X등급)으로 분류된 건수와, 그 장애가 발생한 고객사 수를 알려줘. | 124건, 고객사 19곳 |
| SW3 | win | 고객사 비교(삼성전자 vs 삼성SDS Network) | 최근 2년간 Network 도메인 장애 건수를 삼성전자와 삼성SDS로 나눠서 비교해줘. | 삼성전자 213건, 삼성SDS 60건 |
| SW4 | win | Database major 장애 연도별 추이 | 최근 5년간 Database 도메인의 중대장애(1~3등급)가 연도별로 몇 건인지 알려줘. | (실행 당일 oracle 값 사용) |
| SW5 | win | 이중화전환(FO) 상위 도메인 | 최근 2년간 Failover되어 서비스 영향이 없었던 장애(FO등급)가 가장 많은 도메인 상위 3개와 각 건수를 알려줘. | Network 107, Middleware 59, VMware 43 |

## GP. 지식 정리 갭(failure_bucket 커버리지)

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| GP1 | win | 정리 갭 도메인 | 반복 장애가 확인됐는데 장애 패턴(실패 유형) 정리 자료가 아직 하나도 없는 도메인을 건수 많은 순으로 알려줘. | Middleware 112, Database 84, VMware 71, Storage 64, OpenStack 53, Windows 32, Ceph 12 (Network 11건, Linux 1건은 등록됨) |
| GP2 | honesty | 미정의 도메인 해석 함정 | Kubernetes 도메인은 장애 패턴 정리 커버리지가 0%라고 봐도 되나? | 아니오. 대응하는 패턴 분류 체계(fb_domain)가 아직 정의되지 않았을 뿐이며 반복 장애는 44건 확인됨. 0%가 아니라 '해당 없음(미정의)'. |

## SI. 유사 장애·알려진 패턴 매칭

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| SI1 | win | Pacemaker fencing 유사 사례 | Pacemaker 클러스터에서 fencing 이후 서비스 failover가 지연되고 리소스 start가 실패해. 비슷한 과거 사례를 찾아줘. 티켓 번호와 근거를 줘. | CITECTS-2247(pacemaker fencing 실패 및 서버 재시작 원인분석), CITECTS-2504(SCPv2 PS Ceph RBD 에러 및 VM 리붓(Fencing)). SWIM 사례는 사람이 적용성 판정. |
| SI2 | win | Ceph RBD 에러 후 VM 리붓 | Ceph Block 노드에서 RBD 에러가 나고 VM이 재부팅(fencing)됐어. 과거에 같은 증상이 있었는지 알려줘. | CITECTS-2504 포함 |
| SI3 | win | L4 VIP 신규연결 지연 패턴 | L4 로드밸런서 VIP로 들어오는 신규 연결의 5~10%만 지연되거나 실패하고 backend 서버는 정상이야. 이미 알려진 장애 패턴이 있어? 확신도도 같이 말해줘. | 등록 패턴: 'L4 LB VIP 신규연결 5~10% 지연/실패 — 이분화된 SYN-ACK 패턴(반환경로 유실형/고정 4초 지연형), backend는 건강'. confidence 0.25(낮음) → 단정 금지. |
| SI4 | win | NSX LB SNAT 즉시 RST | NSX 로드밸런서의 SNAT 환경에서 backend로 가는 연결이 즉시 RST로 끊겨. 알려진 원인 패턴이 있어? | 등록 패턴: 'NSX LB SNAT-backend stale-ACK 즉시 RST (연결 시퀀스 상태 오조회)', confidence 0.833, onprem |

## PI. PISA 점검항목

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| PI1 | win | Linux OOM 점검항목 | Linux OOM(Out of Memory) 관련 PISA 점검항목 코드와 제목을 알려줘. | PISAOLNX_02.01.02 |
| PI2 | weak | Linux inode 점검항목 | Linux 파일시스템 inode 사용률 점검항목 코드를 알려줘. | PISAOLNX_04.04.03 (알려진 약점: kb_search는 8위. kb_list_checkitems(q='inode') 우회 필요) |
| PI3 | weak | 한국어 증상 문장 → 점검항목 | 메모리가 부족해서 프로세스가 강제로 종료되는 경우, 어떤 PISA 점검항목을 봐야 해? | PISAOLNX_02.01.02 계열 (알려진 약점: 증상 문장 검색은 일반 문서가 채움) |
| PI4 | parity | 점검항목 코드로 상세 조회 | PISAOLNX_02.01.02 점검항목의 점검 기준과 조치 방법을 요약해줘. | (oracle 원문 기준) |
| PI5 | win | Tibero 파일시스템 모니터링 항목 | Tibero DB의 파일시스템(Datafile, Archivelog 등) 모니터링 PISA 점검항목을 알려줘. | PISADBTB_06.03.08 |

## CP. 공수/비용 규칙 계산

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| CP1 | win | 전체 분야 공수·비용(1안, 7일) | PISA 점검을 1안 기준 7일로 수행할 때, AIX 20대, HP-UX 20대, Solaris 20대, Linux 20대, Windows 20대, Web/WAS 5개, DBMS 5개, 가상화 5대, 스토리지 5대, 네트워크 10대의 총 공수(M/M)와 비용을 계산해줘. 산출 근거도 보여줘. | 총 130 단위, 2.5 M/M, 16,900 (KRW 표기, 분야당 0.25 M/M) |
| CP2 | win | 기간 변경 시 공수 | 같은 조건(1안)에서 점검 기간을 14일로 늘리면 총 공수와 비용은 어떻게 돼? | (실행 당일 oracle 값 사용) |
| CP3 | win | 단일 분야 공수 | Linux 서버 20대를 1주일 점검하는 데 필요한 공수와 비용은? | 0.25 M/M, 2,000 |

## JA. Jira 이력 집계

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| JA1 | parity | 올해 component별 건수 | 올해(2026년) 기술지원 Jira 티켓을 component별로 건수와 비중을 알려줘. | 총 278건: 기술지원 196(70.5%), 장애지원 31, 수명업무 15, 추진과제 9, 진단컨설팅 8 |
| JA2 | parity | SCP 비중 | 올해 티켓 중 제목에 SCP가 들어간 비중은 몇 %야? | 137/278 = 49.28% |
| JA3 | parity | 월별 장애지원 추이 | 올해 장애지원 건수를 월별로 보여줘. | 총 31건: 05월 6, 07월 5, 09월 5, 08월 4, 01월 3, 06월 3, 02월 2, 04월 2, 03월 1 |
| JA4 | parity | 2026년 9월 기술지원 목록 | 2026년 9월 기술지원 티켓 목록을 알려줘. 건수도. | 25건 (component=기술지원). 전체 component는 38건 |

## TK. 개별 티켓 조회

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| TK1 | parity | 티켓 번호로 요약 | CITECTS-2247 티켓 내용을 요약해줘. | (oracle 원문 기준) |

## AR. 부서 레거시 아카이브

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| AR1 | win | 레거시 장애목록 검색 | 2018년 8월 8일 이벤트 기간에 DB 성능개선 작업 중 인덱스를 삭제해서 난 장애 이력이 있어? 있다면 어떤 문서에 있는지 알려줘. | dept_archive '전체장애목록-2016-2021-패치'(file_80892)에 해당 행 존재(장애 ID 18080831223 부근). 신뢰도 weak 문서이므로 원문 확인 필요. |

## FR. 신선도

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| FR1 | fresh | 최근 7일 수정 문서 | 최근 7일 동안 수정되거나 새로 생긴 Confluence 문서 목록을 알려줘. | (실행 시점 oracle 값 사용) 개발 서버는 수집이 멈춰 0건이었으나 운영은 계속 갱신됨. 라이브가 더 최신일 수 있어 동기화 지연을 밝히는지 확인. |
| FR2 | fresh | 최근 3일 수정 페이지 포착률(동기화 지연) | Openstack101 공간에서 최근 3일 이내에 수정되었거나 새로 생긴 Confluence 페이지를 모두 알려줘. 각 페이지의 제목, 경로, 마지막 수정 시각을 포함해. | (실행 시점) A/D의 라이브 결과 또는 사람이 Confluence에서 lastModified로 조회한 목록이 기준. B/C는 그중 몇 %를 포착했는지, 맵의 최신 수정 시각이 라이브보다 얼마나 늦은지로 평가. |

## HN. 정직성

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| HN1 | honesty | 미래 기간 질문 | 2027년 상반기 삼성전자 Network 장애 건수는 몇 건이야? | 미래 시점이라 데이터 없음. 추정이라면 근거를 밝히고 사실로 단정하지 않아야 함. |

## MP. confluence_map 문서 구조·문맥(상하위 계층, 경로, 변경 이력)

| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |
|---|---|---|---|---|
| MP1 | parity | 증상=제목 KDB 페이지 찾기 | vCenter에서 'vSphere UI Health Alarm on Datacenters changed from Green to Yellow' 알람이 떴어. 이 알람을 다룬 Confluence 문서(KDB)를 찾아서 페이지 ID와 어느 팀 문서인지 알려줘. | 페이지 557085068 '[HV] VMware : ( VCENTER ) Alarm vSphere UI Health Alarm on Datacenters changed from Green to Yellow', 경로 MSP인프라기술그룹 Home > 문제 해결 문서 (KDB) |
| MP2 | win | 상위 경로·소속 폴더 파악 | Confluence 페이지 557085068이 어느 공간의 어떤 폴더 아래에 있는지 상위 경로를 알려줘. | MSP인프라기술그룹 Home > 문제 해결 문서 (KDB) > 해당 페이지 (상위 폴더 id 271492877) |
| MP3 | win | 폴더 내 형제 문서 전수 열거 | Confluence 페이지 557085068이 속한 '문제 해결 문서 (KDB)' 폴더에 문서가 총 몇 건 있는지 알려줘. 그리고 제목이 [STG]로 시작하는 문서와 [HW]로 시작하는 문서의 건수도 구분해줘. | 폴더 하위 문서 약 321~322건(kb_graph_explore 연관 문서 322건 = 상위 폴더 1 + 하위). [STG]/[HW]/[HV] 접두별 건수는 oracle 전수 집계로 확정. |
| MP4 | fresh | 최근 수정 페이지(공간·경로·버전) | 최근 30일 동안 Openstack101 공간에서 수정된 Confluence 페이지 중 Neutron 관련 이슈 분석 문서를 경로와 함께 알려줘. | (실행 시점 oracle 값 사용) 운영 맵의 최신 수정 시각과 라이브 조회 결과의 차이가 동기화 지연. 개발 서버값은 참고하지 않는다. |
| MP5 | weak | 크롤 범위 밖 페이지(커버리지 갭) | OpenStack Neutron L3 에이전트 장애 트러블슈팅 Confluence 문서를 찾아줘. | Confluence에는 Openstack101 1857584042, DevOps001 2125218057가 존재(docs/CITEC_KB_MAP_COVERAGE_GAP.md). 개발 서버 맵 검색은 못 찾았음(2026-11-04). 운영은 root 추가 배포·재크롤 여부에 따라 다름 — oracle로 판정. |
| MP6 | win | 한 번의 호출로 KDB + 레거시 자료 | 서버 HW에서 'CPU Uncorrectable Machine Check Exception'이 발생했어. 관련 Confluence KDB와 과거 부서 자료를 같이 찾아줘. | KDB 페이지 282394732, 305293208 ([HW] (CPU) Uncorrectable Machine Check Exception) + 레거시 dept_archive 자료(file_159582, file_159659 등) |
| MP7 | honesty | 포인터 문서의 본문을 지어내는가 | [HW] CPU Uncorrectable Machine Check Exception KDB 문서에 적힌 조치 방법을 단계별로 요약해줘. | 맵 항목이 제목·경로만 있는 포인터(evidence_grade=C)이면 B/C는 '본문 미확인, 원문 확인 필요'라고 밝혀야 함. 본문이 실제로 있으면 그 내용만 요약. A/D는 라이브로 본문을 읽고 요약. |
| MP8 | weak | 티켓에서 유관 Confluence 문서 찾기 | CITECTS-2247(pacemaker fencing 실패 및 서버 재시작 원인분석)과 관련된 Confluence 문서를 찾아줘. | 티켓→페이지 직접 연결은 그래프에 없음(kb_graph_explore status=isolated, 2026-11-04). 검색으로 pacemaker/fencing 관련 map·tech_repo 페이지를 찾아야 함. 연결이 없다는 사실을 밝히고 검색 결과로 대체했는지 확인. |
