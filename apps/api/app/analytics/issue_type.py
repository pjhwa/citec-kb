"""Rule-based issue-type labels for support tickets (no LLM).

Jira Component is work category (기술지원/장애지원/…), not the *kind of issue*.
This module classifies title+body into fine-grained operational issue types.

Priority: first matching rule wins. Rules are ordered from specific symptoms
to broader domains, then process/admin work.
"""

from __future__ import annotations

import re
from typing import Optional

# (label, pattern) — first match wins.
# Keep labels short for UI buckets; ~30 types covering the support corpus.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    # ── 1. Performance & capacity symptoms ──────────────────────────
    (
        "타임아웃",
        re.compile(
            r"timeout|time\s*out|타임\s*아웃|TimeOut|timed?\s*out|"
            r"연결\s*시간\s*초과|응답\s*시간\s*초과",
            re.I,
        ),
    ),
    (
        "리소스고갈",
        re.compile(
            r"CPU\s*high|CPU\s*100|CPU\s*사용|IOWAIT|iowait|"
            r"memory\s*leak|메모리\s*누수|메모리\s*증가|PGA\s*메모리|"
            r"Connection\s*Pool|Connection\s*Leak|conntrack|"
            r"nf_conntrack|OOM|out\s*of\s*memory|스왑|swap\s*full|"
            r"디스크\s*풀|disk\s*full|inode|용량\s*부족|리소스\s*부족",
            re.I,
        ),
    ),
    (
        "성능저하/지연",
        re.compile(
            r"성능\s*이슈|성능\s*저하|성능\s*문제|응답\s*지연|서비스\s*지연|"
            r"접속\s*지연|통신\s*지연|처리\s*지연|슬로우|slow\s*query|"
            r"latency|지연\s*이슈|지연\s*장애|DB\s*성능|성능\s*지연|"
            r"느려|느림|병목|throughput",
            re.I,
        ),
    ),
    (
        "성능테스트/튜닝",
        re.compile(
            r"성능\s*테스트|부하\s*테스트|부하/성능|성능\s*개선|"
            r"SQL\s*튜닝|쿼리\s*튜닝|Long\s*Query|튜닝\s*지원|"
            r"perf(?:ormance)?\s*test|벤치마크|Compaction",
            re.I,
        ),
    ),
    # ── 2. Availability / outage symptoms ───────────────────────────
    (
        "로그인/인증불가",
        re.compile(
            r"로그인\s*불가|log\s*in\s*fail|AD\s*로그인|인증\s*실패|"
            r"SSO\s*실패|계정\s*잠금|비밀번호\s*오류|패스워드\s*오류",
            re.I,
        ),
    ),
    (
        "접속불가",
        re.compile(
            r"접속\s*불가|접속불가|접근\s*불가|연결\s*불가|통신\s*불가|"
            r"서비스\s*불가|ping\s*fail|call\s*fail|"
            r"홈페이지\s*접속|원앱\s*접속|포털\s*접속|"
            r"외부\s*인터넷\s*통신\s*불가|대외\s*접속",
            re.I,
        ),
    ),
    # Equipment domain before generic "세션 비정상"/Crash
    (
        "LB/로드밸런서",
        re.compile(
            r"\bLB\b|로드\s*밸런|Load\s*Balancer|A10\s*LB|"
            r"F5\b|HAProxy|L4\s*스위치|L7\s*LB|in-line\s*구성",
            re.I,
        ),
    ),
    (
        "방화벽/보안GW",
        re.compile(
            r"방화벽|FortiGate|WAF|SECaaS|보안\s*GW|보안게이트|"
            r"IPS|IDS|UTM|vE방화벽",
            re.I,
        ),
    ),
    (
        "DNS/이름해석",
        re.compile(
            r"\bDNS\b|Domain\s*Name|도메인\s*네임|이름\s*해석|"
            r"DNS\s*업데이트|루트\s*도메인",
            re.I,
        ),
    ),
    (
        "통신/연동오류",
        re.compile(
            r"통신\s*오류|호출\s*오류|연동\s*오류|내부통신|"
            r"API\s*호출\s*오류|서비스\s*호출오류|DNS와\s*통신|"
            r"세션\s*비정상|RST\s*발생|패킷\s*Drop|패킷\s*드롭",
            re.I,
        ),
    ),
    (
        "Failover/클러스터",
        re.compile(
            r"failover|Fail\s*over|FailOver|클러스터\s*장애|"
            r"CRS\s*기동|HA\s*장애|Heart\s*beat|스플릿\s*브레인|"
            r"Cluster\s*Failover|(?:CRS|클러스터|cluster).{0,24}기동\s*불가",
            re.I,
        ),
    ),
    (
        "시스템Crash/Hang",
        re.compile(
            r"\bCrash\b|\bPanic\b|kernel\s*panic|커널\s*패닉|"
            r"\bhang\b|\bHung\b|OS\s*행|노드\s*Hang|"
            r"리부팅|재부팅|\breboot\b|fencing|부팅\s*불가|"
            r"(?:서버|프로세스|서비스|OS|노드)\s*비정상\s*종료|"
            r"반복적\s*reboot|Instance\s*Crash|OSD\s*전체\s*DOWN|노드\s*Down",
            re.I,
        ),
    ),
    (
        "서비스장애/FRB",
        re.compile(
            r"\bFRB\b|(?<!예방을 위한 )(?<!예방\s)(?<!예방 )장애|"
            r"서비스\s*중단|service\s*down|(?<![A-Za-z])down(?![A-Za-z])|"
            r"다운\s*발생|장애\s*분석|장애\s*지원|장애\s*TF|"
            r"장애\s*관련|주요시스템\s*장애",
            re.I,
        ),
    ),
    # ── 3. Errors / bugs ────────────────────────────────────────────
    (
        "소프트웨어버그",
        re.compile(
            r"\(Bug\)|\bbug\b|버그|버그패치|CSCvh|CVE-\d|"
            r"Runtime\s*error|런타임\s*error|exception|"
            r"소프트웨어\s*결함|벤더\s*버그",
            re.I,
        ),
    ),
    (
        "작업/명령실패",
        re.compile(
            r"실패\s*이슈|실패\s*건|수행\s*실패|업로드\s*시\s*에러|"
            r"백업\s*실패|설치\s*실패|배포\s*실패|명령어\s*수행\s*실패|"
            r"간헐적\s*오류|에러\s*발생|오류\s*발생|error\s*발생",
            re.I,
        ),
    ),
    (
        "일반오류",
        re.compile(
            r"오류|에러|\berror\b|(?<![A-Za-z])ERR(?![A-Za-z])",
            re.I,
        ),
    ),
    # ── 4. Change / config (before broad "스위치/네트워크") ──────────
    (
        "패치/업그레이드",
        re.compile(
            r"패치|업그레이드|Upgrade|버전\s*결정|버전\s*업|"
            r"정기\s*업데이트|OneFS\s*업그레이드|"
            r"스위치OS\s*업그레이드|Windows\s*Server\s*update|"
            r"\bPRB\b|커스텀\s*이미지|ROMMON\s*업그레이드",
            re.I,
        ),
    ),
    (
        "설정오류/파라미터",
        re.compile(
            r"설정\s*오류|설정\s*값|파라미터\s*오류|잘못된\s*설정|"
            r"misconfig|config\s*error|nf_conntrack_max|"
            r"타입\s*변경|Controller\s*타입",
            re.I,
        ),
    ),
    (
        "구성/표준화",
        re.compile(
            r"구성\s*오류|config(?:uration)?|파라미터|세션\s*설정|"
            r"방화벽\s*설정|표준화|수집주기|Feature\s*Request|"
            r"설정\s*가이드|구성에\s*대한|신규\s*구성에\s*대한|"
            r"IT표준화|Golden\s*ROMMON",
            re.I,
        ),
    ),
    # ── 5. Network domain (broader) ─────────────────────────────────
    (
        "무선/원격접속",
        re.compile(
            r"무선\s*네트워크|VDI|원격\s*접속|VPN|RDP|"
            r"점프\s*서버|배스천",
            re.I,
        ),
    ),
    (
        "네트워크일반",
        re.compile(
            r"네트워크|N/?W\b|NSX|BGP|스위치|multicast|패킷|"
            r"ACL|NIC|MTU|VXLAN|VLAN|라우팅|세션수|"
            r"통신\s*경로|BM\s*Edge|L3\s*스위치|Loop\s*장애|"
            r"서버팜\s*네트워크|네트워크\s*구조|네트워크\s*이슈|"
            r"네트워크\s*스위치",
            re.I,
        ),
    ),
    # ── 6. Storage / backup / DR ────────────────────────────────────
    (
        "스토리지IO장애",
        re.compile(
            r"I/?O\s*중단|disk\s*offline|디스크\s*offline|"
            r"NLM\s*Lock|RBD\s*에러|OSD\s|Ceph\s*Crash|"
            r"스토리지\s*문제|Object\s*Storage\s*문제|"
            r"파일\s*스토리지.*불가|NAS.*중단|vSAN.*장애",
            re.I,
        ),
    ),
    (
        "스토리지용량/증설",
        re.compile(
            r"스토리지\s*증설|용량\s*증설|디스크\s*증설|볼륨\s*증설|"
            r"(?:스토리지|디스크|볼륨|vSAN).{0,16}증설|"
            r"용량\s*산정|사이징|스토리지\s*확장",
            re.I,
        ),
    ),
    (
        "스토리지일반",
        re.compile(
            r"스토리지|storage|디스크|\bdisk\b|Ceph|NAS|SAN|볼륨|"
            r"Volume|vSAN|PowerScale|Object\s*Storage|RBD|"
            r"파일\s*스토리지|NFS|eNAS|HNAS|OneFS",
            re.I,
        ),
    ),
    (
        "백업실패/복구",
        re.compile(
            r"백업\s*실패|backup\s*fail|복구|restore|"
            r"백업\s*솔루션|vProtect|백업\s*전용",
            re.I,
        ),
    ),
    (
        "DR/이중화",
        re.compile(
            r"\bDR\b|DRCC|DR\s*ISP|이중화|HA\s*링크|"
            r"Replication\s*중단|재해\s*복구",
            re.I,
        ),
    ),
    # ── 6. Database ─────────────────────────────────────────────────
    (
        "DB성능/튜닝",
        re.compile(
            r"DB\s*성능|DBMS\s*성능|SQL\s*튜닝|쿼리\s*튜닝|"
            r"Long\s*Query|PGA|Exadata.*성능|DB\s*서비스\s*성능",
            re.I,
        ),
    ),
    (
        "DB장애/접속",
        re.compile(
            r"DB\s*접속\s*불가|DB\s*서버\s*장애|DBMS\s*장애|"
            r"DB\s*Connection|Oracle.*장애|MySQL.*장애|"
            r"HANA.*장애|MSSQL.*장애|Instance\s*Crash.*DB",
            re.I,
        ),
    ),
    (
        "DB구축/이관",
        re.compile(
            r"DB\s*추가구축|DBMS\s*선정|데이터\s*마이그레이션|"
            r"DB\s*이관|통합DB|DBaaS|DB\s*구축|OGG\s*버전",
            re.I,
        ),
    ),
    (
        "DB일반",
        re.compile(
            r"\bDB\b|DBMS|Oracle|MySQL|MSSQL|MS-SQL|Postgre|"
            r"Greenplum|GPDB|HANA|Exadata|OGG|\bSQL\b|쿼리|"
            r"Replication|DBaaS",
            re.I,
        ),
    ),
    # ── 7. Security ─────────────────────────────────────────────────
    (
        "인증서/SSL/TLS",
        re.compile(
            r"인증서|SSL|TLS|SecureBoot|인증서\s*만료|"
            r"인증서\s*갱신|인증서\s*업데이트|k8s\s*SSL",
            re.I,
        ),
    ),
    (
        "암호화/권한",
        re.compile(
            r"암호화|컬럼암호화|권한\s*설정|계정\s*권한|"
            r"FIDO|비밀번호|패스워드|접근\s*권한",
            re.I,
        ),
    ),
    (
        "보안점검/취약점",
        re.compile(
            r"보안점검|취약|CVE|CTEM|위협센싱|CSAP|"
            r"보안\s*진단|보안\s*솔루션|Defender|"
            r"정기\s*보안|보안/네트웍",
            re.I,
        ),
    ),
    # ── 8. Lifecycle (patch/config already matched earlier) ─────────
    (
        "클라우드전환/마이그레이션",
        re.compile(
            r"클라우드\s*전환|SCP\s*전환|마이그레이션|migration|"
            r"이관|U2L|Gen1→SCP|데이터\s*이관|DC\s*이전|"
            r"1host1VM\s*전환|온프레.*전환",
            re.I,
        ),
    ),
    (
        "인프라구축",
        re.compile(
            r"인프라\s*구축|시스템\s*구축|클라우드\s*구축|"
            r"신규\s*구축|추가\s*구축|구축\s*지원|구축\s*제안|"
            r"설치용|설치\s*지원|온보딩|서버\s*구축",
            re.I,
        ),
    ),
    # ── 9. Ops process ──────────────────────────────────────────────
    (
        "점검/진단/컨설팅",
        re.compile(
            r"인프라\s*진단|진단\s*컨설팅|진단\s*결과|정기\s*점검|"
            r"(?<!적성)점검|(?<!적성)진단|컨설팅|"
            r"영향도\s*검증|Lookin\s*수행|진단항목",
            re.I,
        ),
    ),
    (
        "모니터링/관제",
        re.compile(
            r"모니터링\s*적용|시스템\s*모니터링|관제|"
            r"알람\s*미발생|알람\s*발생|node_exporter|vRops|"
            r"수집\s*모니터링|DB\s*모니터링",
            re.I,
        ),
    ),
    (
        "도구/플랫폼개발",
        re.compile(
            r"Lookin|ProbeONE|PerfONE|PIXEL|CI-TEC\s*Tools|"
            r"SAR|SOS\s*Analyzer|Analyzer\s*개발|Analyzer\s*기능|"
            r"Dashboard|대시보드|기능\s*추가|기능\s*구현|"
            r"기능\s*검증|Openstack\s*Generic|"
            r"Log\s*Analyzer|AI\s*Summary",
            re.I,
        ),
    ),
    (
        "기술문의/가이드",
        re.compile(
            r"기술\s*가이드|기술가이드|문의|확인\s*요청|"
            r"가능\s*여부\s*확인|스펙\s*검토|Open\s*API\s*서비스\s*확인|"
            r"사용\s*방법|매뉴얼",
            re.I,
        ),
    ),
    (
        "검토/협의/방안",
        re.compile(
            r"검토|협의|방안|기술\s*측면|CAB\b|제안|"
            r"벤더협의|활성화\s*방안|근본적\s*방안|"
            r"활용\s*방안|개선\s*방안",
            re.I,
        ),
    ),
    # ── 10. Admin / non-incident ────────────────────────────────────
    (
        "교육/세미나",
        re.compile(
            r"세미나|교육|역량강화|워크숍|워크샵|기술\s*세미나|"
            r"클라우드기술세미나",
            re.I,
        ),
    ),
    (
        "채용/평가",
        re.compile(
            r"GSAT|공채|채용|인턴|직무적합성|적성진단|"
            r"평가위원|영문지원서",
            re.I,
        ),
    ),
    (
        "계약/유지보수",
        re.compile(
            r"유지보수|재계약|계약|입찰|단가|"
            r"Renewal(?!\s*DC)|PPISA\s*Renewal",
            re.I,
        ),
    ),
    (
        "행정/기획",
        re.compile(
            r"자산\s*실사|순환재택|팀기획|그룹과제|그룹\d+|"
            r"PISA\s*비\s*정기|PPISA|테스트\s*기술지원|"
            r"진척상황|운영\s*리듬",
            re.I,
        ),
    ),
]


def classify_issue_type(
    title: Optional[str] = None,
    body: Optional[str] = None,
    *,
    body_limit: int = 2500,
) -> str:
    """Return a single fine-grained issue-type label for a support ticket."""
    title_s = (title or "").strip()
    body_s = (body or "").strip()[: max(0, body_limit)]
    if not title_s and not body_s:
        return "기타"

    # Prefer title match (higher signal); fall back to body.
    for label, pat in _RULES:
        if title_s and pat.search(title_s):
            return label
    for label, pat in _RULES:
        if body_s and pat.search(body_s):
            return label
    return "기타"


def issue_type_labels() -> list[str]:
    """Stable ordered list of labels (rules order + 기타)."""
    return [label for label, _ in _RULES] + ["기타"]
