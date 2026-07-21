from app.analytics.issue_type import classify_issue_type, issue_type_labels
from app.query.analytics_intent import detect_analytics_intent, _parse_calendar_year


def test_classify_performance_split():
    assert classify_issue_type("모니모 Redis TimeOut 이슈") == "타임아웃"
    assert classify_issue_type("물산건설 통합DB SCP 이관후 성능이슈 지원") == "성능저하/지연"
    assert classify_issue_type("삼성페이 DB01/02 서버 IOWAIT 증가 이슈") == "리소스고갈"
    assert classify_issue_type("관세청 전자상거래 통관플랫폼 성능 테스트 지원") == "성능테스트/튜닝"


def test_classify_outage_split():
    assert classify_issue_type("[기술지원] 화재 홈페이지 접속불가 이슈") == "접속불가"
    assert classify_issue_type("SDS VDI AD 로그인 불가 이슈") == "로그인/인증불가"
    assert classify_issue_type("[FRB] SCP PG 권익위 장애") == "서비스장애/FRB"
    assert classify_issue_type("GPU VM OS행 Crash발생") == "시스템Crash/Hang"
    assert classify_issue_type("[장애지원] 제일기획 SAP Oracle(IBP) CRS 기동 불가") == "Failover/클러스터"


def test_classify_error_and_change():
    assert classify_issue_type("(Bug) 업로드 시 에러 발생 (Windows cluster log)") == "소프트웨어버그"
    assert classify_issue_type("[기술지원] SCP VM과 전자DNS와 통신 오류 분석") == "통신/연동오류"
    assert classify_issue_type("[SDI] Cisco 스위치 Golden ROMMON 업그레이드 기술지원") == "패치/업그레이드"
    assert classify_issue_type("호텔신라 네트워크 스위치 신규 구성에 대한 설정 값 분석 요청") == "설정오류/파라미터"


def test_classify_domain_split():
    assert classify_issue_type("SCPv2 수원 PS 방화벽 2호기 이슈") == "방화벽/보안GW"
    assert classify_issue_type("A10 LB 세션 비정상 종료(RST 발생) 이슈 분석") == "LB/로드밸런서"
    assert classify_issue_type("[기술지원] SCP Windows VM 인증서 업데이트 이슈") == "인증서/SSL/TLS"
    assert classify_issue_type("중남미 SDS시스템(TOTVS) SCP 전환 지원") == "클라우드전환/마이그레이션"


def test_classify_admin_and_other():
    assert classify_issue_type("[GSAT] '26 상반기 3급 공채") == "채용/평가"
    assert classify_issue_type("[세미나] Broadcom '26년 1분기 세미나") == "교육/세미나"
    assert classify_issue_type("완전 알 수 없는 티켓 제목 xyz") == "기타"
    assert "타임아웃" in issue_type_labels()
    assert issue_type_labels()[-1] == "기타"


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
