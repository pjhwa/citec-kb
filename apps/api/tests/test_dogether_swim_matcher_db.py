"""DOgether 내용 매칭 - DB 통합 테스트(scratch DB only).

저장·판정·재실행 안전성(confirmed/rejected 보존), 수집기 재업로드 후 유지, 그래프 연결·재동기화 해시,
역조회를 확인한다. 임베딩은 만들지 않으므로 similarity는 0이고 날짜·분야 신호만 쓴다.
"""

from __future__ import annotations

import csv
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

POST = "dogether-990101"
POST_ID = f"dogether_preventive:{POST}"
INC_A, INC_B, INC_C = "26099990101", "26099990102", "26099990103"
OTHER = "990-other-doc"
EXTERNAL_IDS = (POST, INC_A, INC_B, INC_C, OTHER)


class FakeGraph:
    def __init__(self):
        self.calls = []

    def merge_document(self, doc, edges, **kw):
        self.calls.append((doc["id"], list(edges)))


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
            s.query(Document).filter(Document.external_id.in_(EXTERNAL_IDS)).delete(synchronize_session=False)

    wipe()
    with session_scope() as s:
        for ext, when, dom in ((INC_A, "2026-09-14 09:00", "middleware"), (INC_B, "2026-09-15 10:00", None),
                               (INC_C, "2026-09-25 10:00", None)):
            s.add(Document(id=f"incident_reports:{ext}", source_type="incident_reports", external_id=ext, title=f"장애 {ext}",
                           body_md="장애 본문", metadata_={"발생일시(한국)": when}, content_hash=uuid.uuid4().hex,
                           status="active", source_uri="u", domain=dom))
    yield
    wipe()


def _write_post(tmp_path, body_date="2026-09-14", extra=""):
    p = tmp_path / "dogether_990101.md"
    p.write_text(
        "---\n문서ID : dogether-990101\n게시ID : 990101\n게시유형 : 장애사례\n제목 : [장애사례] 테스트 글\n"
        "분야 : Application\n분야키 : project.tech.preventive.application\n게시일시 : 2026-11-05 08:35\n"
        f"원문URL : https://dogether.example/990101\n{extra}---\n\n# 테스트\n\n- 시간 : {body_date} 08:00 ~ 08:40\n",
        encoding="utf-8",
    )
    return p


def _upload(tmp_path, **kw):
    from app.ingest.adapters import parse_dogether_preventive_file
    from app.ingest.pipeline import upsert_document_from_draft

    return upsert_document_from_draft(parse_dogether_preventive_file(_write_post(tmp_path, **kw)))


def _cfg(**over):
    from app.matching.dogether_swim import DEFAULTS

    cfg = {**DEFAULTS, "weights": dict(DEFAULTS["weights"])}
    cfg.update(over)
    return cfg


def _run(cfg, *, force=True):
    from app.db.session import session_scope
    from app.matching import dogether_swim as m

    out = []
    with session_scope() as s:
        for post in m.select_posts(s, force=force, limit=None):
            groups = m.match_post(s, post, cfg, {})
            out.append(m.apply_post(s, post, groups, cfg))
    return out


def _rows():
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        return {
            r.ext: (r.method, r.status, r.rank)
            for r in s.execute(text(
                "SELECT i.external_id ext, m.method, m.status, m.rank FROM dogether_swim_matches m "
                "JOIN documents i ON i.id=m.incident_document_id WHERE m.post_document_id=:p"), {"p": POST_ID})
        }


def test_candidates_by_date_window_and_review_only_when_auto_disabled(tmp_path):
    assert _upload(tmp_path)["action"] == "inserted"
    _run(_cfg())  # auto_enabled False, T1/M None
    rows = _rows()
    assert set(rows) == {INC_A, INC_B}  # ±1일 안(14·15일). 25일 장애는 후보가 아니다
    assert {st for _m, st, _r in rows.values()} == {"review"}
    assert rows[INC_A][2] == 1  # 같은 날 + 분야 일치가 1위


