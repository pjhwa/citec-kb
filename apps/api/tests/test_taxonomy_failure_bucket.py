from app.taxonomy import infer_domain


def test_infer_domain_failure_bucket_uses_protocol():
    domain = infer_domain(
        "LB idle-timeout으로 인한 RST",
        "TCP RST 직전 idle 62초",
        source_type="failure_bucket",
        metadata={"protocol": "TCP"},
    )
    assert domain == "tcp"


def test_infer_domain_failure_bucket_no_protocol_falls_back():
    domain = infer_domain(
        "네트워크 방화벽 이슈",
        "증상 설명",
        source_type="failure_bucket",
        metadata={},
    )
    assert domain == "network"
