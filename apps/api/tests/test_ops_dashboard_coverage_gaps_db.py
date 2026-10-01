"""DB-touching tests for app.ops.dashboard.coverage_gaps (P1-B,
docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §9:
"source→raw→document→active chunk→현재 model embedding→frame 각 단계의
건수를 source ID로 대조하라").

Same opt-in-DB convention as test_confluence_sync_db.py.
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


_PREFIX = "test_coverage_gaps"


def _make_doc(session, *, external_id: str, source_type: str, metadata: dict) -> str:
    from app.db.models import Document

    doc_id = f"{_PREFIX}:{source_type}:{external_id}"
    payload = f"{external_id}\n{source_type}"
    doc = Document(
        id=doc_id,
        source_id="fs_raw",
        source_type=source_type,
        external_id=external_id,
        title="test",
        body_md="test body",
        metadata_=metadata,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        version=1,
        status="active",
        evidence_grade="B",
    )
    session.merge(doc)
    return doc_id


def _cleanup(doc_ids: list[str]) -> None:
    from sqlalchemy import delete

    from app.db.models import Document, IssueFrame
    from app.db.session import session_scope

    with session_scope() as session:
        session.execute(delete(IssueFrame).where(IssueFrame.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))


def test_confluence_map_reports_missing_ancestor_ids_source_version_tech_relevant():
    """Delta-based, not absolute-count-based: the scratch DB may carry other
    fixtures' confluence_map rows from earlier tests in the same run (same
    fragility class as the skipped_fresh==0 issue fixed earlier this
    session — see test_frames_job_staleness_db.py's history), so this
    compares coverage_gaps before/after adding exactly our own two
    documents instead of asserting on the global total_active/missing
    counts directly. A bug that counted *every* row (or zero rows) as
    missing, regardless of content, would fail these delta assertions even
    when other fixtures are present; the old absolute `missing < total`
    check would not have caught that."""
    from app.db.session import session_scope
    from app.ops.dashboard import coverage_gaps

    doc_with = f"{_PREFIX}:confluence_map:1"
    doc_without = f"{_PREFIX}:confluence_map:2"
    _cleanup([doc_with, doc_without])
    try:
        with session_scope() as session:
            before = coverage_gaps(session)["confluence_map"]

        with session_scope() as session:
            _make_doc(
                session,
                external_id="1",
                source_type="confluence_map",
                metadata={"ancestor_ids": ["1"], "source_version": 3, "tech_relevant": "relevant"},
            )
            _make_doc(session, external_id="2", source_type="confluence_map", metadata={})

        with session_scope() as session:
            after = coverage_gaps(session)["confluence_map"]

        # +2 total_active (one fully populated, one fully empty) → exactly
        # +1 to each missing_* count, never +0 or +2.
        assert after["total_active"] - before["total_active"] == 2
        assert after["missing_ancestor_ids"] - before["missing_ancestor_ids"] == 1
        assert after["missing_source_version"] - before["missing_source_version"] == 1
        assert after["missing_tech_relevant"] - before["missing_tech_relevant"] == 1
    finally:
        _cleanup([doc_with, doc_without])


def test_support_history_reports_missing_frame():
    """Delta-based for the same reason as the confluence_map test above."""
    from app.db.session import session_scope
    from app.ops.dashboard import coverage_gaps

    doc_with_frame = f"{_PREFIX}:support_history:CITECTS-1"
    doc_without_frame = f"{_PREFIX}:support_history:CITECTS-2"
    _cleanup([doc_with_frame, doc_without_frame])
    try:
        with session_scope() as session:
            before = coverage_gaps(session)["support_history"]

        with session_scope() as session:
            _make_doc(session, external_id="CITECTS-1", source_type="support_history", metadata={})
            _make_doc(session, external_id="CITECTS-2", source_type="support_history", metadata={})

        with session_scope() as session:
            import uuid

            from app.db.models import IssueFrame

            session.add(IssueFrame(id=str(uuid.uuid4()), document_id=doc_with_frame, quality=0.5))

        with session_scope() as session:
            after = coverage_gaps(session)["support_history"]

        assert after["total_active"] - before["total_active"] == 2
        # one of the two new docs has a frame, the other doesn't → +1, not
        # +0 or +2.
        assert after["missing_frame"] - before["missing_frame"] == 1
        # confluence_map-only fields must not appear for support_history
        assert "missing_ancestor_ids" not in after
        assert "missing_tech_relevant" not in after
    finally:
        _cleanup([doc_with_frame, doc_without_frame])


def test_source_type_with_no_documents_is_zero_not_absent():
    from app.db.session import session_scope
    from app.ops.dashboard import coverage_gaps

    with session_scope() as session:
        gaps = coverage_gaps(session)
    # tuning_ai/dept_archive etc. may have zero rows in a fresh scratch DB —
    # they must still appear with total_active=0, not be missing from the
    # dict (a caller iterating gaps.items() must not need a fallback).
    for st in ("confluence_map", "tech_repo", "confluence_docs"):
        assert st in gaps
        assert gaps[st]["total_active"] >= 0
