import json

import pytest

from app.evalv2 import labels


def _cand(eid, grade, status="ok", arms=None, seed=False):
    arms = dict({"mq_on": 1} if arms is None else arms)
    if seed:
        arms["seed"] = 0
    return {"external_id": eid, "title": eid, "source_type": "support_history", "evidence_eligible": True,
            "arms": arms, "llm": {"grade": grade, "evidence": "", "status": status}}


def _judged(queries):
    return {"run_id": "r", "model": "m", "queries": queries}


def test_candidates_for_review_prioritises_llm_positives_unverified_and_top3_and_seed():
    cands = [_cand(f"D{i}", 0, arms={"mq_on": i}) for i in range(1, 10)]
    cands[6] = _cand("D7", 2, arms={"mq_on": 7})
    cands[7] = _cand("D8", None, status="unverified", arms={"mq_on": 8})
    cands.append(_cand("SEED", 0, seed=True, arms={}))
    q = {"id": "x", "candidates": cands}
    shown = [c["external_id"] for c in labels.candidates_for_review(q, max_n=8)]
    assert "D7" in shown and "D8" in shown and "SEED" in shown       # positives, needs-human, seed
    assert {"D1", "D2", "D3"} <= set(shown)                           # top-3 of the pool always shown
    assert len(shown) <= 8


def test_validate_review_catches_unknown_ids_and_bad_marks():
    judged = _judged([{"id": "q1", "q": "a", "slice": "real", "source": "real", "candidates": [_cand("A", 2)]}])
    ok = {"reviewer": "r", "reviewed_on": "2026-11-05", "labels": {"q1": {"A": "O"}}}
    assert labels.validate_review(judged, ok) == []
    bad = {"reviewer": "", "reviewed_on": "x", "labels": {"qX": {"A": "O"}, "q1": {"ZZZ": "O", "A": "?"}}}
    errs = labels.validate_review(judged, bad)
    assert any("reviewer" in e for e in errs) and any("qX" in e for e in errs)
    assert any("ZZZ" in e for e in errs) and any("'?'" in e for e in errs)


def test_build_evalsets_maps_marks_and_drops_unreviewed_or_no_answer():
    judged = _judged([
        {"id": "q1", "q": "질의 하나 입니다", "slice": "real", "source": "real", "candidates": [_cand("A", 2), _cand("B", 1), _cand("C", 0)]},
        {"id": "q2", "q": "질의 둘 입니다", "slice": "vocab", "source": "synthetic", "candidates": [_cand("D", 2)]},
        {"id": "q3", "q": "질의 셋 입니다", "slice": "pointer", "source": "synthetic", "candidates": [_cand("E", 2)]},
    ])
    review = {"reviewer": "r", "reviewed_on": "2026-11-05", "labels": {
        "q1": {"A": "O", "B": "△", "C": "X"},
        "q2": {"D": "X"},            # no relevant doc -> dropped as no_answer
    }}                                # q3 unreviewed -> dropped
    out = labels.build_evalsets(judged, review, evalset_id="citec-eval-v2-test")
    cases = out["dev"]["cases"] + out["frozen"]["cases"]
    assert [c["id"] for c in cases] == ["q1"]
    c = cases[0]
    assert c["expect"] == ["A"] and c["also_ok"] == ["B"] and c["group"] == "v2_real"
    assert c["kind"] == "search" and c["k"] == 10 and c["source"] == "real" and c["reviewed_on"] == "2026-11-05"
    assert out["report"] == {"kept": 1, "no_answer": 1, "unreviewed": 1}


def test_split_is_deterministic_and_roughly_balanced():
    qs = [{"id": f"q{i}", "q": f"질의 번호 {i} 입니다", "slice": "real", "source": "real", "candidates": [_cand("A", 2)]}
          for i in range(40)]
    review = {"reviewer": "r", "reviewed_on": "2026-11-05", "labels": {f"q{i}": {"A": "O"} for i in range(40)}}
    a = labels.build_evalsets(_judged(qs), review, evalset_id="t")
    b = labels.build_evalsets(_judged(qs), review, evalset_id="t")
    assert [c["id"] for c in a["dev"]["cases"]] == [c["id"] for c in b["dev"]["cases"]]
    assert 10 <= len(a["dev"]["cases"]) <= 30 and len(a["dev"]["cases"]) + len(a["frozen"]["cases"]) == 40
    assert a["frozen"]["frozen_at_utc"]
    assert all(c["split"] == "frozen" for c in a["frozen"]["cases"])


def test_candidates_for_review_cap_always_keeps_seed_and_is_deterministic():
    cands = [_cand(f"P{i}", 2, arms={"mq_on": i}) for i in range(1, 8)]   # 7 LLM positives
    cands.append(_cand("SEED", 0, seed=True, arms={}))                      # seed with no pool rank
    q = {"id": "x", "candidates": cands}
    shown = [c["external_id"] for c in labels.candidates_for_review(q, max_n=3)]
    assert len(shown) == 3 and "SEED" in shown
    assert set(shown) == {"SEED", "P1", "P2"}                                 # lowest-priority others dropped
    again = [c["external_id"] for c in labels.candidates_for_review({"id": "x", "candidates": list(reversed(cands))}, max_n=3)]
    assert set(again) == set(shown)                                           # input order does not change the pick
