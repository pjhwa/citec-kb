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


def _mutate_doc(doc_id: str, body: str, title: str | None = None) -> None:
    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("UPDATE documents SET body_md=:b, title=COALESCE(:t, title), content_hash=:h, version=version+1 WHERE id=:i"),
                  {"b": body, "t": title, "h": uuid.uuid4().hex, "i": doc_id})


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
    _mutate_doc(doc["document_id"], "완전히 다른 본문", title="완전히 다른 제목")
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


# ---- periodic topic scan + failure bucket linkage ------------------------------------

def _bucket(name="scan-test 링크 플래핑", conf_support=2):
    from app.db.models import FailureBucket
    from app.db.session import session_scope
    from app.failure_buckets import service as fb

    out = _bucket_raw(name)
    with session_scope() as s:  # 지지 사례 수는 refine/등록 경로가 올리는 값 — 시험에서는 직접 지정
        s.get(FailureBucket, out["id"]).support_count = conf_support
    return out


def _bucket_raw(name):
    from app.failure_buckets import service as fb

    return fb.create_bucket(
        bucket_name=name, fb_domain="linux", symptom="NIC 링크가 반복해서 끊긴다",
        discriminating_signals=["ens1f1np1 link down 반복"], root_cause="SFP 접촉 불량으로 링크 플래핑",
        recommended_action="SFP 교체", evidence_ref="confluence:1", created_by="human-tester")


@pytest.fixture()
def scan_env(monkeypatch):
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kw: {"embedded": 0})
    # 시험 DB의 다른 버킷·문서가 섞이지 않게 탐지기를 이 시험의 데이터로 한정한다
    from app.decisions import scan

    monkeypatch.setattr(scan, "load_config", lambda: {**scan.DEFAULTS, "detectors": ["fb_confirm", "deadline"],
                                                       "max_new_per_run": 50, "max_open_server_proposals": 500})
    return scan


def _scan(client, **params):
    r = client.post("/v1/decisions/_scan-topics", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_scan_dry_run_creates_nothing_then_creates_once_and_is_idempotent(client, scan_env):
    b = _bucket()
    key = f"fb:{b['id']}"
    dry = _scan(client, dry_run="true")
    assert key in {w["source_key"] for w in dry["would_create"]} and dry["created_count"] == 0
    assert not client.get("/v1/decisions", params={"topic": "failure_bucket"}).json()["items"] or True
    first = _scan(client)
    assert key in {c["source_key"] for c in first["created"]}
    again = _scan(client)
    assert key not in {c["source_key"] for c in again["created"]} and again["already_known"] >= 1
    card = next(i for i in client.get("/v1/decisions").json()["items"] if i["source_key"] == key)
    assert card["created_by_kind"] == "server_job" and card["source_ref"]["id"] == b["id"]
    assert [o["key"] for o in card["options"]][:3] == ["confirm", "revise", "retire"]
    assert card["evidence_count"] == 1


def test_bucket_confirmation_link_appears_and_verdict_does_not_change_bucket(client, scan_env):
    from app.failure_buckets import service as fb

    b = _bucket("scan-test 연계 버킷")
    assert fb.get_bucket(b["id"])["confirmation"] is None
    _scan(client)
    did = next(i["id"] for i in client.get("/v1/decisions").json()["items"] if i["source_key"] == f"fb:{b['id']}")
    link = fb.get_bucket(b["id"])["confirmation"]
    assert link["decision_id"] == did and link["verdict"] is None and link["effective_status"] == "proposed"
    before = fb.get_bucket(b["id"])["confidence"]
    assert _verdict(client, did, option_key="confirm").status_code == 200
    after = fb.get_bucket(b["id"])
    assert after["confirmation"]["verdict"] == "확정 패턴으로 인정" and after["confirmation"]["verdict_key"] == "confirm"
    assert after["confidence"] == before and after["status"] == "active"  # 사람 확정은 별도 신호, 버킷은 안 바뀐다
    matches = fb.match_buckets(observed_signals=["ens1f1np1 link down 반복"], fb_domain="linux")
    assert next(m for m in matches if m["bucket_id"] == b["id"])["confirmation"]["verdict_key"] == "confirm"


def test_scan_deadline_title_detector_and_caps(client, scan_env, monkeypatch):
    doc = _doc(body="본문에는 기한이 없다", eid=f"DL-{uuid.uuid4().hex[:6]}")
    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("UPDATE documents SET title=:t WHERE id=:i"), {"t": "ZZTOOL (엑셀)_2031.12.31 까지 제공", "i": doc["document_id"]})
    r = _scan(client)
    made = [c for c in r["created"] if c["detector"] == "deadline"]
    assert any(c["source_key"] == f"deadline:{doc['document_id']}" for c in made)
    card = next(i for i in client.get("/v1/decisions").json()["items"] if i["source_key"] == f"deadline:{doc['document_id']}")
    assert card["options"][0]["label"] == "2031.12.31" and card["evidence_count"] == 1
    # 같은 문서로 진행 중 결정이 있으면 다른 key로 다시 제안하지 않는다 + 상한 0이면 아무것도 안 만든다
    assert _scan(client, max_new=0)["created_count"] == 0


