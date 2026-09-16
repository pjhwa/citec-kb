"""CI-TEC-specific taxonomy for the recurring-incident dashboard: the 10
component domains CI-TEC provides RCA expertise for, plus a cross-cutting
"성능" (performance) domain, and SWIM's 최종등급 (final grade) → severity
tier mapping.

Deliberately separate from frames/extract.py's `_COMPONENT_HINTS`: those
hints serve general-purpose similar-incident matching across every
source_type (support_history included) and are tuned for that broader
job. This module exists only for CI-TEC's own 11-domain lens on SWIM
(incident_reports) data — mutating the shared hint list to fit these 11
domains would risk regressing similar-incident matching elsewhere.

2026-09-16 real-corpus validation (15,333 SWIM incident_reports, see
citec-kb conversation): Network dominates (58.8%) because SWIM is a
company-wide incident log, not CI-TEC-scoped — most of that is generic
site/line-down traffic outside CI-TEC's 10 domains. A recurring-pattern
dashboard for CI-TEC must filter to rows that match at least one of these
domains (or a CI-TEC-relevant 운영부서) before aggregating, or the numbers
are dominated by noise CI-TEC doesn't own.
"""

from __future__ import annotations

import re

# Ordered (not alphabetical) — matches app.frames.extract._COMPONENT_HINTS'
# convention: first-match-wins order only matters for readability here since
# tag_citec_domains() returns every match, not just the first.
#
# Real-corpus hit rates (15,333 SWIM incident_reports, whole-body scan,
# 2026-09-16, after adding the SCP v1/v2 rule below): Network 58.8%, 성능
# 6.8%, Database 3.8%, Middleware 3.6%, Storage 2.6%, VMware 1.6% (238),
# Windows 1.0%, Kubernetes 0.9%, OpenStack 0.4% (55), Linux 0.5%, Ceph
# 0.1%. Ceph/OpenStack/Kubernetes/Linux are thin in this company-wide
# corpus — expected, since most of their traffic isn't CI-TEC's
# domain-specific incident_reports subset to begin with.
_CITEC_DOMAIN_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bLinux\b|리눅스|\bRHEL\b|CentOS|우분투|Ubuntu|SUSE", re.I), "Linux"),
    (
        re.compile(
            r"\bWindows\s*(Server)?\b|윈도우(즈)?\s*서버|Active\s*Directory\b",
            re.I,
        ),
        "Windows",
    ),
    # SCP(Samsung Cloud Platform) v1 runs on VMware; v2 was rebuilt on
    # OpenStack (박재화, 2026-09-16). Only an EXPLICIT version marker
    # ("SCP v1"/"SCPv1"/"SCP1.0" or the v2 equivalents) resolves to a
    # domain here — bare "SCP" (~5,927/5,987 SCP mentions in the corpus,
    # 2026-09-16 count) is deliberately left untagged by this rule: 박재화
    # confirmed plenty of unqualified "SCP" incidents are ones where the
    # v1/v2 distinction genuinely doesn't matter (e.g. a network/facility
    # issue at the SCP platform level, not its hypervisor/orchestration
    # layer) — guessing v1-by-default would misattribute those to VMware.
    (re.compile(r"\bVMware\b|vSphere|\bESXi\b|vCenter|v-?[Mm]otion|\bNSX\b|SCP\s*v?1(?:\.0)?\b", re.I), "VMware"),
    # "Nuri"/"누리" is CI-TEC's internal OpenStack platform brand name (see
    # app.confluence.map_sync.MAP_SOURCE_DEFS' Openstack101 roots
    # "Nuri 운영구성"/"Nuri 운영 관련") — without it OpenStack coverage in
    # this corpus was near zero (6 hits by generic keywords alone).
    (
        re.compile(
            r"OpenStack|오픈스택|\bNova\b|\bNeutron\b|\bCinder\b|\bNuri\b|누리|SCP\s*v?2(?:\.0)?\b",
            re.I,
        ),
        "OpenStack",
    ),
    (re.compile(r"\bKubernetes\b|\bk8s\b|\bPOD\b|쿠버네티스|\bDocker\b|컨테이너", re.I), "Kubernetes"),
    (re.compile(r"\bWAS\b|WebLogic|Tomcat|JBoss|WebSphere|미들웨어|Nginx|Apache\s*(HTTP)?", re.I), "Middleware"),
    (
        re.compile(
            r"네트워크|네트웍|회선|스위치|라우터|방화벽|\bNW\b|\bBGP\b|\bVPN\b|L[234]\s*스위치|로드밸런서|\bDNS\b",
            re.I,
        ),
        "Network",
    ),
    (
        re.compile(r"스토리지|\bStorage\b|\bNAS\b|\bSAN\b|\bWEKA\b|NetApp|디스크\s*(장애|오류|Full|고장)", re.I),
        "Storage",
    ),
    (re.compile(r"\bCeph\b|세프", re.I), "Ceph"),
    (
        re.compile(
            r"\bOracle\b|\bMySQL\b|PostgreSQL|\bPostgres\b|\bMSSQL\b|SQL\s*Server|\bDB2\b"
            r"|Tibero|MariaDB|SAP\s*HANA|\bDBMS\b|\bDB\s*(Hang|다운|장애)",
            re.I,
        ),
        "Database",
    ),
    # Cross-cutting, not a component: an incident tagged 성능 is very often
    # ALSO tagged with the component it happened on (e.g. Database + 성능
    # for "DB Hang"). That overlap is intentional — see module docstring.
    (
        re.compile(
            r"성능\s*(저하|이슈|문제)|응답\s*지연|처리\s*지연|타임아웃|\btimeout\b"
            r"|과부하|부하\s*(증가|급증)|응답시간|\blatency\b|\bHang\b",
            re.I,
        ),
        "성능",
    ),
]

