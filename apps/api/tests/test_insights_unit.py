from app.insights.service import TRANSITIONS, VALID_STATUS


def test_insight_status_machine():
    assert "draft" in VALID_STATUS
    assert "review" in VALID_STATUS
    assert "approved" in VALID_STATUS
    assert "rejected" in VALID_STATUS
    assert "review" in TRANSITIONS["draft"]
    assert "rejected" in TRANSITIONS["draft"]
    assert "approved" in TRANSITIONS["review"]
    assert "rejected" in TRANSITIONS["review"]
    assert "draft" in TRANSITIONS["review"]
    assert TRANSITIONS["approved"] == set()
    assert "draft" in TRANSITIONS["rejected"]


def test_no_escape_from_approved():
    assert not TRANSITIONS["approved"]
