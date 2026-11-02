"""Slice sampling against a scratch DB only."""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to run")
if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

TAG = "evs_test_"


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
            s.query(Document).filter(Document.external_id.like(TAG + "%")).delete(synchronize_session=False)

    wipe()
    yield
    wipe()
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _add(ext, st, title, body="본문 내용입니다"):
    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as s:
        s.add(Document(id=uuid.uuid4().hex, source_type=st, external_id=TAG + ext, title=title, body_md=body,
                       metadata_={}, content_hash=uuid.uuid4().hex, status="active", source_uri="https://x/" + ext))


def test_sample_docs_is_deterministic_filters_slice_and_short_titles():
    from app.db.session import session_scope
    from app.evalv2 import synth

    for i in range(6):
        _add(f"m{i}", "confluence_map", f"맵 문서 제목 번호 {i} 입니다")
    _add("t1", "support_history", "티켓 제목입니다 길이충분")
    _add("short", "confluence_map", "짧음")
    with session_scope() as s:
        a = synth.sample_docs(s, "pointer", 3, seed="s1", prefix=TAG)
        b = synth.sample_docs(s, "pointer", 3, seed="s1", prefix=TAG)
        c = synth.sample_docs(s, "pointer", 3, seed="s2", prefix=TAG)
    assert [d["external_id"] for d in a] == [d["external_id"] for d in b]
    assert len(a) == 3 and all(d["external_id"].startswith(TAG + "m") for d in a)
    assert len(c) == 3  # a different seed still returns a full sample
    assert a[0]["excerpt"] and a[0]["title"]
