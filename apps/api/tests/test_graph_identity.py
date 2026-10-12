from __future__ import annotations

from app.graph.identity import GRADE_RANK, best_candidate_id, resolve_representative


def test_grade_rank_includes_a_minus():
    assert GRADE_RANK["A-"] == 1
    assert GRADE_RANK["A"] < GRADE_RANK["A-"] < GRADE_RANK["B"] < GRADE_RANK["C"]


def test_resolve_representative_prefers_active_over_archived():
    candidates = [
        {"document_id": "d-archived", "status": "archived", "evidence_grade": "A", "source_type": "tech_repo"},
        {"document_id": "d-active", "status": "active", "evidence_grade": "C", "source_type": "confluence_map"},
    ]
    assert resolve_representative(candidates)["document_id"] == "d-active"


def test_resolve_representative_prefers_higher_grade_among_active():
    candidates = [
        {"document_id": "d-b", "status": "active", "evidence_grade": "B", "source_type": "confluence_map"},
        {"document_id": "d-a", "status": "active", "evidence_grade": "A", "source_type": "confluence_map"},
    ]
    assert resolve_representative(candidates)["document_id"] == "d-a"


def test_resolve_representative_prefers_tier1_source_on_grade_tie():
    candidates = [
        {"document_id": "d-map", "status": "active", "evidence_grade": "A", "source_type": "confluence_map", "space_key": "OTHER"},
        {"document_id": "d-techrepo", "status": "active", "evidence_grade": "A", "source_type": "tech_repo"},
    ]
    assert resolve_representative(candidates)["document_id"] == "d-techrepo"


def test_resolve_representative_breaks_final_tie_by_id():
    candidates = [
        {"document_id": "z", "status": "active", "evidence_grade": "A", "source_type": "tech_repo"},
        {"document_id": "a", "status": "active", "evidence_grade": "A", "source_type": "tech_repo"},
    ]
    assert resolve_representative(candidates)["document_id"] == "a"


def test_resolve_representative_empty_is_none():
    assert resolve_representative([]) is None


def test_best_candidate_id_wraps_resolve_representative():
    candidates = [{"document_id": "d1", "status": "active", "evidence_grade": "B", "source_type": "tech_repo"}]
    assert best_candidate_id(candidates) == "d1"
    assert best_candidate_id([]) is None
