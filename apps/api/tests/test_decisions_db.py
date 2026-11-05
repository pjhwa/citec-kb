"""Decisions against a scratch Postgres (scripts/scratch_db_test.sh)."""

from __future__ import annotations

import os
import unicodedata
import uuid

import pytest

_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")
if _DSN:
    os.environ["DATABASE_URL"] = _DSN

pytestmark = pytest.mark.skipif(not _DSN, reason="needs scratch DB")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.settings import get_settings  # noqa: E402

TITLE = "PIXEL (엑셀기반 PISA진단)_2026.11.30 까지 제공"
OPTS = [{"key": "a", "label": "2026.11.30"}, {"key": "b", "label": "연장"}]


@pytest.fixture(autouse=True)
def _no_embedding(monkeypatch):
    from app.decisions import mirror

    monkeypatch.setattr(mirror, "_embed", lambda doc_id: None)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    get_settings.cache_clear()
    from app.main import app
    yield TestClient(app)
    get_settings.cache_clear()


def _doc(body: str = TITLE, eid: str | None = None) -> dict:
    from app.ingest.adapters import DocumentDraft
    from app.ingest.pipeline import upsert_document_from_draft

    eid = eid or f"T-{uuid.uuid4().hex[:8]}"
    d = DocumentDraft(source_type="tech_repo", external_id=eid, title=TITLE, body_md=body,
                      evidence_grade="A", source_uri=f"test://{eid}").finalize()
    return upsert_document_from_draft(d)


def _mutate_doc(doc_id: str, body: str) -> None:
    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("UPDATE documents SET body_md=:b, content_hash=:h, version=version+1 WHERE id=:i"),
                  {"b": body, "h": uuid.uuid4().hex, "i": doc_id})


