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


def test_swim_month_trend_routes_to_incident_reports():
    """Phase 2 Track B: SWIM keyword routes analytics source_type, not group_by axis."""
    intent = detect_analytics_intent("SWIM 장애 월별 추이")
    assert intent is not None
    assert intent["source_type"] == "incident_reports"
    assert intent["group_by"] == "month"


def test_swim_issue_type_breakdown_uses_generic_classifier():
    intent = detect_analytics_intent("SWIM 장애유형별 건수")
    assert intent is not None
    assert intent["source_type"] == "incident_reports"
    assert intent["group_by"] == "issue_type"
    assert intent["component"] is None  # _COMP_MAP (Jira Component) never applies to SWIM


def test_swim_component_axis_downgrades_to_total():
    """aggregate_tickets._bucket_key reads Document.metadata['Component'] (Jira-only) —
    SWIM has no such key, so a component/status/assignee axis would silently bucket
    everything into "(empty)". Must downgrade to total instead of faking a breakdown.
    """
    intent = detect_analytics_intent("SWIM 장애 컴포넌트별 건수")
    assert intent is not None
    assert intent["source_type"] == "incident_reports"
    assert intent["group_by"] == "total"
