from types import SimpleNamespace

from app.graph.identity import GRADE_RANK
from app.rag.packer import format_context_block, pack_chunks

DEC = {"status": "confirmed", "recheck": False, "decided_by": "홍길동", "decided_at": "2026-11-05T01:00:00+00:00",
       "self_reported": True}


def _hit(i, decision=None):
    return SimpleNamespace(document_id=f"d{i}", chunk_id=f"c{i}", title=f"t{i}", external_id=f"E{i}",
                           source_type="confirmed_decision" if decision else "tech_repo",
                           snippet="본문", source_uri=None, score=1.0, evidence_eligible=True,
                           map_synced_at=None, decision=decision)


def test_grade_h_outranks_everything():
    assert GRADE_RANK["H"] < min(v for k, v in GRADE_RANK.items() if k != "H")


def test_context_block_marks_confirmed_with_who_and_when():
    txt = format_context_block(pack_chunks([_hit(1, DEC)]))
    assert "사람 확정" in txt and "홍길동(자기 신고)" in txt and "2026-11-05" in txt and "재확인" not in txt


def test_context_block_marks_recheck():
    txt = format_context_block(pack_chunks([_hit(1, {**DEC, "recheck": True})]))
    assert "재확인 필요" in txt and "사실로 단정하지 말고" in txt


def test_ask_fold_prints_decision_line_and_recheck():
    from sys import path
    import pathlib

    path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "mcp-server"))
    from ask_fold import fold_ask_events

    cit = {"id": "C1", "external_id": "DEC-ab12cd34", "source_type": "confirmed_decision", "title": "q",
           "decision": {**DEC, "recheck": True}}
    out = fold_ask_events([{"type": "done", "result": {"answer": "기준은 X [C1]", "generation_status": "ok",
                                                         "citations": [cit]}}])
    assert "사람 확정: 확정자 홍길동 (자기 신고), 확정일 2026-11-05" in out and "재확인 필요" in out