def _propose(client, doc_id=None, quote="2026.11.30 까지 제공", **kw):
    ev = [{"quote": quote, "stance": "supports", "option_key": "a", "document_id": doc_id}] if doc_id else []
    r = client.post("/v1/decisions", json={"question": "PIXEL 제공 종료일은?", "options": OPTS,
                                          "ai_recommendation_key": "a", "evidence": ev, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _verdict(client, did, **kw):
    n = client.get(f"/v1/decisions/{did}/nonce").json()["nonce"]
    body = {"option_key": "a", "decided_by": "홍길동", "nonce": n, **kw}
    return client.post(f"/v1/decisions/{did}/verdict", json=body)


def test_evidence_gate_rejects_missing_quote_missing_doc_and_accepts_nfd(client):
    doc = _doc()
    d = _propose(client)
    did = d["id"]
    ok = lambda q, **kw: client.post(f"/v1/decisions/{did}/evidence", json={  # noqa: E731
        "quote": q, "stance": "supports", "option_key": "a", "document_id": kw.get("doc", doc["document_id"])})
    assert ok("존재하지 않는 문장").status_code == 400
    assert ok("2026.11.30", doc="no-such-doc").status_code == 400
    assert ok(unicodedata.normalize("NFD", "엑셀기반 PISA진단)_2026.11.30  까지 제공")).status_code == 200
    assert ok("엑셀기반 PISA진단)_2026.11.30 까지 제공").status_code == 409  # duplicate after normalisation


def test_verdict_needs_evidence_nonce_and_is_single_use(client):
    d = _propose(client)
    assert _verdict(client, d["id"]).status_code == 400  # no evidence
    doc = _doc()
    d = _propose(client, doc["document_id"])
    r = _verdict(client, d["id"])
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    n = client.get(f"/v1/decisions/{d['id']}/nonce").json()["nonce"]
    body = {"option_key": "a", "decided_by": "x", "nonce": n}
    assert client.post(f"/v1/decisions/{d['id']}/verdict", json=body).status_code == 200
    assert client.post(f"/v1/decisions/{d['id']}/verdict", json=body).status_code == 403  # reused
    assert client.post(f"/v1/decisions/{d['id']}/verdict", json={**body, "nonce": "bogus"}).status_code == 403


def test_nei_does_not_confirm_and_other_needs_text(client):
    d = _propose(client, _doc()["document_id"])
    r = _verdict(client, d["id"], option_key="NEI")
    assert r.status_code == 200 and r.json()["status"] == "proposed"
    assert _verdict(client, d["id"], option_key="OTHER").status_code == 400
    assert _verdict(client, d["id"], option_key="OTHER", free_text="12/31까지").status_code == 200


def test_external_only_needs_explicit_confirmation(client):
    d = client.post("/v1/decisions", json={"question": "연장 여부?", "options": OPTS, "evidence": [{
        "quote": "연장 검토", "stance": "supports", "option_key": "b",
        "external_url": "https://example.invalid/n", "external_snapshot": "연장 검토 중",
        "retrieved_at": "2026-11-03T00:00:00Z"}]}).json()
    assert d["external_only"] is True
    assert _verdict(client, d["id"], option_key="b").status_code == 400
    r = _verdict(client, d["id"], option_key="b", external_only_confirm=True)
    assert r.status_code == 200
    bad = client.post(f"/v1/decisions/{d['id']}/evidence", json={
        "quote": "x", "stance": "context", "external_url": "ftp://x", "external_snapshot": "s",
        "retrieved_at": "2026-11-03T00:00:00Z"})
    assert bad.status_code == 400


def test_verdict_rows_are_append_only_at_db_level(client):
    d = _propose(client, _doc()["document_id"])
    assert _verdict(client, d["id"]).status_code == 200
    from app.db.session import session_scope

    for sql in ("UPDATE decision_verdicts SET rationale='x'", "DELETE FROM decision_verdicts"):
        with pytest.raises(Exception, match="append-only"):
            with session_scope() as s:
                s.execute(text(sql))
    assert _verdict(client, d["id"], rationale="재확인").status_code == 200
    assert len(client.get(f"/v1/decisions/{d['id']}").json()["history"]) == 2


def test_changed_evidence_needs_recheck_then_keep_restores(client):
    doc = _doc()
    d = _propose(client, doc["document_id"])
    _verdict(client, d["id"])
    _mutate_doc(doc["document_id"], TITLE + " (v98)")
    card = client.get(f"/v1/decisions/{d['id']}").json()
    assert card["status"] == "confirmed" and card["effective_status"] == "needs_recheck"
    assert card["changed_pins"] == 1
    scan = client.post("/v1/decisions/_scan-staleness").json()
    assert d["id"] in scan["marked_needs_recheck"]
    assert client.get(f"/v1/decisions/{d['id']}").json()["status"] == "needs_recheck"
    assert _verdict(client, d["id"], keep=True).status_code == 200  # 한 번의 유지
    card = client.get(f"/v1/decisions/{d['id']}").json()
    assert card["status"] == "confirmed" and card["effective_status"] == "confirmed"
    assert card["changed_pins"] == 0


def test_keep_fails_loudly_when_quote_vanished(client):
    doc = _doc()
    d = _propose(client, doc["document_id"])
    _verdict(client, d["id"])
    _mutate_doc(doc["document_id"], "완전히 다른 본문")
    assert _verdict(client, d["id"], keep=True).status_code == 409


def test_supersede_hides_old_by_default_and_retire_restore(client):
    doc = _doc()
    old = _propose(client, doc["document_id"])
    _verdict(client, old["id"])
    new = _propose(client, doc["document_id"])
    assert client.post(f"/v1/decisions/{old['id']}/supersede", json={"new_decision_id": new["id"]}).status_code == 200
    ids = lambda **kw: {i["id"] for i in client.get("/v1/decisions", params=kw).json()["items"]}  # noqa: E731
    assert old["id"] not in ids() and old["id"] in ids(include_superseded=True)
    assert client.get(f"/v1/decisions/{new['id']}").json()["supersedes"] == [old["id"]]
    assert _verdict(client, old["id"]).status_code == 409
    assert client.post(f"/v1/decisions/{new['id']}/retire").status_code == 200
    assert new["id"] not in ids()
    assert client.post(f"/v1/decisions/{new['id']}/restore").json()["status"] == "proposed"


def test_option_lint_rejects_reserved_keys_via_api(client):
    r = client.post("/v1/decisions", json={"question": "q?", "options": [
        {"key": "NEI", "label": "x"}, {"key": "b", "label": "y"}]})
    assert r.status_code == 400


# ---- auth transition (acceptance #11) ------------------------------------------

def _auth_client(monkeypatch, mode="apikey"):
    import app.auth.principal as pr

    monkeypatch.setenv("AUTH_MODE", mode)
    monkeypatch.setenv("AUTH_TOKENS_JSON", '{"sen": {"sub":"s","name":"real-senior","roles":["senior"]},'
                       '"vw": {"sub":"v","name":"viewer1","roles":["viewer"]}}')
    get_settings.cache_clear()
    monkeypatch.setattr(pr, "_token_cache", None)
    from app.main import app
    return TestClient(app)


def test_auth_on_gates_and_ignores_forged_decided_by(monkeypatch, client):
    d = _propose(client, _doc()["document_id"])  # AUTH off: created anonymously
    c = _auth_client(monkeypatch)
    H = lambda t: {"X-API-Key": t}  # noqa: E731
    assert c.get(f"/v1/decisions/{d['id']}/nonce").status_code == 401
    assert c.get(f"/v1/decisions/{d['id']}/nonce", headers=H("vw")).status_code == 403
    n = c.get(f"/v1/decisions/{d['id']}/nonce", headers=H("sen")).json()["nonce"]
    r = c.post(f"/v1/decisions/{d['id']}/verdict", headers=H("sen"),
               json={"option_key": "a", "decided_by": "FORGED", "nonce": n})
    assert r.status_code == 200 and r.json()["verdict"]["decided_by"] == "real-senior"
    assert r.json()["verdict"]["auth_via"] == "apikey" and not r.json()["verdict"]["self_reported"]
    assert c.get("/v1/decisions").status_code == 200  # reads stay public
    assert c.post("/v1/decisions", json={"question": "q?", "options": OPTS}).status_code == 401


def test_auth_off_again_accepts_self_reported_name(monkeypatch, client):
    d = _propose(client, _doc()["document_id"])
    r = _verdict(client, d["id"], decided_by="자기신고")
    v = r.json()["verdict"]
    assert v["decided_by"] == "자기신고" and v["auth_via"] == "anonymous" and v["self_reported"]
    n = client.get(f"/v1/decisions/{d['id']}/nonce").json()["nonce"]
    assert client.post(f"/v1/decisions/{d['id']}/verdict", json={"option_key": "a", "nonce": n}).status_code == 400


# ---- search mirror (B2) --------------------------------------------------------

def _mirror_doc(did):
    from app.db.models import Document
    from app.db.session import session_scope
    from sqlalchemy import select

    with session_scope() as s:
        d = s.scalar(select(Document).where(Document.source_type == "confirmed_decision",
                                            Document.external_id == f"DEC-{did[:8]}"))
        if not d:
            return None
        return {"id": d.id, "status": d.status, "grade": d.evidence_grade, "hash": d.content_hash,
                "body": d.body_md, "meta": dict(d.metadata_ or {}), "version": d.version}


def test_mirror_created_on_confirm_with_grade_h_and_self_reported_marker(client):
    d = _propose(client, _doc()["document_id"])
    assert _mirror_doc(d["id"]) is None  # proposed: not searchable
    r = _verdict(client, d["id"], rationale="원문 제목 변경")
    assert r.json()["mirror_error"] is None
    m = _mirror_doc(d["id"])
    assert m["status"] == "active" and m["grade"] == "H"
    assert "결론: 2026.11.30" in m["body"] and "홍길동 (자기 신고)" in m["body"]
    assert "변경 이유: 원문 제목 변경" in m["body"] and "tech_repo/T-" in m["body"]
    assert "선택하지 않은 선택지:\n- 연장" in m["body"]


def test_mirror_hash_stable_across_recheck_state_changes(client):
    doc = _doc()
    d = _propose(client, doc["document_id"])
    _verdict(client, d["id"])
    before = _mirror_doc(d["id"])
    # evidence doc changes -> needs_recheck: mirror meaning unchanged -> no re-chunk/re-embed
    _mutate_doc(doc["document_id"], TITLE + " (v98)")
    client.post("/v1/decisions/_scan-staleness")
    after = _mirror_doc(d["id"])
    assert after["meta"]["recheck"] is True and before["meta"].get("recheck") is False
    assert after["hash"] == before["hash"] and after["version"] == before["version"]
    assert after["status"] == "active"


def test_mirror_status_follows_supersede_and_retire(client):
    doc = _doc()
    old = _propose(client, doc["document_id"])
    _verdict(client, old["id"])
    new = _propose(client, doc["document_id"])
    _verdict(client, new["id"])
    client.post(f"/v1/decisions/{old['id']}/supersede", json={"new_decision_id": new["id"]})
    assert _mirror_doc(old["id"])["status"] == "superseded"
    assert f"DEC-{old['id'][:8]}" in _mirror_doc(new["id"])["body"]  # 이전 기준 링크
    client.post(f"/v1/decisions/{new['id']}/retire")
    assert _mirror_doc(new["id"])["status"] == "archived"
    client.post(f"/v1/decisions/{new['id']}/restore")
    assert _mirror_doc(new["id"])["status"] == "active"


# ---- search / rag integration (C1) ---------------------------------------------

def _search(q, **filters):
    from app.db.session import session_scope
    from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

    with session_scope() as s:
        return hybrid_search(s, SearchRequest(q=q, top_k=100, min_top_score=0.0,
                                              filters=SearchFilters(**filters)))


def _confirmed_hit(resp, did):
    return next((h for h in resp.results if h.external_id == f"DEC-{did[:8]}"), None)


def test_search_finds_confirmed_mirror_with_decision_meta_and_factor_is_general(client, monkeypatch):
    doc = _doc(body=TITLE + "\n제공 종료일 안내")
    d = _propose(client, doc["document_id"])
    _verdict(client, d["id"], rationale="원문 제목")
    q = "PIXEL 제공 종료일 결론 확정"
    monkeypatch.setenv("SEARCH_CONFIRMED_FACTOR", "1.0")
    h = _confirmed_hit(_search(q), d["id"])
    assert h and h.evidence_grade == "H" and h.evidence_eligible is True
    assert h.decision["status"] == "confirmed" and h.decision["recheck"] is False
    assert h.decision["decided_by"] == "홍길동" and h.decision["self_reported"] is True
    base = h.score
    monkeypatch.setenv("SEARCH_CONFIRMED_FACTOR", "2.0")
    boosted = _confirmed_hit(_search(q), d["id"]).score
    assert boosted > base
    # 근거 문서가 바뀌어 needs_recheck가 되면 같은 요인이 있어도 올리지 않는다
    _mutate_doc(doc["document_id"], TITLE + " v98")
    client.post("/v1/decisions/_scan-staleness")
    h2 = _confirmed_hit(_search(q), d["id"])
    assert h2.decision["recheck"] is True and h2.score == pytest.approx(base, rel=1e-6)
    # "유지" 한 번이면 다시 올라간다
    assert _verdict(client, d["id"], keep=True).status_code == 200
    h3 = _confirmed_hit(_search(q), d["id"])
    assert h3.decision["recheck"] is False and h3.score > base


def test_superseded_excluded_by_default_and_visible_with_flag(client):
    doc = _doc()
    old = _propose(client, doc["document_id"])
    _verdict(client, old["id"])
    new = _propose(client, doc["document_id"])
    _verdict(client, new["id"])
    client.post(f"/v1/decisions/{old['id']}/supersede", json={"new_decision_id": new["id"]})
    q = "PIXEL 제공 종료일 확정"
    assert _confirmed_hit(_search(q, source_type="confirmed_decision"), old["id"]) is None
    inc = _search(q, source_type="confirmed_decision", include_superseded=True)
    hit = _confirmed_hit(inc, old["id"])
    assert hit and hit.decision["status"] == "superseded"
    assert _confirmed_hit(_search(q, source_type="confirmed_decision"), new["id"])


def test_source_type_filter_aliases(client):
    from app.retrieval.filter_args import normalize_source_type

    for alias in ("decision", "decisions", "confirmed", "confirmed_decision"):
        assert normalize_source_type(alias, field="section") == "confirmed_decision"
    with pytest.raises(ValueError):
        normalize_source_type("nonsense", field="section")


def test_search_code_has_no_hardcoded_answers():
    import pathlib

    src = pathlib.Path("app/retrieval/search.py").read_text() + pathlib.Path("app/decisions/mirror.py").read_text()
    for needle in ("PIXEL", "80022224", "PISA Station"):
        assert needle not in src


def test_failed_evidence_does_not_leave_orphan_proposal(client):
    before = client.get("/v1/decisions", params={"status": "proposed"}).json()["total"]
    r = client.post("/v1/decisions", json={"question": "고아 확인?", "options": OPTS, "evidence": [{
        "quote": "존재하지 않는 문장", "stance": "supports", "option_key": "a", "document_id": _doc()["document_id"]}]})
    assert r.status_code == 400
    assert client.get("/v1/decisions", params={"status": "proposed"}).json()["total"] == before
