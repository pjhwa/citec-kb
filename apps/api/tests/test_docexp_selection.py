import pytest

from app.docexp import selection
from app.docexp.selection import classify_title


@pytest.mark.parametrize("title", [
    "23-12-25 12월 4주차", "`25년 1월 1주차 CAB/PIR", "Nuri VPC 회의록(241211)",
    "22-06-08 55차 미팅", "7월 1주차 스크럼", "월간 보고", "Weekly meeting notes", "Meeting 정리",
])
def test_meeting_titles(title):
    assert classify_title(title) == "meeting"


@pytest.mark.parametrize("title", [
    "GET /console/api/support/widgets", "/v1/foo/bar", "POST /api/x", "/console/api/x 조회",
])
def test_api_titles(title):
    assert classify_title(title) == "api"


@pytest.mark.parametrize("title", [
    "20240312 점검", "2023-12 리포트", "12월 결산", "2024년 3월 점검", "25년 1월 결산",
])
def test_date_titles(title):
    assert classify_title(title) == "date"


@pytest.mark.parametrize("title", [
    "[Host] OS볼륨 90%이상 사용으로 인한 Pod Evicted 발생 조치 기록",
    "Oracle RAC 구성 가이드", "", "   ", None,
    "주차장 차단기 장애", "주차 관제 시스템 점검", "주차 요금 정산", "회의실 예약 시스템 오류",
    "/etc/fstab 설정 오류", "/var 볼륨 full 조치", "Patch 적용 가이드", "Meetings API 연동",
    "get 방식 설명",
])
def test_informative_titles_not_selected(title):
    assert classify_title(title) is None


ROWS = [(f"id{i}", f"ext{i:03d}", t) for i, t in enumerate(
    ["1월 1주차 미팅", "Oracle 가이드", "GET /v1/a", "2024년 3월 점검", "주차장 장애"] * 3)]


@pytest.fixture
def fake_db(monkeypatch):
    fetched = []
    monkeypatch.setattr(selection, "_list_candidates", lambda: list(ROWS))

    def fetch(ids):
        fetched.append(list(ids))
        return {i: f"body-{i}" for i in ids}

    monkeypatch.setattr(selection, "_fetch_bodies", fetch)
    return fetched


def test_select_documents_filters_and_orders(fake_db):
    docs = list(selection.select_documents())
    assert len(docs) == 9  # 3 of 5 titles x 3 repeats
    assert [d["external_id"] for d in docs] == sorted(d["external_id"] for d in docs)
    assert {d["rule"] for d in docs} == {"meeting", "api", "date"}
    assert docs[0]["body_md"] == f"body-{docs[0]['id']}"


def test_select_documents_limit_and_seed(fake_db):
    assert list(selection.select_documents(limit=0)) == []
    a = [d["external_id"] for d in selection.select_documents(limit=4, seed=1)]
    b = [d["external_id"] for d in selection.select_documents(limit=4, seed=1)]
    c = [d["external_id"] for d in selection.select_documents(limit=4, seed=2)]
    full = [d["external_id"] for d in selection.select_documents(seed=1)]
    assert a == b == full[:4] and len(a) == 4
    assert a != c


def test_select_documents_fetches_bodies_in_batches(fake_db, monkeypatch):
    monkeypatch.setattr(selection, "BATCH", 4)
    list(selection.select_documents())
    assert [len(x) for x in fake_db] == [4, 4, 1]
