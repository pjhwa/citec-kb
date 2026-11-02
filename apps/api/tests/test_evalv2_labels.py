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
    assert out["report"] == {"kept": 1, "no_answer": 1, "unreviewed": 1, "unlabeled_candidates": 0,
                             "kept_by_slice": {"real": 1}}


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
    shown = [c["external_id"] for c in labels.candidates_for_review(q, max_n=2)]
    # seed + pool top-3 are guaranteed; the cap (2) applies to the remainder (P4, P5)
    assert set(shown) == {"SEED", "P1", "P2", "P3", "P4", "P5"}
    again = [c["external_id"] for c in labels.candidates_for_review({"id": "x", "candidates": list(reversed(cands))}, max_n=2)]
    assert set(again) == set(shown)                                           # input order does not change the pick


def test_top3_and_seed_always_shown_even_with_many_llm_positives():
    cands = [_cand(f"P{i}", 2, arms={"mq_on": i}) for i in range(1, 11)]   # 10 LLM positives, ranks 1..10
    cands.append(_cand("SEED", 0, seed=True, arms={}))
    cands.append(_cand("LOWTOP", 0, arms={"mq_on": 11}))
    shown = [c["external_id"] for c in labels.candidates_for_review({"id": "x", "candidates": cands}, max_n=8)]
    assert {"SEED", "P1", "P2", "P3"} <= set(shown)
    assert len(shown) <= 8 + 4
    # remainder (non-guaranteed) is capped at max_n
    assert len([e for e in shown if e not in {"SEED", "P1", "P2", "P3"}]) == 7  # P4..P10; LOWTOP is not a positive


def test_validate_review_strict_date_shape_and_duplicate_query_ids():
    judged = _judged([{"id": "q1", "q": "a", "slice": "real", "source": "real", "candidates": [_cand("A", 2)]}])
    base = {"reviewer": "r", "labels": {"q1": {"A": "O"}}}
    assert any("reviewed_on" in e for e in labels.validate_review(judged, {**base, "reviewed_on": "2026-13-45"}))
    assert any("labels" in e for e in labels.validate_review(judged, {**base, "reviewed_on": "2026-11-05", "labels": []}))
    assert any("q1" in e for e in labels.validate_review(judged, {**base, "reviewed_on": "2026-11-05", "labels": {"q1": ["A"]}}))
    dup = _judged([judged["queries"][0], judged["queries"][0]])
    assert any("duplicate" in e for e in labels.validate_review(dup, {**base, "reviewed_on": "2026-11-05"}))


def test_split_groups_queries_sharing_a_seed_doc_and_copies_fields():
    seed = {"external_id": "SEED-9", "title": "t", "source_type": "support_history"}
    qs = [{"id": f"q{i}", "q": f"서로 다른 질의 {i} 입니다", "slice": "symptom", "source": "synthetic", "count": 2,
           "seed_doc": seed, "candidates": [_cand("A", 2)]} for i in range(12)]
    review = {"reviewer": "r", "reviewed_on": "2026-11-05", "labels": {f"q{i}": {"A": "O"} for i in range(12)}}
    out = labels.build_evalsets(_judged(qs), review, evalset_id="t")
    assert len(out["dev"]["cases"]) in (0, 12)
    cases = out["dev"]["cases"] + out["frozen"]["cases"]
    assert cases[0]["seed_doc"] == seed and cases[0]["count"] == 2


def test_report_unlabeled_candidates_kept_by_slice_and_frozen_at():
    qs = [
        {"id": "q1", "q": "질의 하나 입니다", "slice": "real", "source": "real",
         "candidates": [_cand("A", 2), _cand("B", 0), _cand("C", 0)]},
        {"id": "q2", "q": "질의 둘 입니다", "slice": "vocab", "source": "synthetic", "candidates": [_cand("D", 2)]},
        {"id": "q3", "q": "질의 셋 입니다", "slice": "pointer", "source": "synthetic",
         "candidates": [_cand("E", 2), _cand("F", 0)]},
    ]
    review = {"reviewer": "r", "reviewed_on": "2026-11-05", "labels": {
        "q1": {"A": "O", "B": "X"},     # C unmarked
        "q2": {"D": "△"},               # triangle only -> dropped, not counted as kept
    }}
    out = labels.build_evalsets(_judged(qs), review, evalset_id="t", frozen_at="2026-11-06T00:00:00+00:00")
    assert out["report"]["kept"] == 1 and out["report"]["no_answer"] == 1 and out["report"]["unreviewed"] == 1
    assert out["report"]["unlabeled_candidates"] == 1
    assert out["report"]["kept_by_slice"] == {"real": 1}
    assert out["frozen"]["frozen_at_utc"] == "2026-11-06T00:00:00+00:00"
    assert any("비관련" in c for c in out["dev"]["caveats"]) and any("△" in c for c in out["dev"]["caveats"])
