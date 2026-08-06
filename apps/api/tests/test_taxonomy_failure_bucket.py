from app.taxonomy import infer_domain


def test_infer_domain_failure_bucket_uses_fb_domain_network():
    domain = infer_domain(
        "LB idle-timeout으로 인한 RST",
        "TCP RST 직전 idle 62초",
        source_type="failure_bucket",
        metadata={"fb_domain": "network", "protocol": "TCP"},
    )
    assert domain == "network"


def test_infer_domain_failure_bucket_cluster_maps_to_os():
    domain = infer_domain(
        "쿼럼 손실로 인한 페일오버",
        "token timeout 이내 펜싱 발생",
        source_type="failure_bucket",
        metadata={"fb_domain": "cluster"},
    )
    assert domain == "os"


def test_infer_domain_failure_bucket_windows_maps_to_os():
    domain = infer_domain(
        "AD 복제 실패",
        "Event ID 1135 발생 후 5초 이내 1177 발생",
        source_type="failure_bucket",
        metadata={"fb_domain": "windows"},
    )
    assert domain == "os"


def test_infer_domain_failure_bucket_unmapped_fb_domain_falls_back_to_keywords():
    domain = infer_domain(
        "네트워크 방화벽 이슈",
        "증상 설명",
        source_type="failure_bucket",
        metadata={"fb_domain": "some_new_plugin"},
    )
    assert domain == "network"


def test_infer_domain_failure_bucket_no_metadata_falls_back():
    domain = infer_domain(
        "네트워크 방화벽 이슈",
        "증상 설명",
        source_type="failure_bucket",
        metadata={},
    )
    assert domain == "network"
