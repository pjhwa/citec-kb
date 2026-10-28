"""A4 — section/area 정규화 (scratch DB 전용)."""

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

TAG = "fa_test_"


@pytest.fixture(autouse=True)
def _db():
    from app.db import session as db_session
    from app.db.models import Document
    from app.db.session import session_scope
    from app.retrieval import filter_args
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    filter_args.clear_cache()

    def wipe():
        with session_scope() as s:
            s.query(Document).filter(Document.external_id.like(TAG + "%")).delete(synchronize_session=False)

    wipe()
    with session_scope() as s:
        s.add(Document(id=uuid.uuid4().hex, source_type="checkitem", external_id=TAG + "1", title="t",
                       body_md="b", metadata_={}, content_hash=uuid.uuid4().hex, status="active",
                       source_uri="u", domain="linux"))
    filter_args.clear_cache()
    yield
    wipe()
    filter_args.clear_cache()


def test_source_type_aliases_and_case():
    from app.retrieval.filter_args import normalize_source_type as n

    assert n("checkitems") == "checkitem"
    assert n("CheckItem") == "checkitem"
    assert n("dept-archive") == "dept_archive"
    assert n("  support_history ") == "support_history"
    assert n("") is None and n(None) is None
    assert n("confluence_map") == "confluence_map"


def test_unknown_source_type_lists_allowed():
    from app.retrieval.filter_args import normalize_source_type as n

    with pytest.raises(ValueError) as e:
        n("pisa_checklist_typo", field="section")
    assert "section" in str(e.value) and "checkitem" in str(e.value)


def test_canonical_domain_case_and_warning():
    from app.retrieval.filter_args import canonical_domain as c

    assert c("Linux") == ("linux", None)
    assert c("LINUX")[0] == "linux"
    assert c("") == (None, None)
    val, warn = c("not_a_domain")
    assert val == "not_a_domain" and warn and "not_a_domain" in warn


def test_v1_search_rejects_bad_section_and_normalizes_alias(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    bad = client.post("/v1/search", json={"q": "x", "filters": {"source_type": "bogus_type"}})
    assert bad.status_code == 400 and "allowed" in bad.json()["detail"]
    bad2 = client.post("/v1/search", json={"q": "x", "filters": {"exclude_source_types": ["nope"]}})
    assert bad2.status_code == 400

    # 별칭은 통과해야 한다(임베딩 모델 없이도 FTS 경로로 200)
    import app.embed.model as em

    monkeypatch.setattr(em, "embed_query", lambda q: (_ for _ in ()).throw(RuntimeError("no model")))
    ok = client.post("/v1/search", json={"q": "x", "multi_query": False,
                                         "filters": {"source_type": "checkitems", "domain": "LINUX"}})
    assert ok.status_code == 200
    assert "warnings" not in ok.json()
    warn = client.post("/v1/search", json={"q": "x", "multi_query": False, "filters": {"domain": "zzz_none"}})
    assert warn.status_code == 200 and warn.json()["warnings"]


def test_legacy_wiki_search_rejects_unknown_section_but_keeps_known_names():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/wiki/search", params={"q": "x", "section": "bogus"}).status_code == 400
    for sec in ("", "general", "checkitems", "sql_tuning", "synthesis", "dept-archive"):
        assert client.get("/api/wiki/search", params={"q": "", "section": sec}).status_code == 200, sec