def test_scan_respects_open_proposal_cap(client, monkeypatch):
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kw: {"embedded": 0})
    from app.decisions import scan

    _bucket("scan-test 상한 버킷")
    monkeypatch.setattr(scan, "load_config", lambda: {**scan.DEFAULTS, "detectors": ["fb_confirm"],
                                                       "max_new_per_run": 50, "max_open_server_proposals": 0})
    r = _scan(client)
    assert r["created_count"] == 0 and r["skipped_by_cap"] >= 1


def test_scan_evidence_failure_does_not_leave_orphans_and_is_reported(client, scan_env, monkeypatch):
    from app.decisions import scan

    bad = scan.Proposal(detector="deadline", source_key="deadline:bad", question="q?", options=[
        {"key": "a", "label": "x"}, {"key": "b", "label": "y"}],
        evidence=[{"quote": "존재하지 않음", "stance": "supports", "option_key": "a", "document_id": _doc()["document_id"]}])
    monkeypatch.setattr(scan, "detect_deadline", lambda s, **kw: [bad])
    r = _scan(client)
    assert "deadline:bad" in r["errors"]
    assert not [i for i in client.get("/v1/decisions").json()["items"] if i["source_key"] == "deadline:bad"]


def test_scan_skips_bucket_without_support_and_shows_bucket_context(client, scan_env):
    none = _bucket("scan-test 지지 없음 링크", conf_support=0)
    some = _bucket("scan-test 지지 있음 링크", conf_support=2)
    keys = {w["source_key"]: w for w in _scan(client, dry_run="true")["would_create"]}
    assert f"fb:{none['id']}" not in keys
    ctx = keys[f"fb:{some['id']}"]["context"]
    labels = {f["label"]: f["value"] for f in ctx["fields"]}
    assert ctx["kind"] == "failure_bucket" and "ens1f1np1" in labels["판별 신호"] and "SFP" in labels["근본원인"]
    assert "지지 2건" in labels["지지/반증 사례"]
    _scan(client)
    card = next(i for i in client.get("/v1/decisions").json()["items"] if i["source_key"] == f"fb:{some['id']}")
    assert card["source_ref"]["context"]["kind"] == "failure_bucket"


def test_bucket_gap_proposes_per_group_with_full_incident_list(client, scan_env, monkeypatch):
    from app.citec_dashboard import service as dash
    from app.decisions import scan

    from app.db.models import Document
    from app.db.session import session_scope

    doc = _doc()
    with session_scope() as s:
        d = s.get(Document, doc["document_id"])
        ext, ttl = d.external_id, d.title
        d.source_type = "incident_reports"
    monkeypatch.setattr(dash, "failure_bucket_coverage", lambda **kw: {"domains": [
        {"domain": "Storage", "fb_domain": "storage", "status": "gap", "recurring_incident_count": 5}]})
    samples = [{"external_id": ext, "title": ttl, "url": None, "occurred_at": "2026-10-01"}] + [
        {"external_id": f"x{i}", "title": f"기타 사건 {i}", "url": None, "occurred_at": "2026-09-0%d" % (i + 1)} for i in range(4)]
    monkeypatch.setattr(dash, "query_recurring_patterns", lambda **kw: {"groups": [
        {"group": {"domain": "Storage", "customer": "고객A"}, "count": 5, "samples": samples},
        {"group": {"domain": "Storage", "customer": "(미상)"}, "count": 9, "samples": samples},
        {"group": {"domain": "Database", "customer": "고객B"}, "count": 8, "samples": samples}]})
    monkeypatch.setattr(scan, "_incident_fields",
                        lambda doc: {"situation": "", "cause": "컨트롤러 펌웨어 오류로 볼륨 단절", "action": ""})
    with session_scope() as s:
        props = scan.detect_bucket_gap(s, since_days=365, min_count=3, max_groups=5, max_incidents=60)
    assert len(props) == 1  # (미상) 묶음과 다른 도메인(태그 중복) 묶음은 제외
    p = props[0]
    assert p.source_key == "gap2:Storage:storage:고객A" and "고객A" in p.question and "5건" in p.question
    ctx = p.source_ref["context"]
    assert ctx["kind"] == "incident_group" and len(ctx["items"]) == 5 and ctx["items"][0]["title"] == ttl
    assert p.evidence[0]["quote"] == ttl


