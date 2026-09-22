"""One-time seed data for the confluence_map source registry.

Used only by alembic/versions/20260922_0007_confluence_map_sources_seed.py
(to upsert the 12 originally-hardcoded spaces into the `sources` table) and
by test_confluence_map_source_seed.py. Nothing at runtime imports this —
see app.confluence.map_sync.get_source_defs(), which reads the registry
from the DB. Do not add new spaces here; use POST /v1/confluence-map/sources
instead (see apps/api/app/routers/confluence_map.py).
"""

from __future__ import annotations

from typing import Any

# Curated roots per space — narrow subtrees only (incident/tech-support
# relevant), same "don't ingest whole space" policy as sync.py's
# CONFLUENCE_DOCS_ROOTS/TECHREPO_ROOTS. Whole-space crawl would also pull
# other teams' 손익 관리/팀 KPI/해외법인 업무/Personal Space/999. FreeSpace —
# not CI-TEC's business and pure noise for incident lookup.
#
# Approved 2026-09-16 (박재화) after sampling each space's actual content via
# confluence-mcp getChild (see conversation) — content shape varies a lot:
# CLDENG's KDB root alone has 100s of pages titled as literal symptom/error
# strings (e.g. "[HW] CPU Uncorrectable Machine Check Exception"), so a
# title-only index is already high-value there; others (Openstack101,
# EMCloud) are much thinner.
#
# sysops (MSP인프라운영팀) intentionally excluded for now: its home subtree
# only has 2 children (과제 진행 현황/손익 관리), no issue/KDB-shaped content
# as of this date. Add a roots entry here once that space grows one.
#
# 2026-09-16 (박재화, 2차 결정): LOOKIN/TechRepo/ServiceExcellenceTeam/
# ICLOUDUT는 "나머지 부분만 1회성 마이그레이션"이 아니라 **전체 공간을
# confluence_map으로 상시 포함**하고 매일 증분 갱신한다 — root은 해당 공간의
# 홈/최상위 페이지 하나(= 전체가 그 밑에 있으므로 사실상 "전체 공간" 크롤).
# LOOKIN/TechRepo는 이미 confluence_docs/tech_repo(4+5개 서브트리)로 본문
#전체가 들어간 페이지도 있는데, 그 페이지들도 이 맵에 C등급 포인터로 중복
# 등록된다 — 검색 랭킹엔 무해(A등급이 항상 우선)하고, 매번 실시간이라 예전
# "1회성 마이그레이션 스냅샷이 stale해지는" 문제 자체가 없어진다(예:
# 스냅샷과 실제 사이 326건 격차 같은 것). 홈페이지 자기 자신은 `ancestor=`
# CQL 특성상 결과에 안 잡힌다(자손만 반환) — confluence_docs/tech_repo도
# 동일한 특성이라 새로운 제약이 아니다.
SEED_MAP_SOURCE_DEFS: dict[str, dict[str, Any]] = {
    "confluence_map_lookin": {
        "space_key": "LOOKIN",
        "space_name": "CI-TEC",
        "roots": {
            "222532724": "전체 공간 (CI-TEC Home)",
        },
    },
    "confluence_map_techrepo": {
        "space_key": "TechRepo",
        "space_name": "[클라우드] 테크리포(Tech-Repository)",
        "roots": {
            "31951116": "전체 공간 (테크리포 Home)",
        },
    },
    "confluence_map_serviceexcellenceteam": {
        "space_key": "ServiceExcellenceTeam",
        "space_name": "서비스일류화팀",
        "roots": {
            "2001257717": "전체 공간 (서비스일류화팀 Home)",
        },
    },
    "confluence_map_icloudut": {
        "space_key": "ICLOUDUT",
        "space_name": "Cloud Umbrella Team",
        "roots": {
            "230553963": "전체 공간 (SCP Umbrella Team Home)",
        },
    },
    "confluence_map_devops001": {
        "space_key": "DevOps001",
        "space_name": "SCP인프라운영팀",
        "roots": {
            "601879661": "005. 이슈/문제/KDB/SOP",
            "468085983": "006. SCP CASE study",
            "370644108": "★★ SCP (SCP SRE + SCP NW Share) ★★",
        },
        # Added 2026-09-21: lives under 개인별 업무공간 > 이정수, outside all
        # 3 curated roots above. Registered as a single-page seed rather
        # than adding "개인별 업무공간" as a 4th root — that folder is one
        # person's personal workspace, not team-curated content, and
        # crawling it whole would sweep in unrelated personal pages.
        "explicit_pages": {
            "1475349722": "GitHub 아이피 변경 적용 (개인 업무공간 seed: 이정수 > R+ 거점서버 인수인계)",
        },
    },
    "confluence_map_openstack101": {
        "space_key": "Openstack101",
        "space_name": "OPENSTACK PLATFORM",
        "roots": {
            "1148203906": "knowledge base",
            "2318695720": "9. 팀 ISSUE 관리",
            "1176274225": "Nuri 운영구성",
            "1204002143": "Nuri 운영 관련",
        },
        # Added 2026-09-21: lives under Personal Space > 정지원, outside all
        # 4 curated roots above — same rationale as confluence_map_devops001's
        # explicit_pages.
        "explicit_pages": {
            "2254661271": "Squid Proxy 전환 (개인 업무공간 seed: 정지원 > 2026~Techops)",
        },
    },
    "confluence_map_cldeng": {
        "space_key": "CLDENG",
        "space_name": "MSP인프라기술그룹",
        "roots": {
            "289411381": "KB/SOP 검색",
            "271492877": "문제 해결 문서 (KDB)",
            "271779122": "004. 이슈,장애 관리",
            "271779763": "006. 기술&자동화",
            "271786535": "007. HW 운영 (서버HW/가상화/스토리지)",
            "331893132": "009. 통합백업",
            "561906785": "119. (★)DR 전환 및 비상가동 절차(★)",
        },
    },
    "confluence_map_dftrts": {
        "space_key": "DFTRTS",
        "space_name": "기술검증그룹",
        "roots": {
            "145822951": "기술자료",
            "966044388": "인프라설계검증",
            "958711159": "하드웨어분석",
        },
    },
    "confluence_map_emcloud": {
        "space_key": "EMCloud",
        "space_name": "통합Managed Infra서비스팀",
        "roots": {
            "271034968": "문제 해결 문서",
            "184265267": "7. Cloud Engineering(Shared Service)",
        },
    },
    # Added 2026-09-21 to close a gap a GitHub-connectivity KB question
    # exposed: these 3 spaces had zero MAP_SOURCE_DEFS coverage, and the
    # relevant pages are individually-known-important rather than whole
    # curated subtrees worth crawling — see explicit_pages below and
    # docs/superpowers/plans/2026-09-21-confluence-map-handoff-gap-closure.md.
    "confluence_map_spc": {
        "space_key": "SPC",
        "space_name": "Coding References",
        "roots": {},
        "explicit_pages": {
            "155680474": "SDS GitHub info.(connection, policy, etc.)",
            "383755011": "GitHub Q&A",
        },
    },
    "confluence_map_guid": {
        "space_key": "GUID",
        "space_name": "DevOps Support",
        "roots": {},
        "explicit_pages": {
            "1488175558": "Self-hosted Runner 구축하기",
        },
    },
    "confluence_map_genaibusiness": {
        "space_key": "genaibusiness",
        "space_name": "Gen.AI 사업팀",
        "roots": {},
        "explicit_pages": {
            "1268082455": "신규 SCP 프로젝트 구성시 추가 작업",
        },
    },
}