def test_auto_only_with_enabled_thresholds_and_margin(tmp_path):
    _upload(tmp_path)
    _run(_cfg(auto_enabled=True, T1=0.2, M=0.05))
    assert _rows() == {INC_A: ("content", "auto", 1)}  # auto면 1위만 저장


def test_rerun_is_idempotent_and_removes_only_stale_content_rows(tmp_path):
    _upload(tmp_path)
    _run(_cfg())
    _run(_cfg())
    assert set(_rows()) == {INC_A, INC_B}
    _upload(tmp_path, body_date="2026-09-25")  # 글의 날짜가 바뀌어 14·15일 후보가 사라진다
    _run(_cfg())
    assert set(_rows()) == {INC_C}


def test_confirmed_and_rejected_are_never_touched(tmp_path):
    from sqlalchemy import text

    from app.db.session import session_scope

    _upload(tmp_path)
    _run(_cfg())
    with session_scope() as s:
        s.execute(text("UPDATE dogether_swim_matches SET status='confirmed', method='manual' "
                       "WHERE post_document_id=:p AND incident_document_id=:i"), {"p": POST_ID, "i": f"incident_reports:{INC_A}"})
        s.execute(text("UPDATE dogether_swim_matches SET status='rejected' "
                       "WHERE post_document_id=:p AND incident_document_id=:i"), {"p": POST_ID, "i": f"incident_reports:{INC_B}"})
    _run(_cfg(auto_enabled=True, T1=0.0, M=0.0))
    rows = _rows()
    assert rows[INC_A][:2] == ("manual", "confirmed")
    assert rows[INC_B][1] == "rejected"  # 다시 제안되지 않고 지워지지도 않는다
    _upload(tmp_path, body_date="2026-09-25")
    _run(_cfg())
    rows = _rows()
    assert rows[INC_A][:2] == ("manual", "confirmed") and rows[INC_B][1] == "rejected"


def test_incremental_selection_uses_run_state_even_with_no_candidates(tmp_path):
    from app.db.session import session_scope
    from app.matching import dogether_swim as m

    _upload(tmp_path, body_date="2026-01-02")  # 그날 장애 없음 → 후보 0건
    _run(_cfg())
    assert _rows() == {}
    with session_scope() as s:
        assert [p.external_id for p in m.select_posts(s, force=False, limit=None)] == []  # 후보 0건이어도 처리 기록이 남는다
    _upload(tmp_path, body_date="2026-09-14")  # 내용이 바뀌면 다시 대상이 된다
    with session_scope() as s:
        assert [p.external_id for p in m.select_posts(s, force=False, limit=None)] == [POST]


def test_matches_survive_collector_reupload(tmp_path):
    """수집기가 같은 글을 다시 올려도(metadata 재생성, 내용 변경) 매칭 행이 유지된다."""
    _upload(tmp_path)
    _run(_cfg(auto_enabled=True, T1=0.2, M=0.05))
    before = _rows()
    assert _upload(tmp_path, extra="태그 : 새태그\n")["action"] == "updated"
    assert _rows() == before


def test_unnumbered_only_and_work_type_filter(tmp_path):
    from app.db.session import session_scope
    from app.matching import dogether_swim as m

    _upload(tmp_path, extra="SWIM번호 : 26099990101\n")  # 번호가 있는 글은 대상이 아니다
    with session_scope() as s:
        assert m.load_posts(s, unnumbered_only=True) == []
        assert [p.external_id for p in m.load_posts(s, unnumbered_only=False)] == [POST]


def test_import_review_confirms_and_rejects(tmp_path):
    from app.matching import dogether_swim as m

    _upload(tmp_path)
    _run(_cfg())
    csv_path = tmp_path / "review.csv"
    m_export = m.export_review(str(csv_path))
    assert m_export >= 2
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["post_external_id"] == POST:
            r["decision"] = "confirmed" if r["incident_external_id"] == INC_A else "rejected"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=m.REVIEW_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    assert m.import_review(str(csv_path))["confirmed"] == 1
    got = _rows()
    assert got[INC_A][:2] == ("manual", "confirmed") and got[INC_B][1] == "rejected"


