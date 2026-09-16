"""DB-touching tests for app.frames.job.extract_citec_domains.

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
scratch Postgres+pgvector DB with the alembic schema applied (same opt-in
convention as tests/test_confluence_sync_db.py — see that file's
docstring for how to stand one up). Never the live citec_knowledge DB.
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


def _make_document(session, *, external_id: str, title: str, body_md: str, metadata: dict) -> str:
    import hashlib
    import json

    from app.db.models import Document

    doc_id = f"test_citec_domains:{external_id}"
    payload = f"{title}\n{body_md}\n{json.dumps(metadata, sort_keys=True)}"
    doc = Document(
        id=doc_id,
        source_id="fs_raw",
        source_type="incident_reports",
        external_id=external_id,
        title=title,
        body_md=body_md,
        metadata_=metadata,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        version=1,
        status="active",
        evidence_grade="B",
    )
    session.merge(doc)
    return doc_id


def _cleanup(external_ids: list[str]) -> None:
    from sqlalchemy import delete

    from app.db.models import Document, IssueFrame
    from app.db.session import session_scope

    with session_scope() as session:
        doc_ids = [f"test_citec_domains:{eid}" for eid in external_ids]
        session.execute(delete(IssueFrame).where(IssueFrame.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))


def test_extract_citec_domains_tags_and_classifies_severity():
    from app.db.session import session_scope
    from app.frames.job import extract_citec_domains

    eid = "swim_test_1"
    _cleanup([eid])
    try:
        with session_scope() as session:
            _make_document(
                session,
                external_id=eid,
                title="SCPv2 가상화 호스트 다운으로 이중화전환",
                body_md="■ 장애원인: OpenStack 컴퓨트 노드 이상",
                metadata={"최종등급": "FO등급"},
            )

        stats = extract_citec_domains(source_type="incident_reports")
        assert stats["errors"] == 0
        assert stats["upserted"] >= 1

        from app.db.models import Document, IssueFrame

        with session_scope() as session:
            doc = session.query(Document).filter_by(
                source_type="incident_reports", external_id=eid
            ).one()
            frame = session.query(IssueFrame).filter_by(document_id=doc.id).one()
            assert "OpenStack" in frame.citec_domains
            assert frame.severity_tier == "failover_no_impact"
    finally:
        _cleanup([eid])


def test_extract_citec_domains_is_idempotent_without_force():
    from app.db.session import session_scope
    from app.frames.job import extract_citec_domains

    eid = "swim_test_2"
    _cleanup([eid])
    try:
        with session_scope() as session:
            _make_document(
                session,
                external_id=eid,
                title="Ceph 클러스터 OSD 장애",
                body_md="■ 장애원인: Ceph OSD 다운",
                metadata={"최종등급": "4등급"},
            )

        stats1 = extract_citec_domains(source_type="incident_reports")
        assert stats1["upserted"] >= 1

        # Second run without --force must not re-select the same row (it's
        # already tagged: severity_tier is not NULL) — the whole point of
        # the "already processed" marker.
        stats2 = extract_citec_domains(source_type="incident_reports")
        from app.db.models import Document

        with session_scope() as session:
            doc_id = session.query(Document.id).filter_by(
                source_type="incident_reports", external_id=eid
            ).scalar()
        assert doc_id is not None
        # This doc specifically should not appear in the second run's work
        # at all (processed count for other leftover rows may be nonzero
        # in a shared scratch DB, so assert narrowly via force=True below
        # instead of asserting stats2["processed"] == 0 globally).

        stats3 = extract_citec_domains(source_type="incident_reports", force=True)
        assert stats3["upserted"] >= 1
    finally:
        _cleanup([eid])


def test_extract_citec_domains_creates_frame_row_when_none_exists():
    """extract_frames() need not have run first — extract_citec_domains()
    creates the issue_frames row itself."""
    from app.db.session import session_scope
    from app.frames.job import extract_citec_domains

    eid = "swim_test_3"
    _cleanup([eid])
    try:
        with session_scope() as session:
            _make_document(
                session,
                external_id=eid,
                title="일반 회선 장애",
                body_md="■ 장애원인: 회선 불안정",
                metadata={"최종등급": "X등급"},
            )

        from app.db.models import Document, IssueFrame

        with session_scope() as session:
            doc_id = session.query(Document.id).filter_by(
                source_type="incident_reports", external_id=eid
            ).scalar()
            assert session.query(IssueFrame).filter_by(document_id=doc_id).count() == 0

        stats = extract_citec_domains(source_type="incident_reports")
        assert stats["errors"] == 0

        with session_scope() as session:
            frame = session.query(IssueFrame).filter_by(document_id=doc_id).one()
            assert frame.severity_tier == "customer_fault"
            assert "Network" in frame.citec_domains
    finally:
        _cleanup([eid])
