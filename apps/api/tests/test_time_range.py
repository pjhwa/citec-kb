from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.query.time_range import detect_time_scoped_list, parse_relative_range

KST = ZoneInfo("Asia/Seoul")


def test_last_week_range():
    # Wednesday 2026-07-15 KST
    now = datetime(2026, 7, 15, 12, 0, tzinfo=KST)
    dr = parse_relative_range("지난 주 지원건", now=now)
    assert dr is not None
    assert dr.date_from == date(2026, 7, 6)  # Mon prev week
    assert dr.date_to == date(2026, 7, 12)  # Sun prev week
    assert "지난" in dr.label


def test_last_week_from_monday():
    # Monday 2026-07-20 → last week Mon 7/13 – Sun 7/19
    now = datetime(2026, 7, 20, 9, 0, tzinfo=KST)
    dr = parse_relative_range("지난주", now=now)
    assert dr is not None
    assert dr.date_from == date(2026, 7, 13)
    assert dr.date_to == date(2026, 7, 19)


def test_recent_7_days():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=KST)
    dr = parse_relative_range("최근 7일 티켓", now=now)
    assert dr is not None
    assert dr.date_from == date(2026, 7, 9)
    assert dr.date_to == date(2026, 7, 15)


def test_this_month_and_last_month():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=KST)
    this_m = parse_relative_range("이번 달 지원건", now=now)
    assert this_m is not None
    assert this_m.date_from == date(2026, 7, 1)
    assert this_m.date_to == date(2026, 7, 15)

    last_m = parse_relative_range("지난 달 티켓", now=now)
    assert last_m is not None
    assert last_m.date_from == date(2026, 6, 1)
    assert last_m.date_to == date(2026, 6, 30)


def test_yesterday_today():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=KST)
    y = parse_relative_range("어제 지원", now=now)
    assert y is not None
    assert y.date_from == date(2026, 7, 14)
    assert y.date_to == date(2026, 7, 14)
    t = parse_relative_range("오늘 티켓", now=now)
    assert t is not None
    assert t.date_from == date(2026, 7, 15)


def test_detect_time_scoped_list_support():
    intent = detect_time_scoped_list("지난 주 지원건")
    assert intent is not None
    assert intent["intent"] == "time_scoped_list"
    assert intent["source_type"] == "support_history"
    assert intent["date_field"] == "Created"
    assert intent["date_from"] <= intent["date_to"]


def test_detect_rejects_bare_keyword_without_time():
    assert detect_time_scoped_list("지원건 목록 보여줘") is None


def test_detect_rejects_bare_time_without_list_or_support():
    # "지난 주" alone — no list/support wording
    assert detect_time_scoped_list("지난 주") is None


def test_detect_accepts_ticket_list_wording():
    intent = detect_time_scoped_list("최근 7일 티켓 목록")
    assert intent is not None
    assert intent["intent"] == "time_scoped_list"
