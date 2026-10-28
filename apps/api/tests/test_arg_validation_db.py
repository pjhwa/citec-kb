"""A3 — 잘못된 인자는 조용히 다른 동작으로 바뀌지 않고 400 + 허용 값 목록을 돌려준다.

scratch DB 전용(앱 import/세션 사용). 검증은 DB 조회 전에 일어나는 것이 대부분이다.
"""

from __future__ import annotations

import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.db import session as db_session
    from app.main import app
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    return TestClient(app)


def test_analytics_group_by_bogus_is_400_with_allowed(client):
    r = client.get("/v1/analytics/tickets", params={"group_by": "bogus", "relative": "올해"})
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "bogus" in d and "year" in d and "issue_type" in d


def test_analytics_valid_group_by_still_ok(client):
    for gb in ("year", "month", "total", "component", "status", "assignee", "issue_type"):
        assert client.get("/v1/analytics/tickets", params={"group_by": gb}).status_code == 200, gb


def test_planner_intent_group_by_values_are_all_valid():
    """analytics_intent가 내는 group_by는 전부 허용 집합 안이어야 kb_query가 500이 되지 않는다."""
    from app.analytics.aggregate import _GROUP_BY
    from app.query.analytics_intent import detect_analytics_intent

    for q in ["올해 건수", "연도별 건수", "월별 건수", "컴포넌트별", "유형별 비중", "상태별", "담당자별", "총 몇 건"]:
        intent = detect_analytics_intent(q)
        if intent and intent.get("group_by"):
            assert intent["group_by"] in _GROUP_BY | {"token"}, (q, intent)


def test_recurring_patterns_unknown_domain_is_400(client):
    r = client.get("/v1/citec-dashboard/recurring-patterns", params={"group_by": "domain", "domains": "Redis"})
    assert r.status_code == 400
    assert "Redis" in r.json()["detail"] and "Linux" in r.json()["detail"]


def test_recurring_patterns_domain_case_insensitive_and_severity(client):
    ok = client.get("/v1/citec-dashboard/recurring-patterns", params={"group_by": "domain", "domains": "linux,STORAGE"})
    assert ok.status_code == 200
    assert ok.json()["filters"]["domains"] == ["Linux", "Storage"]
    bad = client.get("/v1/citec-dashboard/recurring-patterns", params={"group_by": "domain", "severity_tiers": "huge"})
    assert bad.status_code == 400 and "major" in bad.json()["detail"]


def test_failure_bucket_match_validates_filters(client):
    body = {"observed_signals": ["x"], "symptom": "s"}
    bad = client.post("/v1/failure-buckets/match", json={**body, "fb_domain": "bogus"})
    assert bad.status_code == 400 and "network" in bad.json()["detail"]
    badenv = client.post("/v1/failure-buckets/match", json={**body, "environment": "moon"})
    assert badenv.status_code == 400 and "onprem" in badenv.json()["detail"]
    ok = client.post("/v1/failure-buckets/match", json={**body, "fb_domain": "NETWORK", "environment": "CSP"})
    assert ok.status_code == 200
