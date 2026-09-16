"""DB-touching tests for app.citec_dashboard.service.

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
scratch Postgres+pgvector DB with the alembic schema applied (same opt-in
convention as tests/test_confluence_sync_db.py). Never the live
citec_knowledge DB.
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


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _seed(session, *, external_id, title, meta, domains, tier):
    import hashlib
    import json
    import uuid

    from app.db.models import Document, IssueFrame

    doc_id = f"test_dash:{external_id}"
    payload = f"{title}\n\n{json.dumps(meta, sort_keys=True)}"
    doc = Document(
        id=doc_id,
        source_id="fs_raw",
        source_type="incident_reports",
        external_id=external_id,
        title=title,
        body_md="",
        metadata_=meta,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        version=1,
        status="active",
        evidence_grade="B",
        source_uri=f"https://example/{external_id}",
    )
    session.merge(doc)
    session.flush()
    frame = IssueFrame(id=str(uuid.uuid4()), document_id=doc_id, citec_domains=domains, severity_tier=tier)
    session.merge(frame)
    return doc_id


def _cleanup(external_ids: list[str]) -> None:
    from sqlalchemy import delete

    from app.db.models import Document, IssueFrame
    from app.db.session import session_scope

    with session_scope() as session:
        doc_ids = [f"test_dash:{e}" for e in external_ids]
        session.execute(delete(IssueFrame).where(IssueFrame.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))


def test_query_recurring_patterns_groups_by_domain_with_fanout():
    from app.citec_dashboard.service import query_recurring_patterns
    from app.db.session import session_scope

    eids = ["dash1", "dash2", "dash3"]
    _cleanup(eids)
    try:
        with session_scope() as session:
            _seed(session, external_id="dash1", title="DB Hang 장애",
                  meta={"운영부서": "팀A", "고객사": "고객X", "발생일시(한국)": "2026-08-01 10:00"},
                  domains=["Database", "성능"], tier="major")
            _seed(session, external_id="dash2", title="DB 접속 불가",
                  meta={"운영부서": "팀A", "고객사": "고객Y", "발생일시(한국)": "2026-08-05 10:00"},
                  domains=["Database"], tier="minor")
            _seed(session, external_id="dash3", title="Network 회선 장애",
                  meta={"운영부서": "팀B", "고객사": "고객X", "발생일시(한국)": "2026-08-10 10:00"},
                  domains=["Network"], tier="minor")

        result = query_recurring_patterns(group_by=["domain"], min_count=2)
        by_domain = {g["group"]["domain"]: g for g in result["groups"]}
        assert by_domain["Database"]["count"] == 2  # dash1 + dash2
        assert by_domain["Database"]["customer_count"] == 2  # 고객X, 고객Y
        # Network only has 1 (dash3) -> below min_count=2, excluded
        assert "Network" not in by_domain
    finally:
        _cleanup(eids)


def test_query_recurring_patterns_since_days_filters_by_occurred_at():
    from datetime import datetime, timedelta, timezone

    from app.citec_dashboard.service import query_recurring_patterns
    from app.db.session import session_scope

    eids = ["dash_old1", "dash_old2", "dash_old3", "dash_new1"]
    _cleanup(eids)
    old_date = (datetime.now(timezone.utc) - timedelta(days=800)).strftime("%Y-%m-%d")
    new_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        with session_scope() as session:
            for i, eid in enumerate(["dash_old1", "dash_old2", "dash_old3"]):
                _seed(session, external_id=eid, title=f"오래된 VMware 장애 {i}",
                      meta={"운영부서": "팀A", "고객사": "고객X", "발생일시(한국)": f"{old_date} 10:00"},
                      domains=["VMware"], tier="minor")
            _seed(session, external_id="dash_new1", title="최근 VMware 장애",
                  meta={"운영부서": "팀A", "고객사": "고객X", "발생일시(한국)": f"{new_date} 10:00"},
                  domains=["VMware"], tier="minor")

        result_all = query_recurring_patterns(group_by=["domain"], domains=["VMware"], min_count=1)
        vmware_all = next(g for g in result_all["groups"] if g["group"]["domain"] == "VMware")
        assert vmware_all["count"] >= 4

        result_recent = query_recurring_patterns(
            group_by=["domain"], domains=["VMware"], min_count=1, since_days=30
        )
        vmware_recent = next(g for g in result_recent["groups"] if g["group"]["domain"] == "VMware")
        assert vmware_recent["count"] == 1
    finally:
        _cleanup(eids)


def test_query_recurring_patterns_rejects_invalid_group_by():
    from app.citec_dashboard.service import query_recurring_patterns

    with pytest.raises(ValueError):
        query_recurring_patterns(group_by=["not_a_real_dim"])


def test_query_recurring_patterns_rejects_too_many_dims():
    from app.citec_dashboard.service import query_recurring_patterns

    with pytest.raises(ValueError):
        query_recurring_patterns(group_by=["domain", "severity_tier", "dept", "customer"])


def test_failure_bucket_coverage_flags_gap_for_domain_with_no_bucket():
    from app.citec_dashboard.service import failure_bucket_coverage
    from app.db.session import session_scope

    eids = ["dash_gap1", "dash_gap2", "dash_gap3"]
    _cleanup(eids)
    try:
        with session_scope() as session:
            for i, eid in enumerate(eids):
                _seed(session, external_id=eid, title=f"Kubernetes POD 장애 {i}",
                      meta={"운영부서": "팀A", "고객사": "고객X", "발생일시(한국)": "2026-08-01 10:00"},
                      domains=["Kubernetes"], tier="minor")

        result = failure_bucket_coverage(since_days=3650, min_count=3)
        row = next(r for r in result["domains"] if r["domain"] == "Kubernetes")
        assert row["recurring_incident_count"] >= 3
        assert row["fb_domain"] is None
        assert row["status"] == "no_fb_domain_defined"

        network_row = next(r for r in result["domains"] if r["domain"] == "Network")
        assert network_row["fb_domain"] == "network"
    finally:
        _cleanup(eids)


def test_domain_catalog_is_static_and_cheap():
    from app.citec_dashboard.service import domain_catalog

    cat = domain_catalog()
    assert len(cat["domains"]) == 11
    assert "major" in cat["severity_tiers"]
    assert cat["max_group_by_dimensions"] == 3