CITEC_DOMAINS: tuple[str, ...] = tuple(name for _, name in _CITEC_DOMAIN_HINTS)


def tag_citec_domains(text: str, title: str = "") -> list[str]:
    """All matching domains for one incident, in CITEC_DOMAINS order (not
    alphabetical, not match-count order) — same "whole body, not just
    title" scan as app.frames.extract's component hints, so an incident's
    body-text symptom/root-cause sections (■ 장애원인/■ 검토의견/...) count,
    not just its title.

    Multi-label is intentional: an incident can genuinely span more than
    one domain, and 성능 in particular is meant to co-occur with whatever
    component it happened on. A single incidental mention (e.g. "AD서버"
    inside an unrelated DNS-fix note) can tag a domain that isn't really
    the incident's main subject — this is a known coarse-grained tradeoff
    of keyword-only tagging, not a bug to chase to zero; treat single-hit
    domain tags as lower-confidence than domains matched in the title.
    """
    blob = f"{title}\n{text[:12000]}"
    return [name for pat, name in _CITEC_DOMAIN_HINTS if pat.search(blob)]


# SWIM 최종등급 → severity tier (박재화, 2026-09-16):
#   FO등급 = Failover되어 서비스 영향 없었던 장애 (무중단)
#   X등급  = 고객사 귀책 (SDS 책임 아님)
#   N등급  = 벤더 귀책
#   4등급  = 장애는 있었으나 서비스 영향 미미/없음 (경미)
#   1~3등급 = 중대장애
#
# "major"만 반복-패턴 대시보드의 1차 관심사(우리가 고쳐야 할 것). X/N등급은
# 귀책이 CI-TEC/SDS 밖이라 "반복되는 우리 문제"로 잘못 집계되면 안 되지만,
# 벤더/고객사 추세 파악용으로는 여전히 유용해 별도 tier로 보존한다(버리지
# 않음) — 대시보드 쪽에서 tier로 필터링해서 쓸 것.
_SEVERITY_TIER_MAP: dict[str, str] = {
    "1등급": "major",
    "2등급": "major",
    "3등급": "major",
    "4등급": "minor",
    "FO등급": "failover_no_impact",
    "X등급": "customer_fault",
    "N등급": "vendor_fault",
}


def classify_severity_tier(grade: str | None) -> str:
    """Maps a raw 최종등급 string to a severity tier. Unrecognized/blank
    grades (e.g. LI등급/LR등급 seen in the recent-2-year sample, or a typo)
    return "unknown" rather than guessing — surface them for a human to
    classify instead of silently misclassifying."""
    return _SEVERITY_TIER_MAP.get((grade or "").strip(), "unknown")
