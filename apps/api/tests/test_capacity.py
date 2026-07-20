from app.capacity.calculator import estimate_capacity, list_capacity_rules
from app.capacity.intent import detect_capacity_intent, parse_period_days


def test_list_rules_1an():
    rules = list_capacity_rules()
    assert rules["basis"] == "1안"
    assert rules["mm_per_field_week"] == 0.25
    assert rules["llm_used"] is False
    fields = {f["field"]: f["units"] for f in rules["fields"]}
    assert fields["Linux"] == 20
    assert fields["Web/WAS"] == 5
    assert fields["네트워크"] == 10


def test_estimate_1_week_linux():
    r = estimate_capacity(period_days=7, fields=["Linux"])
    assert r["llm_used"] is False
    assert r["scale"] == 1.0
    assert r["scale_note"] is None
    assert len(r["fields"]) == 1
    assert r["fields"][0]["units"] == 20
    assert r["fields"][0]["mm"] == 0.25
    assert r["fields"][0]["unit_price"] == 100
    assert r["fields"][0]["price"] == 2000
    assert r["totals"]["mm"] == 0.25


def test_estimate_2_week_scale():
    r = estimate_capacity(period_days=14, fields=["Linux", "DBMS"])
    assert r["scale"] == 2.0
    assert r["scale_note"] is not None
    by = {f["field"]: f for f in r["fields"]}
    assert by["Linux"]["units"] == 40
    assert by["Linux"]["mm"] == 0.5
    assert by["DBMS"]["units"] == 10
    assert by["DBMS"]["unit_price"] == 500
    assert by["DBMS"]["price"] == 5000  # 10 * 500
    assert r["totals"]["mm"] == 1.0


def test_estimate_all_fields_1w_mm():
    r = estimate_capacity(period_days=7)
    # 10 fields * 0.25
    assert r["totals"]["field_count"] == 10
    assert abs(r["totals"]["mm"] - 2.5) < 1e-6


def test_detect_capacity_2weeks_linux():
    intent = detect_capacity_intent("2주 Linux 분야 대수")
    assert intent is not None
    assert intent["intent"] == "capacity"
    assert intent["period_days"] == 14
    assert "Linux" in (intent.get("fields") or [])


def test_detect_price_and_mm():
    assert detect_capacity_intent("분야별 단가") is not None
    assert detect_capacity_intent("0.25M/M 1안") is not None
    assert detect_capacity_intent("지난 주 지원건") is None
    assert detect_capacity_intent("연도별 지원 건수") is None


def test_parse_period():
    assert parse_period_days("2주 공수") == 14
    assert parse_period_days("1주 대수") == 7
    assert parse_period_days("21일") == 21
