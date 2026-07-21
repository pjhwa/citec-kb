from app.analytics.issue_type import classify_issue_type
from app.query.analytics_intent import detect_analytics_intent, _parse_calendar_year


def test_classify_performance():
    assert classify_issue_type("물산건설 통합DB SCP 이관후 성능이슈 지원") == "성능이슈"
    assert classify_issue_type("모니모 Redis TimeOut 이슈") == "성능이슈"


def test_classify_outage_and_access():
    assert classify_issue_type("[기술지원] 화재 홈페이지 접속불가 이슈") == "접속불가"
    assert classify_issue_type("[FRB] SCP PG 권익위 장애") == "서비스장애/다운"


def test_classify_config_and_other():
    assert classify_issue_type("호텔신라 네트워크 스위치 신규 구성에 대한 설정 값 분석 요청") == "설정/구성"
    assert classify_issue_type("[SDI] Cisco 스위치 Golden ROMMON 업그레이드 기술지원") == "패치/업그레이드"
    assert classify_issue_type("완전 알 수 없는 티켓 제목 xyz") == "기타"


def test_type_query_uses_issue_type_not_component():
    intent = detect_analytics_intent("2026년에 지원한 기술지원의 유형을 알려줘")
    assert intent is not None
    assert intent["group_by"] == "issue_type"
    assert intent.get("component") is None
    assert intent.get("date_from") == "2026-01-01"
    assert intent.get("date_to") == "2026-12-31"
    assert intent.get("include_samples") is True


def test_component_axis_still_component():
    intent = detect_analytics_intent("Component 비중")
    assert intent is not None
    assert intent["group_by"] == "component"


def test_calendar_year_parse():
    assert _parse_calendar_year("2026년에 지원") == 2026
    assert _parse_calendar_year("26년 상반기") == 2026
    assert _parse_calendar_year("'26년 GSAT") == 2026