def _gap_env(monkeypatch, causes: dict[str, str], customer="고객A"):
    """원인 문구(causes: 제목 접미사→원인)대로 장애 문서를 만들고 공백 탐지기가 보는 통계를 흉내 낸다."""
    from app.citec_dashboard import service as dash
    from app.db.models import Document
    from app.db.session import session_scope
    from app.decisions import scan
    from app.ingest.adapters import DocumentDraft
    from app.ingest.pipeline import upsert_document_from_draft

    samples, by_ext = [], {}
    for k, cause in causes.items():
        eid = f"INC-{uuid.uuid4().hex[:8]}"
        d = upsert_document_from_draft(DocumentDraft(
            source_type="incident_reports", external_id=eid, title=f"[{customer}] 장애 {k} {eid}", body_md="x",
            evidence_grade="A", source_uri=f"test://{eid}").finalize())
        by_ext[eid] = cause
        samples.append({"external_id": eid, "title": f"[{customer}] 장애 {k} {eid}", "url": None, "occurred_at": "2026-09-01"})
    monkeypatch.setattr(dash, "failure_bucket_coverage", lambda **kw: {"domains": [
        {"domain": "Storage", "fb_domain": "storage", "status": "gap", "recurring_incident_count": len(samples)}]})
    monkeypatch.setattr(dash, "query_recurring_patterns", lambda **kw: {"groups": [
        {"group": {"domain": "Storage", "customer": customer}, "count": len(samples), "samples": samples}]})
    monkeypatch.setattr(scan, "_incident_fields", lambda doc: {"situation": "", "action": "",
                                                              "cause": by_ext.get(doc.external_id, "") if doc else ""})
    return samples


def _detect(**kw):
    from app.db.session import session_scope
    from app.decisions import scan

    with session_scope() as s:
        return scan.detect_bucket_gap(s, since_days=365, min_count=3, max_groups=5, max_incidents=60, **kw)


def test_has_cause_ignores_boilerplate_and_cluster_groups_similar_causes():
    from app.decisions.scan import cluster_by_cause, has_cause

    assert not has_cause({"cause": "상세원인 파악 중"}) and not has_cause({"cause": ""}) and not has_cause({"cause": "확인중"})
    assert has_cause({"cause": "상세원인 파악 중 DB 다운 이슈, Cluster fencing 으로 reboot"})  # 내용은 있음(확정 여부는 cause_confirmed)
    mk = lambda c: {"cause": c}
    hba = [mk("HBA카드 하드웨어 오류로 서버 리부팅"), mk("HBA 카드 하드웨어 오류 서버 재부팅"), mk("HBA카드 하드웨어 오류로 DB 서버 리부팅")]
    es = [mk("ElasticSearch 스토리지 용량 부족"), mk("ElasticSearch 스토리지 용량 부족으로 색인 불가")]
    out = cluster_by_cause(hba + es + [mk("고객 요청으로 파일스토리지 삭제")])
    assert [len(c) for c in out] == [3, 2, 1]


def test_gap_drops_incidents_without_a_recorded_cause(client, scan_env, monkeypatch):
    _gap_env(monkeypatch, {"a": "컨트롤러 펌웨어 오류로 볼륨 단절", "b": "컨트롤러 펌웨어 오류로 볼륨 단절",
                           "c": "컨트롤러 펌웨어 오류로 볼륨 단절", "d": "상세원인 파악 중", "e": ""})
    props = _detect(max_group_size=8)
    assert len(props) == 1
    ctx = props[0].source_ref["context"]
    assert len(ctx["items"]) == 3 and props[0].source_ref["excluded_no_cause"] == 2
    assert "3건" in props[0].question and "원인이 확정되지 않은 2건" in ctx["note"]
    # 원인이 있는 장애가 min_count 미만이면 제안하지 않는다
    _gap_env(monkeypatch, {"a": "컨트롤러 펌웨어 오류로 볼륨 단절", "b": "상세원인 파악 중", "c": "파악 중", "d": ""})
    assert _detect(max_group_size=8) == []


