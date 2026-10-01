"""DB-touching tests for app.frames.job.extract_frames staleness handling
(P0-C: docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §6).

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
scratch Postgres+pgvector DB with the alembic schema applied (same opt-in
convention as tests/test_confluence_sync_db.py — see that file's docstring
for how to stand one up). Never the live citec_knowledge DB.
"""

from __future__ import annotations

import hashlib
import json
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


_PREFIX = "test_frames_staleness"


def _make_doc(session, *, external_id: str, title: str, body_md: str) -> str:
    from app.db.models import Document

    doc_id = f"{_PREFIX}:{external_id}"
    payload = f"{title}\n{body_md}"
    doc = Document(
        id=doc_id,
        source_id="fs_raw",
        source_type="support_history",
        external_id=external_id,
        title=title,
        body_md=body_md,
        metadata_={},
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
        doc_ids = [f"{_PREFIX}:{eid}" for eid in external_ids]
        session.execute(delete(IssueFrame).where(IssueFrame.document_id.in_(doc_ids)))
        session.execute(delete(Document).where(Document.id.in_(doc_ids)))


_WIP_MD = """## 원본 내용
### 조치
GPU 트래픽의 버스트 발생 여부를 확인하기 위해 모니터링을 진행 중이며 차단 방법과 대응 방안을 계속 검토 중이다.
"""

_DONE_MD = """## 원본 내용
### 조치
5/13 17:00 PB2 Fabric 연결 변경 완료
"""


def test_second_run_without_change_skips_as_fresh_not_reprocessed():
    from app.db.session import session_scope
    from app.frames.job import extract_frames

    eid = "CITECTS-90001"
    _cleanup([eid])
    try:
        with session_scope() as session:
            _make_doc(session, external_id=eid, title="[CITECTS-90001] test", body_md=_WIP_MD)

        first = extract_frames(source_type="support_history", limit=1000)
        assert first["upserted"] >= 1

        from sqlalchemy import select

        from app.db.models import IssueFrame

        with session_scope() as session:
            frame_after_first = session.scalar(
                select(IssueFrame).where(IssueFrame.document_id == f"{_PREFIX}:{eid}")
            )
            updated_at_1 = frame_after_first.updated_at

        second = extract_frames(source_type="support_history", limit=1000)
        # global counters are shared across whatever other CITECTS-% fixtures
        # this scratch DB happens to hold this run (e.g. other test files'
        # rows), so assert on our own document's row, not on aggregate stats:
        # unchanged body_md + unchanged extractor_version must leave its
        # frame untouched (updated_at unchanged) — skipped as fresh, not
        # silently reprocessed, and not silently skipped-forever either
        # (covered by test_body_change_triggers_regeneration_not_skip below).
        assert second["upserted"] >= 0  # sanity: call succeeded
        with session_scope() as session:
            frame_after_second = session.scalar(
                select(IssueFrame).where(IssueFrame.document_id == f"{_PREFIX}:{eid}")
            )
            assert frame_after_second.updated_at == updated_at_1
    finally:
        _cleanup([eid])


def test_body_change_triggers_regeneration_not_skip():
    """This is the exact P0-C bug: before the fix, extract_frames(force=False)
    skipped a document purely because an IssueFrame row already existed, even
    though Document.body_md had since changed from a stale "in progress"
    summary to a resolved original. It must now regenerate."""
    from app.db.session import session_scope
    from app.frames.job import extract_frames

    eid = "CITECTS-90002"
    _cleanup([eid])
    try:
        with session_scope() as session:
            _make_doc(session, external_id=eid, title="[CITECTS-90002] test", body_md=_WIP_MD)
        first = extract_frames(source_type="support_history", limit=1000)
        assert first["upserted"] >= 1

        with session_scope() as session:
            from app.db.models import Document

            doc = session.get(Document, f"{_PREFIX}:{eid}")
            doc.body_md = _DONE_MD
            payload = f"{doc.title}\n{_DONE_MD}"
            doc.content_hash = hashlib.sha256(payload.encode()).hexdigest()

        second = extract_frames(source_type="support_history", limit=1000)
        # >= 1, not skipped_fresh == 0: the scratch DB's global counters
        # cover every CITECTS-% support_history row other test files may
        # have left behind this run, not just this document — assert on our
        # own row's content below instead of trusting the aggregate.
        assert second["regenerated_stale"] >= 1

        with session_scope() as session:
            from sqlalchemy import select

            from app.db.models import IssueFrame

            frame = session.scalar(
                select(IssueFrame).where(IssueFrame.document_id == f"{_PREFIX}:{eid}")
            )
            assert frame is not None
            assert frame.resolution is not None
            assert "완료" in frame.resolution
    finally:
        _cleanup([eid])


def test_extractor_version_bump_forces_one_regeneration_pass():
    """Simulate a pre-existing frame extracted before body_hash/extractor_version
    existed (both NULL, per the migration's docstring) — it must not be
    trusted as fresh forever; it gets exactly one regeneration pass."""
    from app.db.session import session_scope
    from app.frames.job import extract_frames

    eid = "CITECTS-90003"
    _cleanup([eid])
    try:
        with session_scope() as session:
            doc_id = _make_doc(session, external_id=eid, title="[CITECTS-90003] test", body_md=_DONE_MD)

        with session_scope() as session:
            import uuid

            from app.db.models import IssueFrame

            session.add(
                IssueFrame(
                    id=str(uuid.uuid4()),
                    document_id=doc_id,
                    resolution="이전 버전 추출기 결과",
                    quality=0.5,
                    body_hash=None,
                    extractor_version=None,
                )
            )

        result = extract_frames(source_type="support_history", limit=1000)
        assert result["regenerated_stale"] >= 1
    finally:
        _cleanup([eid])
