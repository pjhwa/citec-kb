from __future__ import annotations

from app.graph.explore import (
    MAX_RESULTS_PER_CATEGORY,
    enrich_with_evidence_grade,
    resolve_component_anchor,
    shape_explore_result,
)


def test_resolve_component_anchor_uses_lexicon_variant():
    lexicon_map = {"netapp": ["NetApp"], "넷앱": ["NetApp"]}
    assert resolve_component_anchor("넷앱", lexicon_map) == "NetApp"
    assert resolve_component_anchor("NetApp", lexicon_map) == "NetApp"


def test_resolve_component_anchor_falls_back_to_input_when_not_in_lexicon():
    assert resolve_component_anchor("Unknown-Thing", {}) == "Unknown-Thing"


def test_shape_explore_result_sorts_by_hops_then_evidence_grade():
    raw = {
        "found": True,
        "documents": [
            {"id": "d1", "title": "t1", "source_type": "tech_repo", "relation": "HAS_EVIDENCE", "hops": 2},
            {"id": "d2", "title": "t2", "source_type": "tech_repo", "relation": "HAS_EVIDENCE", "hops": 1},
        ],
        "components": [], "failure_buckets": [], "excluded_hub_components": [],
    }
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert [d["id"] for d in shaped["documents"]] == ["d2", "d1"]
    assert shaped["as_of"] == "2026-10-09"


def test_shape_explore_result_truncates_at_cap():
    docs = [
        {"id": f"d{i}", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES", "hops": 1}
        for i in range(MAX_RESULTS_PER_CATEGORY + 1)
    ]
    raw = {"found": True, "documents": docs, "components": [], "failure_buckets": [],
           "excluded_hub_components": []}
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert len(shaped["documents"]) == MAX_RESULTS_PER_CATEGORY
    assert shaped["truncated"] is True


def test_shape_explore_result_not_found():
    raw = {"found": False, "documents": [], "components": [], "failure_buckets": [],
           "excluded_hub_components": []}
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "missing"})
    assert shaped["found"] is False


def test_enrich_with_evidence_grade_merges_by_id():
    documents = [{"id": "d1", "title": "t"}, {"id": "d2", "title": "t2"}]
    grade_by_id = {"d1": "A"}
    enriched = enrich_with_evidence_grade(documents, grade_by_id)
    assert enriched[0]["evidence_grade"] == "A"
    assert enriched[1]["evidence_grade"] is None


def test_dedup_by_key_removes_duplicates_across_anchors():
    from app.routers.graph import _dedup_by_key

    items = [
        {"id": "d1", "title": "from anchor A", "hops": 2},
        {"id": "d1", "title": "from anchor B", "hops": 2},
        {"id": "d2", "title": "unique", "hops": 1},
    ]
    deduped = _dedup_by_key(items, "id")
    assert len(deduped) == 2
    assert {d["id"] for d in deduped} == {"d1", "d2"}


def test_dedup_by_key_keeps_smallest_hops_on_conflict():
    from app.routers.graph import _dedup_by_key

    items = [
        {"id": "d1", "hops": 2},
        {"id": "d1", "hops": 1},  # closer via a different anchor — should win
        {"id": "d1", "hops": 3},
    ]
    deduped = _dedup_by_key(items, "id")
    assert len(deduped) == 1
    assert deduped[0]["hops"] == 1
