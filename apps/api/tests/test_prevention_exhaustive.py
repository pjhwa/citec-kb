from app.query.exhaustive import detect_exhaustive_intent
from app.query.planner import plan_query
from app.query.prevention import detect_prevention_intent
from app.query.analytics_intent import detect_analytics_intent


def test_prevention_detect():
    p = detect_prevention_intent("모니모 Redis 통신 장애를 예방하려면 무엇을 점검해야 하나?")
    assert p is not None
    assert p["intent"] == "prevention"


def test_exhaustive_detect():
    e = detect_exhaustive_intent("GRO hardware offload 관련 지원 건 전부와 공통 원인·해결은?")
    assert e is not None
    assert e["intent"] == "exhaustive"


def test_title_tokens_analytics_intent():
    a = detect_analytics_intent("장애지원 Component 티켓들은 어떤 제목 패턴이 많은가?")
    assert a is not None
    assert a["mode"] == "title_tokens"
    assert a["component"] == "장애지원"


def test_planner_routes_new_intents():
    assert plan_query("예방하려면 무엇을 점검해야 하나?")["intent"] == "prevention"
    assert plan_query("관련 지원 건 전부 모아줘")["intent"] == "exhaustive"
    assert plan_query("장애지원 제목 패턴")["intent"] == "analytics"
