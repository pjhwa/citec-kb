import pytest

from app.docexp.selection import classify_title, MIN_BODY_CHARS


@pytest.mark.parametrize("title", [
    "23-12-25 12월 4주차", "`25년 1월 1주차 CAB/PIR", "Nuri VPC 회의록(241211)",
    "22-06-08 55차 미팅", "7월 1주차 스크럼", "월간 보고",
])
def test_meeting_titles(title):
    assert classify_title(title) in {"meeting", "date"}


@pytest.mark.parametrize("title", ["GET /console/api/support/widgets", "/v1/foo/bar", "POST /api/x"])
def test_api_titles(title):
    assert classify_title(title) == "api"


@pytest.mark.parametrize("title", ["20240312 점검", "2023-12 리포트", "12월 결산"])
def test_date_titles(title):
    assert classify_title(title) in {"date", "meeting"}


@pytest.mark.parametrize("title", [
    "[Host] OS볼륨 90%이상 사용으로 인한 Pod Evicted 발생 조치 기록",
    "Oracle RAC 구성 가이드", "", "   ", None,
])
def test_informative_titles_not_selected(title):
    assert classify_title(title) is None


def test_min_body_chars_constant():
    assert MIN_BODY_CHARS == 200
