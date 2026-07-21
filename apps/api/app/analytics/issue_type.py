"""Rule-based issue-type labels for support tickets (no LLM).

Jira Component is work category (기술지원/장애지원/…), not the *kind of issue*.
This module classifies title+body into operational issue types such as
성능이슈, 설정/구성, 접속불가, …
"""

from __future__ import annotations

import re
from typing import Optional

# Ordered: first match wins. Prefer concrete symptoms over generic work words.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "성능이슈",
        re.compile(
            r"성능|슬로우|slow\s*query|응답\s*지연|서비스\s*지연|접속\s*지연|"
            r"지연\s*이슈|latency|timeout|time\s*out|타임\s*아웃|"
            r"IOWAIT|iowait|부하\s*테스트|부하/성능|CPU\s*high|memory\s*leak|"
            r"메모리\s*누수|Connection\s*Pool|Connection\s*Leak|PGA\s*메모리|"
            r"Long\s*Query|SQL\s*튜닝|쿼리\s*튜닝|성능\s*개선|성능\s*테스트|"
            r"성능\s*저하|perf(?:ormance)?",
            re.I,
        ),
    ),
    (
        "접속불가",
        re.compile(
            r"접속\s*불가|접속불가|로그인\s*불가|통신\s*불가|ping\s*fail|"
            r"서비스\s*불가|접근\s*불가|연결\s*불가|call\s*fail|"
            r"홈페이지\s*접속|원앱\s*접속|포털\s*접속",
            re.I,
        ),
    ),
    (
        "서비스장애/다운",
        re.compile(
            r"(?<!예방을 위한 )(?<!예방 )장애|"
            r"\bFRB\b|서비스\s*중단|다운\s*발생|service\s*down|\bdown\b|"
            r"\bCrash\b|Panic|hang|Hung|OOM|리부팅|재부팅|reboot|fencing|"
            r"부팅\s*불가|failover|Fail\s*over|기동\s*불가|비정상\s*종료|"
            r"노드\s*Down|OSD\s*전체\s*DOWN|Instance\s*Crash",
            re.I,
        ),
    ),
    (
        "오류/버그",
        re.compile(
            r"오류|에러|error|bug|버그|exception|런타임\s*error|"
            r"Runtime\s*error|실패\s*이슈|실패\s*건|호출\s*오류|통신\s*오류|"
            r"업로드\s*시\s*에러|간헐적\s*오류",
            re.I,
        ),
    ),
    (
        "패치/업그레이드",
        re.compile(
            r"패치|업그레이드|Upgrade|버전\s*결정|버전\s*업|"
            r"버그패치|정기\s*업데이트|OneFS\s*업그레이드|"
            r"스위치OS\s*업그레이드|Windows\s*Server\s*update",
            re.I,
        ),
    ),
    (
        "설정/구성",
        re.compile(
            r"설정\s*값|설정\s*오류|구성\s*오류|config(?:uration)?|"
            r"파라미터|세션\s*설정|방화벽\s*설정|인증서\s*업데이트|"
            r"타입\s*변경|표준화|nf_conntrack|수집주기|"
            r"Feature\s*Request|설정\s*가이드|구성에\s*대한\s*설정",
            re.I,
        ),
    ),
    (
        "네트워크",
        re.compile(
            r"네트워크|N/?W\b|NSX|방화벽|BGP|DNS|LB\b|로드\s*밸런|"
            r"스위치|multicast|패킷|ACL|WAF|VPN|TLS\s*이벤트|"
            r"세션\s*비정상|RST\s*발생|통신\s*경로|BM\s*Edge|L3\s*스위치|"
            r"Loop\s*장애|무선\s*네트워크",
            re.I,
        ),
    ),
    (
        "스토리지/용량",
        re.compile(
            r"스토리지|storage|디스크|disk|Ceph|NAS|SAN|볼륨|Volume|"
            r"용량|증설|vSAN|PowerScale|Object\s*Storage|RBD|"
            r"I/?O\s*중단|OSD|파일\s*스토리지|NFS|eNAS|HNAS",
            re.I,
        ),
    ),
    (
        "DB/데이터",
        re.compile(
            r"\bDB\b|DBMS|Oracle|MySQL|MSSQL|MS-SQL|Postgre|Greenplum|GPDB|"
            r"HANA|Exadata|OGG|SQL|쿼리|Replication|DBaaS|DB\s*접속|"
            r"통합DB|DB\s*서버|DB\s*성능",
            re.I,
        ),
    ),
    (
        "백업/DR",
        re.compile(
            r"백업|backup|복구|restore|\bDR\b|DRCC|vProtect|"
            r"이중화|HA\s*링크",
            re.I,
        ),
    ),
    (
        "보안/인증",
        re.compile(
            r"보안|인증서|SSL|TLS|암호화|취약|CVE|권한\s*설정|CSAP|"
            r"SecureBoot|Defender|위협|CTEM|SECaaS|비밀번호|패스워드|"
            r"계정\s*권한|FIDO",
            re.I,
        ),
    ),
    (
        "구축/전환/마이그레이션",
        re.compile(
            r"구축|전환|마이그레이션|migration|이관|이전\s*지원|"
            r"U2L|클라우드\s*전환|신규\s*구성|설치\s*용|설치용|"
            r"데이터\s*이관|온보딩|오픈\s*후|프로젝트\s*지원",
            re.I,
        ),
    ),
    (
        "점검/진단",
        re.compile(
            r"점검|진단|컨설팅|인프라\s*진단|정기\s*보안점검|"
            r"Lookin\s*수행|진단\s*결과|영향도\s*검증",
            re.I,
        ),
    ),
    (
        "모니터링/도구",
        re.compile(
            r"모니터링|Lookin|ProbeONE|PerfONE|PIXEL|SAR|SOS\s*Analyzer|"
            r"CI-TEC\s*Tools|Dashboard|대시보드|알람|node_exporter|"
            r"vRops|관제",
            re.I,
        ),
    ),
    (
        "문의/검토/가이드",
        re.compile(
            r"문의|가이드|검토|협의|방안|기술\s*측면|가능\s*여부|"
            r"스펙\s*검토|CAB|제안|확인\s*요청|공유",
            re.I,
        ),
    ),
    (
        "행정/교육/기타업무",
        re.compile(
            r"GSAT|공채|채용|세미나|교육|인턴|직무적합성|자산\s*실사|"
            r"유지보수|재계약|순환재택|팀기획|그룹과제|그룹\d+|"
            r"벤더협의|계약|입찰|PPISA|PISA\s*비\s*정기|테스트\s*기술지원",
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
    """Return a single issue-type label for a support ticket."""
    title_s = (title or "").strip()
    body_s = (body or "").strip()[: max(0, body_limit)]
    # Title first for ranking quality; body as fallback for sparse titles
    blob = f"{title_s}\n{body_s}"
    if not blob.strip():
        return "기타"
    for label, pat in _RULES:
        if pat.search(title_s):
            return label
    for label, pat in _RULES:
        if pat.search(body_s):
            return label
    return "기타"


def issue_type_labels() -> list[str]:
    return [label for label, _ in _RULES] + ["기타"]