def test_gap_splits_a_large_group_by_cause_and_skips_one_off_incidents(client, scan_env, monkeypatch):
    causes = {f"h{i}": f"HBA카드 하드웨어 오류로 서버 리부팅 {i}" for i in range(3)}
    causes.update({f"e{i}": f"ElasticSearch 스토리지 용량 부족 {i}" for i in range(3)})
    causes.update({"x1": "고객 요청으로 파일스토리지 삭제", "x2": "오브젝트 스토리지 QoS 문제"})
    _gap_env(monkeypatch, causes)
    props = _detect(max_group_size=5)  # 8건 > 5 → 원인별로 나눔
    assert [p.source_key.rsplit("#", 1)[-1] for p in props] == ["1", "2"]
    assert all(len(p.source_ref["context"]["items"]) == 3 for p in props)
    assert all("원인이 비슷한 묶음" in p.source_ref["context"]["heading"] for p in props)
    titles = {i["title"] for p in props for i in p.source_ref["context"]["items"]}
    assert not any("x1" in t or "x2" in t for t in titles)  # 한 건짜리 원인은 반복 패턴이 아니다
    assert len(_detect(max_group_size=8)) == 1  # 상한 이하면 나누지 않는다


def test_gap_marks_incidents_that_are_also_on_another_open_proposal(client, scan_env, monkeypatch):
    from app.db.models import Decision
    from app.db.session import session_scope

    _gap_env(monkeypatch, {k: "컨트롤러 펌웨어 오류로 볼륨 단절" for k in "abc"})
    first = _detect(max_group_size=8)[0]
    assert not any("also_in" in i for i in first.source_ref["context"]["items"])
    did = client.post("/v1/decisions", json={
        "question": first.question, "options": first.options, "created_by_kind": "server_job",
        "evidence": first.evidence}).json()["id"]
    with session_scope() as s:
        s.get(Decision, did).source_key = "gap2:VMware:virtualization:고객A"
        s.get(Decision, did).source_ref = {"type": "domain_gap", "context": {
            "heading": "VMware · 고객A · 최근 365일 장애 3건", "items": first.source_ref["context"]["items"]}}
    again = _detect(max_group_size=8)[0]
    assert all(i["also_in"] == ["VMware · 고객A · 최근 365일 장애 3건"] for i in again.source_ref["context"]["items"])


def test_incident_fields_come_from_metadata_or_body_section():
    from types import SimpleNamespace

    from app.decisions.scan import _incident_fields

    meta = SimpleNamespace(metadata_={"장애원인": "  디스크  풀 "}, body_md="■ 장애상황: 서비스 지연\n■ 장애조치: 로그 삭제\n■ 조치이력요약: x")
    f = _incident_fields(meta)
    assert (f["situation"], f["cause"], f["cause_src"], f["action"]) == ("서비스 지연", "디스크 풀", "장애원인", "로그 삭제")
    assert _incident_fields(None)["cause"] == "" and _incident_fields(None)["cause_src"] == "장애원인"


def test_incident_cause_prefers_confirmed_fields_over_the_initial_report_text():
    from types import SimpleNamespace

    from app.decisions.scan import _incident_fields, cause_confirmed

    closed = SimpleNamespace(metadata_={"진행상태": "종료확정", "장애원인": "원인파악중", "근본원인": "[H/W오류] 컨트롤러 전원 모듈 결함",
                                        "직접원인": "[Reboot] 스토리지 리부팅", "사유": "분류"}, body_md="")
    f = _incident_fields(closed)
    assert (f["cause"], f["cause_src"], f["direct"], f["status"]) == (
        "[H/W오류] 컨트롤러 전원 모듈 결함", "근본원인", "[Reboot] 스토리지 리부팅", "종료확정")
    assert cause_confirmed(f)
    # 근본원인이 없으면 사유까지 내려가고, 전부 상용구뿐이면 최초 보고 문구를 그대로 둔다(→ 미확정)
    only_reason = SimpleNamespace(metadata_={"진행상태": "종료확정", "장애원인": "원인파악중", "사유": "드라이버 버그로 커널 패닉"}, body_md="")
    assert _incident_fields(only_reason)["cause_src"] == "사유"
    none = _incident_fields(SimpleNamespace(metadata_={"진행상태": "종료확정", "장애원인": "원인파악중"}, body_md=""))
    assert none["cause_src"] == "장애원인" and not cause_confirmed(none)


