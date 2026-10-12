from __future__ import annotations

from app.graph.explore import (
    MAX_RESULTS_PER_CATEGORY,
    dedup_by_key,
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


def test_shape_explore_result_breaks_ties_deterministically_by_id():
    # G09(2차 리뷰): hops/grade 동률일 때 입력 순서(Neo4j collect()의 비결정적
    # 순서)가 아니라 id로 결정적으로 정렬돼야 한다. 입력을 반대 순서로 둬도
    # 결과가 같아야 한다.
    raw = {
        "found": True,
        "documents": [
            {"id": "z1", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES",
             "hops": 1, "evidence_grade": "A"},
            {"id": "a1", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES",
             "hops": 1, "evidence_grade": "A"},
        ],
        "components": [], "failure_buckets": [], "excluded_hub_components": [],
    }
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert [d["id"] for d in shaped["documents"]] == ["a1", "z1"]


def test_shape_explore_result_sorts_a_minus_before_b_at_same_hop():
    # G09: 과거 로컬 _GRADE_ORDER가 "A-"를 몰라 C와 동일(순서 없음) 취급했다.
    raw = {
        "found": True,
        "documents": [
            {"id": "d-b", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES",
             "hops": 1, "evidence_grade": "B"},
            {"id": "d-aminus", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES",
             "hops": 1, "evidence_grade": "A-"},
        ],
        "components": [], "failure_buckets": [], "excluded_hub_components": [],
    }
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert [d["id"] for d in shaped["documents"]] == ["d-aminus", "d-b"]


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


def test_shape_explore_result_exposes_per_category_total_and_truncated():
    # G07: 정확히 50개(안 잘림)와 51개(잘림)를 전역 truncated만으로는 구분 못 했다.
    docs_50 = [
        {"id": f"d{i}", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES", "hops": 1}
        for i in range(MAX_RESULTS_PER_CATEGORY)
    ]
    raw = {"found": True, "documents": docs_50, "components": [], "failure_buckets": [],
           "excluded_hub_components": []}
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert shaped["documents_total"] == MAX_RESULTS_PER_CATEGORY
    assert shaped["documents_truncated"] is False

    docs_51 = docs_50 + [{"id": "d-extra", "title": "t", "source_type": "tech_repo",
                           "relation": "REFERENCES", "hops": 1}]
    raw["documents"] = docs_51
    shaped = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert shaped["documents_total"] == MAX_RESULTS_PER_CATEGORY + 1
    assert shaped["documents_truncated"] is True


def test_shape_explore_result_offset_pages_through_sorted_candidates():
    # G07: offset으로 51번째 항목(cap 뒤에 숨은 후보)에 접근할 수 있어야 한다 —
    # TGW 케이스처럼 50건+ 중 21~50번째에 실제 정답이 있는 상황.
    docs = [
        {"id": f"d{i:03d}", "title": "t", "source_type": "tech_repo", "relation": "REFERENCES", "hops": 1}
        for i in range(60)
    ]
    raw = {"found": True, "documents": docs, "components": [], "failure_buckets": [],
           "excluded_hub_components": []}
    page1 = shape_explore_result(raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert [d["id"] for d in page1["documents"]][:3] == ["d000", "d001", "d002"]
    assert page1["documents_truncated"] is True

    page2 = shape_explore_result(
        raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"},
        offsets={"documents": 50},
    )
    assert [d["id"] for d in page2["documents"]] == [f"d{i:03d}" for i in range(50, 60)]
    assert page2["documents_truncated"] is False
    assert page2["documents_total"] == 60


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


def test_filter_excluded_documents_removes_by_page_id():
    from app.graph.explore import filter_excluded_documents

    docs = [{"id": "d1", "external_id": "2586676618", "source_type": "confluence_map"},
            {"id": "d2", "external_id": "999", "source_type": "confluence_map"}]
    kept = filter_excluded_documents(docs, exclude_page_ids={"2586676618"})
    assert [d["id"] for d in kept] == ["d2"]


def test_filter_excluded_documents_removes_by_subtree_ancestor():
    from app.graph.explore import filter_excluded_documents

    docs = [{"id": "d1", "external_id": "111", "source_type": "confluence_map"}]
    kept = filter_excluded_documents(
        docs, exclude_subtree_ids={"2550166358"},
        ancestor_ids_by_doc_id={"d1": ["2550166358", "111"]},
    )
    assert kept == []


def test_filter_excluded_documents_removes_by_source_type():
    from app.graph.explore import filter_excluded_documents

    docs = [{"id": "d1", "external_id": "x", "source_type": "support_history"}]
    kept = filter_excluded_documents(docs, exclude_source_types={"support_history"})
    assert kept == []


def test_filter_excluded_documents_noop_when_no_filters():
    from app.graph.explore import filter_excluded_documents

    docs = [{"id": "d1", "external_id": "x", "source_type": "confluence_map"}]
    assert filter_excluded_documents(docs) == docs


def test_dedup_by_key_removes_duplicates_across_anchors():
    items = [
        {"id": "d1", "title": "from anchor A", "hops": 2},
        {"id": "d1", "title": "from anchor B", "hops": 2},
        {"id": "d2", "title": "unique", "hops": 1},
    ]
    deduped = dedup_by_key(items, "id")
    assert len(deduped) == 2
    assert {d["id"] for d in deduped} == {"d1", "d2"}


def test_dedup_by_key_keeps_smallest_hops_on_conflict():
    items = [
        {"id": "d1", "hops": 2},
        {"id": "d1", "hops": 1},  # closer via a different anchor — should win
        {"id": "d1", "hops": 3},
    ]
    deduped = dedup_by_key(items, "id")
    assert len(deduped) == 1
    assert deduped[0]["hops"] == 1
