from app.query.planner import plan_query


def test_capacity_plan():
    p = plan_query("2주 분야별 대수")
    assert p["intent"] == "capacity"
    assert p["period_days"] == 14


def test_analytics_plan():
    p = plan_query("연도별 지원 건수")
    assert p["intent"] == "analytics"
    assert p["group_by"] == "year"


def test_list_plan():
    p = plan_query("지난 주 지원건")
    assert p["intent"] == "time_scoped_list"


def test_checklist_plan():
    p = plan_query("리눅스 파일시스템 관련 PISA 진단 체크리스트 항목은?")
    assert p["intent"] == "checklist"
    assert p.get("area") == "Linux"


def test_similar_incident_plan():
    p = plan_query("과거 유사 장애가 있었는지와 당시 원인·해결은?")
    assert p["intent"] == "similar_incident"


def test_capacity_case_goes_hybrid():
    # 사례 조회는 rules 계산이 아님
    p = plan_query("진단 인력을 1주 2M/M 또는 2주 4M/M로 제안한 사례는?")
    assert p["intent"] == "hybrid_search"


def test_hybrid_default():
    p = plan_query("CITECTS-979에서 GPDB IO 성능 이슈의 원인과 조치는?")
    assert p["intent"] == "hybrid_search"
