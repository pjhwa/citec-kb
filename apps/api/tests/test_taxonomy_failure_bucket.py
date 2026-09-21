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


def test_infer_domain_failure_bucket_dbms_maps_to_dbms():
    domain = infer_domain(
        "SQL Server AlwaysOn 페일오버",
        "Error 8645 RESOURCE_SEMAPHORE 대기 후 failover",
        source_type="failure_bucket",
        metadata={"fb_domain": "dbms"},
    )
    assert domain == "dbms"


def test_infer_domain_failure_bucket_linux_maps_to_os():
    domain = infer_domain(
        "OOM killer로 인한 노드 무응답",
        "hung_task 120s 경고 연속",
        source_type="failure_bucket",
        metadata={"fb_domain": "linux"},
    )
    assert domain == "os"


def test_infer_domain_failure_bucket_virtualization_maps_to_virtualization():
    domain = infer_domain(
        "ESXi APD로 인한 게스트 I/O 정지",
        "path down 후 APD 140s 초과",
        source_type="failure_bucket",
        metadata={"fb_domain": "virtualization"},
    )
    assert domain == "virtualization"


def test_infer_domain_failure_bucket_middleware_maps_to_middleware():
    domain = infer_domain(
        "WebLogic stuck thread",
        "BEA-000337 stuck thread 600s 초과",
        source_type="failure_bucket",
        metadata={"fb_domain": "middleware"},
    )
    assert domain == "middleware"


def test_infer_domain_failure_bucket_storage_maps_to_storage():
    domain = infer_domain(
        "Ceph slow request",
        "slow request ≥ 30s + osd down 동반",
        source_type="failure_bucket",
        metadata={"fb_domain": "storage"},
    )
    assert domain == "storage"


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
