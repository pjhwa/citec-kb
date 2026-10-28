"""A5 — resolve_ticket / GET /v1/tickets/{id} (scratch DB only)."""

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

IDS = ("CITECTS-900001", "26059990001")


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

    wipe()
    with session_scope() as s:
        for eid, st in (("CITECTS-900001", "support_history"), ("26059990001", "incident_reports")):
            s.add(Document(id=uuid.uuid4().hex, source_type=st, external_id=eid, title=f"t {eid}", body_md="b",
                           metadata_={}, content_hash=uuid.uuid4().hex, status="active", source_uri="u"))
    yield
    wipe()


def test_case_and_format_variants_resolve():
    from app.tickets.query import resolve_ticket

    for raw in ("CITECTS-900001", "citects-900001", "Citects 900001", "citects900001", "  CITECTS_900001 "):
        r = resolve_ticket(raw)
        assert r["row"] and r["row"]["external_id"] == "CITECTS-900001", raw
    assert "normalized_from" not in resolve_ticket("CITECTS-900001")["row"]
    assert resolve_ticket("citects-900001")["row"]["normalized_from"] == "citects-900001"


def test_digits_only_suggests_but_does_not_resolve():
    from app.tickets.query import resolve_ticket

    r = resolve_ticket("900001")
    assert r["row"] is None and r["did_you_mean"] == "CITECTS-900001"


def test_missing_ticket_has_no_suggestion():
    from app.tickets.query import resolve_ticket

    assert resolve_ticket("CITECTS-99999999") == {"row": None, "did_you_mean": None}
    assert resolve_ticket("99999999") == {"row": None, "did_you_mean": None}


def test_swim_digit_ids_still_exact():
    from app.tickets.query import resolve_ticket

    r = resolve_ticket("26059990001", source_type="incident_reports")
    assert r["row"] and r["row"]["external_id"] == "26059990001"


def test_http_404_shapes():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    a = c.get("/v1/tickets/900001")
    assert a.status_code == 404 and a.json()["detail"]["did_you_mean"] == "CITECTS-900001"
    b = c.get("/v1/tickets/CITECTS-99999999")
    assert b.status_code == 404 and b.json()["detail"] == "ticket not found"
    ok = c.get("/v1/tickets/citects-900001")
    assert ok.status_code == 200 and ok.json()["normalized_from"] == "citects-900001"
