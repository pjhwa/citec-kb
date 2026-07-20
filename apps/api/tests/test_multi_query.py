from app.retrieval.multi_query import expand_queries


def test_expand_citects_and_original():
    qs = expand_queries("CITECTS-2502 모니모 Redis 타임아웃 조치")
    assert any("CITECTS-2502" in q.upper() for q in qs)
    assert any("타임아웃" in q or "모니모" in q or "Redis" in q for q in qs)
    assert qs[-1]  # non-empty


def test_expand_faq_hint():
    qs = expand_queries("서비스 base 계약 vs M/M base FAQ")
    assert any("QRB" in q or "FAQ" in q for q in qs)


def test_expand_empty():
    assert expand_queries("") == []
    assert expand_queries("   ") == []
