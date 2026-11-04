"""B/C — system registrant classification and evidence_ref prefixes (scratch DB)."""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


# ---- pure classification (no DB) -------------------------------------------------

@pytest.fixture()
def allowlist(tmp_path, monkeypatch):
    import json

    import app.failure_buckets.service as svc

    (tmp_path / "system_registrants.json").write_text(json.dumps({"systems": ["insight-ops", "agent-room"]}))

    class _S:
        config_dir = tmp_path

    monkeypatch.setattr(svc, "get_settings", lambda: _S)
    monkeypatch.setattr(svc, "_registrants_cache", (None, frozenset()))
    return svc


def test_allowlisted_system_is_system(allowlist):
    assert allowlist.classify_registrant("insight-ops@v1.4") == ("system", None)
    assert allowlist.classify_registrant("agent-room@v0.9") == ("system", None)
    assert allowlist.classify_registrant("insight-ops@v1.4-test") == ("system", None)


def test_malformed_system_name_warns_but_is_human(allowlist):
    kind, warn = allowlist.classify_registrant("Insight Ops@v1")
    assert kind == "human" and warn and "형식" in warn


def test_unlisted_system_name_warns(allowlist):
    kind, warn = allowlist.classify_registrant("sos-analyzer@v0.1")
    assert kind == "human" and warn and "허용 목록" in warn


def test_plain_human_and_email_get_no_warning(allowlist):
    assert allowlist.classify_registrant("홍길동") == ("human", None)
    assert allowlist.classify_registrant(None) == ("human", None)
    assert allowlist.classify_registrant("someone@example.com") == ("human", None)


def test_missing_allowlist_file_does_not_raise(tmp_path, monkeypatch):
    import app.failure_buckets.service as svc

    class _S:
        config_dir = tmp_path

    monkeypatch.setattr(svc, "get_settings", lambda: _S)
    monkeypatch.setattr(svc, "_registrants_cache", (None, frozenset()))
    assert svc.classify_registrant("insight-ops@v1") [0] == "human"


# ---- API responses (DB) ------------------------------------------------------------

db = pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL")


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import session as db_session
    from app.main import app
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kw: {"embedded": 0})
    yield TestClient(app)
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("delete from documents where id in (select document_id from failure_buckets where created_by like 'regpy-%')"))
        s.execute(text("delete from failure_buckets where created_by like 'regpy-%'"))


def _post(client, **over):
    tag = uuid.uuid4().hex[:8]
    body = {
        "bucket_name": f"reg-test {tag}", "fb_domain": "network", "symptom": f"zzrg{tag}",
        "discriminating_signals": [f"zzrg{tag} sig"], "root_cause": "rc", "recommended_action": "ra",
        "evidence_ref": "confluence:123456", "created_by": "regpy-x@v1",
    }
    body.update(over)
    r = client.post("/v1/failure-buckets", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@db
def test_response_has_registrant_kind_and_warnings(client):
    d = _post(client)
    assert d["registrant_kind"] == "human"  # regpy-x is not allowlisted
    assert "created_by_warning" in d  # looks like a system registrant but unlisted
    d2 = _post(client, created_by="regpy-홍길동")
    assert d2["registrant_kind"] == "human" and "created_by_warning" not in d2


@db
@pytest.mark.parametrize("ref", ["insight:ISS-1#report=r1", "agent-room:room1/case2"])
def test_restricted_prefixes_are_known_with_note(client, ref):
    d = _post(client, evidence_ref=ref)
    assert "evidence_ref_warning" not in d
    assert "confluence:" in d["evidence_ref_note"]


@db
def test_confluence_has_no_note_and_paired_ref_has_no_note(client):
    d = _post(client, evidence_ref="confluence:99")
    assert "evidence_ref_note" not in d and "evidence_ref_warning" not in d
    d2 = _post(client, evidence_ref="insight:ISS-1#report=r1 confluence:99")
    assert "evidence_ref_note" not in d2


@db
def test_unknown_prefix_still_warns_and_lists_new_prefixes(client):
    d = _post(client, evidence_ref="free text somewhere")
    assert "insight:" in d["evidence_ref_warning"] and "agent-room:" in d["evidence_ref_warning"]


@db
def test_ops_exposes_24h_counts_per_created_by(client):
    from app.db.session import session_scope
    from app.ops.dashboard import recent_failure_buckets

    _post(client, created_by="regpy-count@v1")
    _post(client, created_by="regpy-count@v1")
    with session_scope() as s:
        out = recent_failure_buckets(s)
    assert out["created_by_24h"]["regpy-count@v1"] == 2
