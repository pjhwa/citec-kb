"""DB-touching tests for app.tickets.query.list_tickets' component filter
(P0-D: docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §7) —
list_tickets and aggregate_tickets must apply the same Component predicate so
a list's row count and a COUNT over the same filters agree.

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
scratch Postgres+pgvector DB with the alembic schema applied (same opt-in
convention as tests/test_confluence_sync_db.py). Never the live
citec_knowledge DB.
"""

from __future__ import annotations

import hashlib
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


_PREFIX = "test_tickets_component"


def _make_doc(session, *, external_id: str, component: str, created: str) -> str:
    from app.db.models import Document

    doc_id = f"{_PREFIX}:{external_id}"
    title = f"[{external_id}] test"
    body_md = "본문"
    payload = f"{title}\n{body_md}\n{component}\n{created}"
    doc = Document(
        id=doc_id,
        source_id="fs_raw",
        source_type="support_history",
        external_id=external_id,
        title=title,
        body_md=body_md,
        metadata_={"Component": component, "Created": created},
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        version=1,
        status="active",
        evidence_grade="B",
    )
    session.merge(doc)
    return doc_id


def _cleanup(external_ids: list[str]) -> None:
    from sqlalchemy import delete

    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        doc_ids = [f"{_PREFIX}:{eid}" for eid in external_ids]
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))


def test_list_and_aggregate_component_filter_counts_agree():
    from app.analytics.aggregate import aggregate_tickets
    from app.db.session import session_scope
    from app.tickets.query import list_tickets

    eids = ["CITECTS-91001", "CITECTS-91002", "CITECTS-91003"]
    _cleanup(eids)
    try:
        with session_scope() as session:
            _make_doc(session, external_id=eids[0], component="기술지원", created="2026-09-08")
            _make_doc(session, external_id=eids[1], component="기술지원", created="2026-09-09")
            _make_doc(session, external_id=eids[2], component="장애지원", created="2026-09-10")

        from datetime import date

        listed = list_tickets(
            source_type="support_history",
            date_field="Created",
            date_from=date(2026, 9, 7),
            date_to=date(2026, 9, 13),
            component="기술지원",
            limit=50,
        )
        agg = aggregate_tickets(
            source_type="support_history",
            group_by="total",
            date_field="Created",
            date_from=date(2026, 9, 7),
            date_to=date(2026, 9, 13),
            component="기술지원",
        )
        assert listed["total"] == 2
        assert agg["total"] == 2
        assert listed["total"] == agg["total"]
        assert {i["external_id"] for i in listed["items"]} == {eids[0], eids[1]}
    finally:
        _cleanup(eids)


def test_list_total_unaffected_by_pagination():
    from datetime import date

    from app.db.session import session_scope
    from app.tickets.query import list_tickets

    eids = [f"CITECTS-9200{i}" for i in range(5)]
    _cleanup(eids)
    try:
        with session_scope() as session:
            for i, eid in enumerate(eids):
                _make_doc(session, external_id=eid, component="기술지원", created=f"2026-09-{8+i:02d}")

        page1 = list_tickets(
            source_type="support_history",
            date_field="Created",
            date_from=date(2026, 9, 7),
            date_to=date(2026, 9, 13),
            component="기술지원",
            limit=2,
            offset=0,
        )
        page2 = list_tickets(
            source_type="support_history",
            date_field="Created",
            date_from=date(2026, 9, 7),
            date_to=date(2026, 9, 13),
            component="기술지원",
            limit=2,
            offset=2,
        )
        assert page1["total"] == 5
        assert page2["total"] == 5
        ids1 = {i["external_id"] for i in page1["items"]}
        ids2 = {i["external_id"] for i in page2["items"]}
        assert ids1.isdisjoint(ids2)
    finally:
        _cleanup(eids)
