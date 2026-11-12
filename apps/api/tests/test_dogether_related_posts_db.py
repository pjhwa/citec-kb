"""incident_reports 역조회(related_prevention_posts) — scratch DB only."""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

INC = "26099990001"
IDS = (INC, "dogether-990001", "dogether-990002", "dogether-990003")


@pytest.fixture(autouse=True)
def _db():
    from app.db import session as db_session
    from app.db.models import Document
    from app.db.session import session_scope
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()

    def wipe():
        with session_scope() as s:
            s.query(Document).filter(Document.external_id.in_(IDS)).delete(synchronize_session=False)

    def add(s, eid, st, meta, status="active", wt=None):
        s.add(Document(id=uuid.uuid4().hex, source_type=st, external_id=eid, title=f"t {eid}", body_md="b",
                       metadata_=meta, content_hash=uuid.uuid4().hex, status=status,
                       source_uri=f"https://x/{eid}", work_type=wt))

    wipe()
    with session_scope() as s:
        add(s, INC, "incident_reports", {})
        add(s, "dogether-990001", "dogether_preventive", {"swim_ids": [INC], "게시일시": "2026-11-05 08:35"}, wt="장애사례")
        add(s, "dogether-990002", "dogether_preventive", {"swim_id_candidates": [INC], "게시일시": "2026-11-06 09:00"})
        add(s, "dogether-990003", "dogether_preventive", {"swim_ids": [INC]}, status="archived")
    yield
    wipe()


def test_related_prevention_posts_matches_and_excludes_inactive():
    from app.tickets.query import related_prevention_posts

    posts = related_prevention_posts(INC)
    assert [(p["external_id"], p["match"]) for p in posts] == [
        ("dogether-990002", "candidate"),
        ("dogether-990001", "swim_ids"),
    ]
    assert posts[1]["게시유형"] == "장애사례" and posts[1]["source_uri"]
    assert related_prevention_posts("26099999999") == []


def test_ticket_detail_attaches_posts_for_incident_only():
    from app.tickets.query import get_ticket_by_external_id

    row = get_ticket_by_external_id(INC, source_type="incident_reports")
    assert len(row["related_prevention_posts"]) == 2
    post = get_ticket_by_external_id("dogether-990001", source_type="dogether_preventive")
    assert "related_prevention_posts" not in post