def test_graph_edge_tags_and_resync_only_when_matches_change(tmp_path):
    import app.graph.pipeline as pipeline
    from app.db.models import Document
    from app.db.session import session_scope
    from app.graph import sync_state

    _upload(tmp_path)
    _run(_cfg(auto_enabled=True, T1=0.2, M=0.05))
    idx = pipeline.build_page_identity_index()
    client = FakeGraph()

    def sync():
        return pipeline.sync_document(POST_ID, client=client, page_identity_index=idx)

    assert sync() == "synced"
    edges = [e for _id, es in client.calls for e in es if e.rel_type == "REFERENCES"]
    assert [(e.target_value, e.tag) for e in edges] == [(f"incident_reports:{INC_A}", "INFERRED")]
    assert sync() == "skipped"  # 매칭 불변 → 재동기화 없음
    from sqlalchemy import text

    with session_scope() as s:
        s.execute(text("UPDATE dogether_swim_matches SET status='confirmed', method='manual' WHERE post_document_id=:p"), {"p": POST_ID})
    assert sync() == "synced"  # 글 내용은 같아도 매칭이 바뀌면 이 글만 다시
    last = client.calls[-1][1]
    assert [(e.target_value, e.tag) for e in last if e.rel_type == "REFERENCES"] == [(f"incident_reports:{INC_A}", "EXTRACTED")]
    with session_scope() as s:
        s.execute(text("UPDATE dogether_swim_matches SET status='rejected' WHERE post_document_id=:p"), {"p": POST_ID})
    assert sync() == "synced"
    assert not [e for e in client.calls[-1][1] if e.rel_type == "REFERENCES"]  # 매칭이 사라지면 연결도 사라진다

    # 다른 문서의 input_hash는 이 기능 때문에 바뀌지 않는다(전체 재동기화 방지).
    from app.graph.hashing import compute_graph_hash

    with session_scope() as s:
        s.add(Document(id=OTHER, source_type="tech_repo", external_id=OTHER, title="t", body_md="b",
                       metadata_={}, content_hash="h-other", status="active", source_uri="u"))
    assert pipeline.sync_document(OTHER, client=client, page_identity_index=idx) == "synced"
    expected = compute_graph_hash("h-other", {"ancestor_ids": None, "components": None, "area": None, "document_entities": []})
    assert sync_state.get_state(OTHER)["input_hash"] == expected


def test_reverse_lookup_labels_scores_and_exclusions(tmp_path):
    from sqlalchemy import text

    from app.db.session import session_scope
    from app.tickets.query import related_prevention_posts

    _upload(tmp_path)
    _run(_cfg(auto_enabled=True, T1=0.2, M=0.05))
    got = related_prevention_posts(INC_A)
    assert [(p["external_id"], p["match"]) for p in got] == [(POST, "content")]
    assert isinstance(got[0]["score"], float)
    assert related_prevention_posts(INC_B) == []  # auto가 아닌 후보(없음)는 나오지 않는다
    with session_scope() as s:
        s.execute(text("UPDATE dogether_swim_matches SET status='confirmed', method='manual' WHERE post_document_id=:p"), {"p": POST_ID})
    assert related_prevention_posts(INC_A)[0]["match"] == "manual"
    with session_scope() as s:
        s.execute(text("UPDATE dogether_swim_matches SET status='review' WHERE post_document_id=:p"), {"p": POST_ID})
    assert related_prevention_posts(INC_A) == []  # review는 나오지 않는다


def test_number_link_wins_over_content_in_reverse_lookup(tmp_path):
    from app.tickets.query import related_prevention_posts

    _upload(tmp_path, extra=f"SWIM번호 : {INC_A}\n")
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text(
            "INSERT INTO dogether_swim_matches (post_document_id, incident_document_id, method, score, rank, status, matcher_version) "
            "VALUES (:p, :i, 'content', 0.9, 1, 'auto', '1')"), {"p": POST_ID, "i": f"incident_reports:{INC_A}"})
    got = related_prevention_posts(INC_A)
    assert [(p["external_id"], p["match"]) for p in got] == [(POST, "swim_ids")]
