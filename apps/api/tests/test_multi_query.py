from app.retrieval.multi_query import expand_queries, strip_question_shell, _is_high_df_singleton


def test_expand_citects_and_original():
    qs = expand_queries("CITECTS-2502 모니모 Redis 타임아웃 조치")
    assert any("CITECTS-2502" in q.upper() for q in qs)
    assert any("타임아웃" in q or "모니모" in q or "Redis" in q for q in qs)
    assert qs[0]  # non-empty; original first


def test_expand_faq_hint():
    qs = expand_queries("서비스 base 계약 vs M/M base FAQ")
    assert any("QRB" in q or "FAQ" in q for q in qs)


def test_expand_empty():
    assert expand_queries("") == []
    assert expand_queries("   ") == []


def test_strip_existence_shell():
    assert "있는가" not in strip_question_shell("2026년 SCP v2 Multi-AZ 가용성 테스트가 있는가?")
    s = strip_question_shell("SCP 가용성 테스트 있나요?")
    assert "있나요" not in s
    assert "가용성" in s


def test_no_high_df_singleton_heads_for_existence_q():
    """「…가 있는가?」 must not multi-query on bare SCP / year / Multi-AZ alone."""
    q = "2026년 SCP v2 Multi-AZ 가용성 테스트가 있는가?"
    qs = expand_queries(q)
    assert qs[0] == q or "SCP" in qs[0]
    # bare high-DF singles should not appear as heads
    singles = {x.strip().lower() for x in qs if " " not in x and "/" not in x}
    assert "scp" not in singles
    assert "2026년" not in singles
    assert "multi-az" not in singles
    # phrase expansions should appear
    joined = " | ".join(qs)
    assert "Multi-AZ" in joined or "SCP v2" in joined or "가용성" in joined


def test_high_df_helper():
    assert _is_high_df_singleton("SCP")
    assert _is_high_df_singleton("2026년")
    assert _is_high_df_singleton("Multi-AZ")
    assert not _is_high_df_singleton("SCP v2 Multi-AZ")
    assert not _is_high_df_singleton("가용성 테스트")