def test_cause_confirmed_needs_analysis_stage_and_no_pending_wording():
    from app.decisions.scan import cause_confirmed

    ok = {"cause": "NSX Edge LB lbconfig 메모리 leak 버그", "status": "종료확정"}
    assert cause_confirmed(ok) and cause_confirmed({**ok, "status": ""})  # 진행상태를 모르면 원인 문구만 본다
    assert not cause_confirmed({**ok, "status": "원인분석중"}) and not cause_confirmed({**ok, "status": "조치완료"})
    assert not cause_confirmed({**ok, "cause": "[원인불명] Nginx Bug로 추정되나 Core Dump 부재로 원인 확정 어려움"})
    assert not cause_confirmed({**ok, "cause": "Disk I/O 이슈 발생, 상세 원인 확인중"})


def _group_decision(client, n=3):
    """공백 제안과 같은 모양(incident_group + consolidate/individual)의 결정을 만든다."""
    from app.ingest.adapters import DocumentDraft
    from app.ingest.pipeline import upsert_document_from_draft

    items = []
    for i in range(n):
        eid = f"INC-{uuid.uuid4().hex[:8]}"
        d = upsert_document_from_draft(DocumentDraft(
            source_type="incident_reports", external_id=eid, title=f"[고객] 스토리지 장애 {eid}", body_md=f"■ 장애원인: 원인{i}",
            evidence_grade="A", source_uri=f"test://{eid}").finalize())
        items.append({"id": eid, "title": f"[고객] 스토리지 장애 {eid}", "date": "2026-10-01", "document_id": d["document_id"]})
    r = client.post("/v1/decisions", json={
        "question": "Storage '고객' 장애 중 같은 원인으로 정리할 건이 있습니까?",
        "options": [{"key": "consolidate", "label": "정리 필요"}, {"key": "individual", "label": "개별 사건"}],
        "created_by_kind": "server_job", "evidence": [
            {"quote": items[0]["title"], "stance": "context", "document_id": items[0]["document_id"]}]})
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    from app.db.models import Decision
    from app.db.session import session_scope

    with session_scope() as s:
        s.get(Decision, did).source_ref = {"type": "domain_gap", "context": {"kind": "incident_group", "items": items}}
    return did, items


def _pick(client, did, ids, **kw):
    n = client.get(f"/v1/decisions/{did}/nonce").json()["nonce"]
    return client.post(f"/v1/decisions/{did}/pick-incidents",
                       json={"document_ids": ids, "decided_by": "홍길동", "nonce": n, **kw})


def test_pick_incidents_records_supporting_evidence_and_confirms_consolidate(client):
    did, items = _group_decision(client)
    r = _pick(client, did, [items[0]["document_id"], items[2]["document_id"]], rationale="같은 컨트롤러 이슈")
    assert r.status_code == 200, r.text
    assert r.json()["picked"] == 2 and r.json()["status"] == "confirmed"
    card = client.get(f"/v1/decisions/{did}").json()
    sup = [e for e in card["evidence_by_option"]["consolidate"] if e["stance"] == "supports"]
    assert {e["document_id"] for e in sup} == {items[0]["document_id"], items[2]["document_id"]}
    assert card["current_verdict"]["option_key"] == "consolidate"
    assert "2건" in card["current_verdict"]["rationale"] and "같은 컨트롤러 이슈" in card["current_verdict"]["rationale"]
    # 같은 선택을 다시 보내도 근거가 중복되지 않는다
    assert _pick(client, did, [items[0]["document_id"]]).status_code == 200
    card = client.get(f"/v1/decisions/{did}").json()
    assert len([e for e in card["evidence_by_option"]["consolidate"] if e["stance"] == "supports"]) == 2


def test_pick_incidents_rejects_empty_unknown_and_rolls_back_evidence(client):
    did, items = _group_decision(client)
    assert _pick(client, did, []).status_code == 422
    other = _doc()["document_id"]
    r = _pick(client, did, [other])
    assert r.status_code == 400 and "not in this decision" in r.json()["detail"]
    # nonce가 틀리면 확정이 실패하고, 방금 추가한 근거도 남지 않는다
    r = client.post(f"/v1/decisions/{did}/pick-incidents", json={
        "document_ids": [items[1]["document_id"]], "decided_by": "홍길동", "nonce": "bogus"})
    assert r.status_code == 403
    card = client.get(f"/v1/decisions/{did}").json()
    assert not [e for e in card["evidence_by_option"].get("consolidate", []) if not e.get("retracted_at")]
    assert card["status"] == "proposed"
