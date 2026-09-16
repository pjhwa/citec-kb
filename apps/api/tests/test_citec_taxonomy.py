"""Unit tests for app.frames.citec_taxonomy — CI-TEC's 11-domain tagging
and SWIM severity-tier classification. Pure functions, no I/O.
"""

from __future__ import annotations

from app.frames.citec_taxonomy import (
    CITEC_DOMAINS,
    classify_severity_tier,
    tag_citec_domains,
)


def test_citec_domains_has_ten_components_plus_performance():
    assert set(CITEC_DOMAINS) == {
        "Linux", "Windows", "VMware", "OpenStack", "Kubernetes",
        "Middleware", "Network", "Storage", "Ceph", "Database", "성능",
    }
    assert len(CITEC_DOMAINS) == 11


def test_tags_network_and_openstack_from_real_swim_title():
    # swim_24102957888 (real corpus, 2026-09-16 validation run)
    assert tag_citec_domains("", title="[삼성SDS] SCP Nuri 방화벽 인터페이스 다운으로 이중화전환") == [
        "OpenStack", "Network",
    ]


def test_openstack_matches_internal_nuri_brand_name():
    """Generic OpenStack/Nova/Neutron/Cinder keywords alone found only 6
    hits in the 15,333-doc corpus (2026-09-16) — CI-TEC's actual OpenStack
    platform is branded "Nuri" internally (see
    app.confluence.map_sync.MAP_SOURCE_DEFS' Openstack101 roots), so
    without this the domain would be almost useless here."""
    assert "OpenStack" in tag_citec_domains("SCP Nuri 방화벽 인터페이스 업/다운 반복", title="")
    assert "OpenStack" in tag_citec_domains("", title="OpenStack Nova 컴퓨트 노드 장애")


def test_scp_v2_tags_openstack():
    # 박재화 2026-09-16: SCP(Samsung Cloud Platform) v2 was rebuilt on
    # OpenStack. Corpus has both "SCPv2" and "SCP v2" spellings (39 + 21
    # hits, 2026-09-16 count).
    assert "OpenStack" in tag_citec_domains("SCPv2 가상화 호스트 다운", title="")
    assert "OpenStack" in tag_citec_domains("", title="SCP v2 네트워크 인터페이스 장애")


def test_scp_v1_tags_vmware():
    # SCP v1 runs on VMware.
    assert "VMware" in tag_citec_domains("SCPv1 ESXi 호스트 리부팅", title="")
    assert "VMware" in tag_citec_domains("", title="SCP v1 가상화 호스트 장애")


def test_bare_scp_without_version_tags_neither_vmware_nor_openstack():
    """박재화 2026-09-16: most of the corpus's ~5,900 bare "SCP" mentions
    have no version marker, and plenty are incidents where the v1/v2
    (VMware vs OpenStack) distinction genuinely doesn't apply (e.g. a
    network/facility issue at the SCP platform level, not its
    hypervisor/orchestration layer). Guessing v1-by-default would
    misattribute those to VMware, so bare "SCP" must resolve to neither
    domain on its own."""
    tags = tag_citec_domains("SCP 방화벽 인터페이스 다운으로 이중화전환", title="")
    assert "VMware" not in tags
    assert "OpenStack" not in tags


def test_scp_version_number_does_not_false_positive_on_unrelated_digits():
    # "SCP 23"/"SCP 600" etc (real corpus noise, e.g. a server/case number)
    # must not be mistaken for "SCP v2".
    assert "OpenStack" not in tag_citec_domains("SCP 23 케이스 조회 오류", title="")
    assert "VMware" not in tag_citec_domains("SCP 600 포트 점검", title="")


def test_tags_database_on_db_hang():
    assert "Database" in tag_citec_domains("차세대 시스템 DB Hang", title="")


def test_performance_co_occurs_with_its_component_not_instead_of_it():
    """성능 is cross-cutting by design — "DB Hang" should tag both Database
    (the component) and 성능 (Hang is a performance-shaped symptom), not
    replace one with the other."""
    tags = tag_citec_domains("메트라이프 차세대 시스템 DB Hang", title="")
    assert "Database" in tags
    assert "성능" in tags


def test_ceph_is_its_own_domain_not_collapsed_into_storage():
    # Ceph must tag as its own domain per 박재화's explicit request (10
    # components, Ceph named separately from Storage) — even though a Ceph
    # incident will often also carry the generic Storage tag, "Ceph" alone
    # must be present so it doesn't disappear behind the broader label.
    tags = tag_citec_domains("Ceph 클러스터 OSD 장애", title="")
    assert "Ceph" in tags


def test_no_match_returns_empty_list():
    assert tag_citec_domains("삼성전자 혜주법인 일시단전으로 인한 정전", title="") == []


def test_bare_ad_server_mention_does_not_false_positive_as_windows():
    """Real corpus case (swim_13112505839, 2026-09-16 validation): an
    earlier draft pattern matched the bare substring "AD서버" and tagged
    this fax-server DNS fix as Windows, even though the incident has
    nothing to do with Windows/AD administration. The pattern requires the
    fuller "Active Directory" or "Windows Server" phrase instead — a lone
    "AD서버" mention (AD used as shorthand, common in many unrelated
    contexts) must not tag Windows on its own."""
    body = (
        "■ 검토의견: 팩스서버의 기존 DNS정보를 AD서버 IP로 변경하는 것이 아닌 "
        "추가 했어야 하나 작업을 제대로 수행하지 못했고, 팩스 서버 DNS 정보를 "
        "기존 DNS 정보로 원복하여 조치함"
    )
    assert "Windows" not in tag_citec_domains(body, title="삼성생명 IPCC 팩스서버 장애")


def test_full_active_directory_phrase_does_tag_windows():
    assert "Windows" in tag_citec_domains("Active Directory 도메인 컨트롤러 장애", title="")


def test_severity_tier_major_for_grades_1_to_3():
    assert classify_severity_tier("1등급") == "major"
    assert classify_severity_tier("2등급") == "major"
    assert classify_severity_tier("3등급") == "major"


def test_severity_tier_minor_for_grade_4():
    assert classify_severity_tier("4등급") == "minor"


def test_severity_tier_failover_no_impact():
    assert classify_severity_tier("FO등급") == "failover_no_impact"


def test_severity_tier_customer_and_vendor_fault():
    assert classify_severity_tier("X등급") == "customer_fault"
    assert classify_severity_tier("N등급") == "vendor_fault"


def test_severity_tier_unknown_for_unrecognized_or_blank():
    # LI등급/LR등급 seen in the recent-2-year real sample (2026-09-16) —
    # not yet mapped; must surface as unknown, not silently misclassified.
    assert classify_severity_tier("LI등급") == "unknown"
    assert classify_severity_tier("") == "unknown"
    assert classify_severity_tier(None) == "unknown"
