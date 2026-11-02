"""doc-expansion FTS branch in app.retrieval.search (SEARCH_DOC_EXPANSION).

Unit tests for the env parsers run everywhere; the DB tests need
CONFLUENCE_SYNC_TEST_DATABASE_URL (use scripts/scratch_db_test.sh).
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest

from app.retrieval import search as S

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")


# ---------------------------------------------------------------- unit


@pytest.mark.parametrize(
    "val,expected",
    [(None, "off"), ("", "off"), ("off", "off"), ("grounded", "grounded"), ("ALL", "all"),
     (" all ", "all"), ("yes", "off"), ("1", "off")],
)
def test_expansion_mode_parsing(monkeypatch, val, expected):
    if val is None:
        monkeypatch.delenv("SEARCH_DOC_EXPANSION", raising=False)
    else:
        monkeypatch.setenv("SEARCH_DOC_EXPANSION", val)
    assert S._expansion_mode() == expected


@pytest.mark.parametrize(
    "val,expected",
    [(None, 0.5), ("0.8", 0.8), ("abc", 0.5), ("", 0.5), ("0", 0.0), ("-1", 0.0)],
)
def test_expansion_weight_parsing(monkeypatch, val, expected):
    if val is None:
        monkeypatch.delenv("SEARCH_DOC_EXPANSION_WEIGHT", raising=False)
    else:
        monkeypatch.setenv("SEARCH_DOC_EXPANSION_WEIGHT", val)
    assert S._expansion_weight() == expected


# ---------------------------------------------------------------- DB

db = pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL")

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

_PREFIX = "test_docexp_search"
_KW = "zzexpkwalpha"
_GKW = "zzexpkwbeta"


@pytest.fixture
def _db():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        session.query(Document).filter(Document.id.like(f"{_PREFIX}:%")).delete(synchronize_session=False)
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _add_doc(session, name, *, source_type="tech_repo", n_chunks=2, inactive0=False, body=None):
    from sqlalchemy import text as sql_text

    from app.db.models import Chunk, Document

    doc_id = f"{_PREFIX}:{name}"
    body = body or f"plain body for {name}"
    session.add(Document(
        id=doc_id, source_type=source_type, external_id=name, title=f"title {name}", body_md=body,
        metadata_={}, content_hash=hashlib.sha256(body.encode()).hexdigest(),
        evidence_grade="A", status="active",
    ))
    session.flush()
    ids = []
    for o in range(n_chunks):
        cid = str(uuid.uuid4())
        ids.append(cid)
        session.add(Chunk(id=cid, document_id=doc_id, ordinal=o, text=f"{body} {o}",
                          header_context="", is_active=not (inactive0 and o == 0)))
        session.flush()
        session.execute(sql_text("UPDATE chunks SET tsv = to_tsvector('simple', :t) WHERE id = :id"),
                        {"t": f"{body} {o}", "id": cid})
    return doc_id, ids


def _expand(doc_id, keywords, grounded, *, summary="", content_hash=None):
    from app.db.models import Document
    from app.db.session import session_scope
    from app.docexp import store

    if content_hash is None:
        with session_scope() as session:
            content_hash = session.get(Document, doc_id).content_hash
    store.upsert(doc_id, {"summary": summary, "keywords": keywords, "grounded": grounded},
                 rule="t", model="t", run_id="t", content_hash=content_hash)


def _fts(q, **filters):
    from app.db.session import session_scope

    req = S.SearchRequest(q=q, filters=S.SearchFilters(**filters))
    with session_scope() as session:
        return S.fts_search(session, req)


@db
def test_mode_off_ignores_expansion(_db, monkeypatch):
    from app.db.session import session_scope

    monkeypatch.delenv("SEARCH_DOC_EXPANSION", raising=False)
    with session_scope() as s:
        d, ids = _add_doc(s, "b")
    _expand(d, [_KW], [])
    assert _fts(_KW) == []


@db
def test_all_returns_representative_lowest_ordinal_active_chunk(_db, monkeypatch):
    from app.db.session import session_scope

    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "all")
    with session_scope() as s:
        d, ids = _add_doc(s, "b", n_chunks=3, inactive0=True)
    _expand(d, [_KW], [])
    assert _fts(_KW) == [ids[1]]  # ordinal 0 inactive -> ordinal 1


@db
def test_grounded_only_matches_grounded_keywords(_db, monkeypatch):
    from app.db.session import session_scope

    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "grounded")
    with session_scope() as s:
        d, ids = _add_doc(s, "b")
    _expand(d, [_KW, _GKW], [_GKW])
    assert _fts(_KW) == []
    assert _fts(_GKW) == [ids[0]]


@db
def test_source_type_filter_applies(_db, monkeypatch):
    from app.db.session import session_scope

    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "all")
    with session_scope() as s:
        d1, i1 = _add_doc(s, "a", source_type="tech_repo")
        d2, i2 = _add_doc(s, "b", source_type="faq")
    _expand(d1, [_KW], [])
    _expand(d2, [_KW], [])
    assert _fts(_KW, source_type="faq") == [i2[0]]
    assert set(_fts(_KW)) == {i1[0], i2[0]}


@db
def test_doc_without_expansion_unaffected_and_weight_zero_off(_db, monkeypatch):
    from app.db.session import session_scope

    with session_scope() as s:
        d1, i1 = _add_doc(s, "plain")          # no expansion; body "plain body for plain"
        d2, i2 = _add_doc(s, "exp")            # body "plain body for exp"
    _expand(d2, [_KW], [])
    monkeypatch.delenv("SEARCH_DOC_EXPANSION", raising=False)
    off = _fts("plain")
    assert i1[0] in off and i2[0] in off       # both bodies match "plain"
    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "all")
    assert _fts("plain") == off                # expansion does not match -> identical result
    monkeypatch.setenv("SEARCH_DOC_EXPANSION_WEIGHT", "0")
    assert _fts(_KW) == []


@db
def test_stale_expansion_ignored(_db, monkeypatch):
    from app.db.session import session_scope

    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "all")
    with session_scope() as s:
        d, ids = _add_doc(s, "b")
    _expand(d, [_KW], [], content_hash="old-hash")
    assert _fts(_KW) == []
    _expand(d, [_KW], [])  # fresh hash
    assert _fts(_KW) == [ids[0]]


@db
def test_competing_body_hit_does_not_hide_expansion_doc(_db, monkeypatch):
    from app.db.session import session_scope

    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "all")
    with session_scope() as s:
        da, ia = _add_doc(s, "a", body="오라클 컴파일 오류 발생 시 확인 절차")
        db_, ib = _add_doc(s, "b", body="월간 점검 회의록 안건 정리")
    _expand(db_, ["프로시저 컴파일 지연", "오라클 DB"], [])
    res = _fts("프로시저 컴파일 지연")
    assert ib[0] in res and ia[0] in res


@db
def test_expansion_only_doc_enters_final_list_among_many_body_hits(_db, monkeypatch):
    from app.db.session import session_scope

    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "all")
    with session_scope() as s:
        for n in range(60):
            _add_doc(s, f"m{n}", n_chunks=1, body=f"컴파일 작업 로그 {n}번째 배치 결과 확인")
        db_, ib = _add_doc(s, "target", n_chunks=1, body="월간 점검 회의록 안건 정리")
    _expand(db_, ["프로시저 컴파일 지연"], [])
    monkeypatch.delenv("SEARCH_DOC_EXPANSION")
    assert ib[0] not in _fts("프로시저 컴파일 지연")
    monkeypatch.setenv("SEARCH_DOC_EXPANSION", "all")
    assert ib[0] in _fts("프로시저 컴파일 지연")  # fts_limit=40 < 60 body hits
