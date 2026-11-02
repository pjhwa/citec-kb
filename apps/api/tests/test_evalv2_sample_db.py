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


def _add(ext, st, title, body="본문 내용입니다 " * 40):
    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as s:
        s.add(Document(id=uuid.uuid4().hex, source_type=st, external_id=TAG + ext, title=title, body_md=body,
                       metadata_={}, content_hash=uuid.uuid4().hex, status="active", source_uri="https://x/" + ext))


def test_sample_docs_is_deterministic_filters_slice_and_short_titles():
    from app.db.session import session_scope
    from app.evalv2 import synth

    for i in range(8):
        _add(f"m{i}", "confluence_map", f"맵 문서 제목 번호 {i} 입니다")
    _add("t1", "support_history", "티켓 제목입니다 길이충분")
    _add("short", "confluence_map", "짧음")
    with session_scope() as s:
        a = synth.sample_docs(s, "pointer", 3, seed="s1", prefix=TAG)
        b = synth.sample_docs(s, "pointer", 3, seed="s1", prefix=TAG)
        c = synth.sample_docs(s, "pointer", 3, seed="s2", prefix=TAG)
    assert [d["external_id"] for d in a] == [d["external_id"] for d in b]
    assert len(a) == 6 and all(d["external_id"].startswith(TAG + "m") for d in a)
    assert len(c) == 6  # a different seed still returns a full sample
    assert a[0]["excerpt"] and a[0]["title"]


def test_sample_docs_vocab_needs_latin_term_and_pointer_short_body_ok_and_like_escaped():
    from app.db.session import session_scope
    from app.evalv2 import synth

    long = "본문 내용입니다 " * 40
    _add("v1", "tech_repo", "서버 점검 절차 안내 문서", body=long + " NIC bonding")   # latin in body
    _add("v2", "tech_repo", "스위치 포트 구성 안내 문서", body=long)                    # no latin -> excluded
    _add("v3", "tech_repo", "VLAN 구성 안내 문서입니다", body=long)                      # latin in title
    _add("v4", "tech_repo", "Latin 있지만 본문이 짧다", body="짧다")                      # body too short
    _add("v5", "support_history", "티켓 제목 SQL 길이충분", body=long)                   # not a vocab source now
    _add("p1", "confluence_map", "포인터 문서 제목 입니다", body="x" * 25)               # pointer-only ok
    _add("p2", "confluence_map", "포인터 문서 제목 짧음", body="x")                       # < 20
    _add("s1", "support_history", "증상 문서 제목 입니다", body="짧은 본문")             # symptom needs 200
    _add("s2", "support_history", "증상 문서 제목 충분", body=long)
    with session_scope() as s:
        v = {d["external_id"] for d in synth.sample_docs(s, "vocab", 10, seed="s", prefix=TAG)}
        p = {d["external_id"] for d in synth.sample_docs(s, "pointer", 10, seed="s", prefix=TAG)}
        y = {d["external_id"] for d in synth.sample_docs(s, "symptom", 10, seed="s", prefix=TAG)}
        w = synth.sample_docs(s, "pointer", 10, seed="s", prefix="evs_%")  # wildcard must not match
    assert v == {TAG + "v1", TAG + "v3"}
    assert p == {TAG + "p1"}
    assert y == {TAG + "s2", TAG + "v5"}  # v5 is support_history -> symptom source, not vocab
    assert w == []


def test_sample_docs_limit_is_oversampled():
    from app.db.session import session_scope
    from app.evalv2 import synth

    for i in range(8):
        _add(f"o{i}", "confluence_map", f"포인터 문서 제목 번호 {i} 입니다")
    with session_scope() as s:
        assert len(synth.sample_docs(s, "pointer", 3, seed="s", prefix=TAG)) == 6
