"""LOOKIN 기술지원 매칭 저장·재실행 안전성, 동기화 import 규칙 - DB 테스트(scratch DB only)."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import text

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

PAGE = "990001001"
INC_A, INC_B = "26099990201", "26099990202"
PATH = "[CI-TEC] > Ⅲ. CI-TEC 기술지원 ('20년~'26년) > ★ 26년 기술지원 > [09/14] 테스트 장애 지원"
EXTERNAL_IDS = (PAGE, INC_A, INC_B)


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
        s.add(Document(id=f"confluence_map:{PAGE}", source_type="confluence_map", external_id=PAGE,
                       title="[09/14] 테스트 장애 지원", body_md="본문", content_hash=uuid.uuid4().hex, status="active",
                       source_uri="u", metadata_={"space_key": "LOOKIN", "경로": PATH}))
        for ext, when in ((INC_A, "2026-09-14 09:00"), (INC_B, "2026-09-14 15:00")):
            s.add(Document(id=f"incident_reports:{ext}", source_type="incident_reports", external_id=ext,
                           title=f"장애 {ext}", body_md="장애", metadata_={"발생일시(한국)": when},
                           content_hash=uuid.uuid4().hex, status="active", source_uri="u"))
    yield
    wipe()


def _rows():
    from app.db.session import session_scope

    with session_scope() as s:
        return {r.ie: (r.method, r.status) for r in s.execute(text(
            "SELECT i.external_id ie, m.method, m.status FROM lookin_swim_matches m "
            "JOIN documents i ON i.id=m.incident_document_id WHERE m.post_document_id=:p"),
            {"p": f"confluence_map:{PAGE}"})}


def test_apply_stores_review_rows_and_keeps_human_decisions():
    from app.matching.lookin_swim import run

    out = run(apply=True, force=True, limit=None)
    assert out["distribution"]["auto"] == 0
    assert _rows() == {INC_A: ("content", "review"), INC_B: ("content", "review")}

    from app.db.session import session_scope

    with session_scope() as s:
        s.execute(text("UPDATE lookin_swim_matches SET method='manual', status='confirmed' "
                       "WHERE incident_document_id=:i"), {"i": f"incident_reports:{INC_A}"})
    run(apply=True, force=True, limit=None)
    assert _rows()[INC_A] == ("manual", "confirmed")


def test_sync_export_and_import_rules(tmp_path):
    from app.matching.lookin_swim import run
    from app.matching.sync_matches import export, import_matches

    run(apply=True, force=True, limit=None)
    f = tmp_path / "e.jsonl"
    counts = export(str(f))
    assert counts["matches"] >= 2
    # 같은 환경에 다시 넣으면 새로 넣을 것도 바꿀 것도 없다
    out = import_matches(str(f), apply=True, only_human=False)
    assert out["inserted"] == 0 and out["updated"] == 0

    # 들어오는 행이 사람 결정이면 대상의 content 행을 바꾼다. 반대로 대상의 사람 결정은 지켜진다.
    lines = [json.loads(x) for x in f.read_text().splitlines()]
    for r in lines:
        if r.get("kind") == "match" and r["incident"] == INC_A and r["table"] == "lookin_swim_matches":
            r.update(method="manual", status="confirmed")
        if r.get("kind") == "match" and r["incident"] == INC_B and r["table"] == "lookin_swim_matches":
            r.update(status="rejected", method="manual")
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in lines))
    import_matches(str(f), apply=True, only_human=True)
    assert _rows() == {INC_A: ("manual", "confirmed"), INC_B: ("manual", "rejected")}
    for r in lines:
        if r.get("kind") == "match" and r["incident"] == INC_A:
            r.update(method="manual", status="rejected")
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in lines))
    out = import_matches(str(f), apply=True, only_human=False)
    assert out["kept_local_human"] >= 2 and _rows()[INC_A] == ("manual", "confirmed")


def test_import_skips_pairs_whose_documents_are_missing(tmp_path):
    from app.matching.sync_matches import import_matches

    row = {"kind": "match", "table": "lookin_swim_matches", "post_source_type": "confluence_map",
           "post": PAGE, "incident": "29999999999", "method": "content", "status": "review", "score": 0.4,
           "rank": 1, "signals": {}, "matcher_version": "1"}
    f = tmp_path / "m.jsonl"
    f.write_text(json.dumps(row) + "\n")
    out = import_matches(str(f), apply=True, only_human=False)
    assert out["skipped_missing_incident"] == 1 and out["inserted"] == 0
