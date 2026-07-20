from app.query.analytics_intent import detect_analytics_intent


def test_year_group_by():
    intent = detect_analytics_intent("연도별 지원 건수")
    assert intent is not None
    assert intent["intent"] == "analytics"
    assert intent["group_by"] == "year"
    assert intent["mode"] == "aggregate"


def test_component_share():
    intent = detect_analytics_intent("Component 비중")
    assert intent is not None
    assert intent["group_by"] == "component"


def test_this_month_incident_count():
    intent = detect_analytics_intent("이번 달 장애 건수")
    assert intent is not None
    assert intent["component"] == "장애지원"
    assert intent["group_by"] == "total"
    assert intent.get("date_from") is not None


def test_entity_share_monimo():
    intent = detect_analytics_intent("모니모 지원 비중")
    assert intent is not None
    assert intent["mode"] == "entity_share"
    assert intent["entity"] == "모니모"


def test_rejects_non_analytics():
    assert detect_analytics_intent("지난 주 지원건") is None
    assert detect_analytics_intent("모니모 Redis 타임아웃") is None
