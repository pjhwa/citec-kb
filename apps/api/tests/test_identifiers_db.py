"""A6 — 코퍼스에 없는 식별자 경고 (scratch DB 전용)."""

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

EID = "CITECTS-900777"


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
            s.query(Document).filter(Document.external_id == EID).delete(synchronize_session=False)

    wipe()
    with session_scope() as s:
        s.add(Document(id=uuid.uuid4().hex, source_type="support_history", external_id=EID, title="t",
                       body_md="b", metadata_={}, content_hash=uuid.uuid4().hex, status="active", source_uri="u"))
    yield
    wipe()


def test_missing_identifiers_checks_corpus_case_insensitively():
    from app.db.session import session_scope
    from app.retrieval.identifiers import check_query_identifiers

    with session_scope() as s:
        assert check_query_identifiers(s, "citects-900777 내용", "strong") == ("strong", [])
        assert check_query_identifiers(s, "CITECTS-99999999 내용", "strong") == ("weak", ["CITECTS-99999999"])
        t, m = check_query_identifiers(s, "CITECTS-900777 와 CITECTS-99999999", "strong")
        assert t == "strong" and m == ["CITECTS-99999999"]
        assert check_query_identifiers(s, "Redis 장애", "medium") == ("medium", [])


def test_v1_search_reports_missing_identifier(monkeypatch):
    from fastapi.testclient import TestClient

    import app.embed.model as em
    from app.main import app

    monkeypatch.setattr(em, "embed_query", lambda q: (_ for _ in ()).throw(RuntimeError("no model")))
    c = TestClient(app)
    bad = c.post("/v1/search", json={"q": "CITECTS-99999999 내용 알려줘", "multi_query": False})
    assert bad.status_code == 200 and bad.json()["missing_identifiers"] == ["CITECTS-99999999"]
    assert bad.json()["trust_retrieval"] in {"weak", "empty"}
    good = c.post("/v1/search", json={"q": f"{EID} 내용", "multi_query": False})
    assert good.status_code == 200 and good.json()["missing_identifiers"] == []
    mq = c.post("/v1/search", json={"q": "CITECTS-99999999 내용 알려줘", "multi_query": True})
    assert mq.status_code == 200 and mq.json()["missing_identifiers"] == ["CITECTS-99999999"]
