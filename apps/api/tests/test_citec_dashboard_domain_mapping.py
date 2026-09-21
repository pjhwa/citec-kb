"""Pure unit tests for the CI-TEC domain -> fb_domain mapping.

Unlike test_citec_dashboard_service.py, this doesn't touch the DB — it just
checks _DOMAIN_TO_FB_DOMAIN itself, so it always runs.
"""

from __future__ import annotations

from app.citec_dashboard.service import _DOMAIN_TO_EXTRA_FB_DOMAINS, _DOMAIN_TO_FB_DOMAIN
from app.taxonomy import _FB_DOMAIN_TO_CORPUS_DOMAIN


def test_every_registered_fb_domain_reaches_some_citec_row():
    # Guards against the drift that started this: a new fb_domain landing in
    # app/taxonomy.py without a corresponding dashboard mapping (identity or
    # extra) would silently vanish from the coverage panel.
    identity = set(_DOMAIN_TO_FB_DOMAIN.values()) - {None}
    extras = {d for t in _DOMAIN_TO_EXTRA_FB_DOMAINS.values() for d in t}
    assert identity | extras == set(_FB_DOMAIN_TO_CORPUS_DOMAIN)


def test_exactly_seven_of_eight_fb_domain_values_are_identity_mapped():
    # 9 of 11 CI-TEC domains map onto these 7 distinct fb_domain values
    # (VMware/OpenStack and Storage/Ceph each share one). `cluster` isn't an
    # identity value for any CI-TEC domain — it only folds into Linux via
    # _DOMAIN_TO_EXTRA_FB_DOMAINS, see below.
    reachable = set(_DOMAIN_TO_FB_DOMAIN.values()) - {None}
    assert reachable == {
        "network",
        "windows",
        "linux",
        "virtualization",
        "middleware",
        "storage",
        "dbms",
    }


def test_linux_maps_to_linux_not_cluster():
    assert _DOMAIN_TO_FB_DOMAIN["Linux"] == "linux"


def test_linux_also_folds_in_pacemaker_cluster_buckets():
    # pacemaker-tools registers under fb_domain="cluster", but Pacemaker/
    # Corosync always runs on Linux hosts here, so those buckets count as
    # Linux coverage too — decision: 2026-09-21, 박재화.
    assert _DOMAIN_TO_EXTRA_FB_DOMAINS["Linux"] == ("cluster",)
    assert "Windows" not in _DOMAIN_TO_EXTRA_FB_DOMAINS  # windows-tools already hits Windows via identity


def test_vmware_and_openstack_share_virtualization():
    assert _DOMAIN_TO_FB_DOMAIN["VMware"] == "virtualization"
    assert _DOMAIN_TO_FB_DOMAIN["OpenStack"] == "virtualization"


def test_storage_and_ceph_share_storage():
    assert _DOMAIN_TO_FB_DOMAIN["Storage"] == "storage"
    assert _DOMAIN_TO_FB_DOMAIN["Ceph"] == "storage"


def test_database_maps_to_dbms():
    assert _DOMAIN_TO_FB_DOMAIN["Database"] == "dbms"


def test_kubernetes_and_performance_remain_unmapped():
    assert _DOMAIN_TO_FB_DOMAIN["Kubernetes"] is None
    assert _DOMAIN_TO_FB_DOMAIN["성능"] is None
