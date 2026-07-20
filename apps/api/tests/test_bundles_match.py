from app.bundles.match import match_bundles


def test_match_network_timeout_bundle():
    hits = match_bundles("Redis 타임아웃 BFD down 순단", top_k=2)
    names = [h["name"] for h in hits]
    assert "network-timeout" in names


def test_match_linux_hang_bundle():
    hits = match_bundles("서버 hang soft lockup not responding", top_k=2)
    names = [h["name"] for h in hits]
    assert "linux-hang" in names
