import pytest
from fastapi.testclient import TestClient

from app.retrieval.filter_args import normalize_source_type


def test_retired_http_paths_answer_410_with_guidance():
    from app.main import app

    c = TestClient(app)
    for method, path in (("get", "/v1/insights"), ("post", "/v1/insights"), ("get", "/v1/insights/abc"),
                         ("post", "/v1/insights/abc/approve"), ("get", "/api/synthesis"), ("get", "/api/synthesis/x")):
        r = getattr(c, method)(path)
        assert r.status_code == 410, (method, path, r.status_code)
        assert "kb_list_decisions" in r.json()["detail"] and r.json()["replacement"] == "/v1/decisions"


@pytest.mark.parametrize("val", ["insight", "insights", "synthesis"])
def test_retired_filter_values_are_rejected_with_allowed_list(val):
    with pytest.raises(ValueError) as e:
        normalize_source_type(val, field="section")
    assert "confirmed_decision" in str(e.value)


def test_feedback_rejects_retired_target_type():
    from app.main import app

    r = TestClient(app).post("/v1/feedback", json={"target_type": "insight", "target_id": "x", "rating": 1})
    assert r.status_code == 400 and "decision" in r.json()["detail"]
