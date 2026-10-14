from __future__ import annotations

from app.graph.explore import (
    MAX_RESULTS_PER_CATEGORY,
    apply_exposure_policy,
    collapse_duplicate_copies,
    dedup_by_key,
    enrich_with_document_metadata,
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


def test_shape_explore_result_status_found_vs_isolated():
    found_raw = {"found": True, "documents": [{"id": "d1", "hops": 1, "relation": "REFERENCES"}],
                 "components": [], "failure_buckets": [], "excluded_hub_components": []}
    shaped = shape_explore_result(found_raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert shaped["status"] == "found"

    isolated_raw = {"found": True, "documents": [], "components": [], "failure_buckets": [],
                     "excluded_hub_components": []}
    shaped = shape_explore_result(isolated_raw, as_of="2026-10-09", anchor={"type": "document", "resolved_id": "d0"})
    assert shaped["status"] == "isolated"


def test_shape_explore_result_status_not_found():
    shaped = shape_explore_result(
        {"found": False, "excluded_hub_components": []},
        as_of="2026-10-09", anchor={"type": "document", "resolved_id": "missing"},
    )
    assert shaped["status"] == "not_found"


def test_shape_explore_result_status_override():
    shaped = shape_explore_result(
        {"found": True, "documents": [], "components": [], "failure_buckets": [], "excluded_hub_components": []},
        as_of="2026-10-09", anchor={"type": "symptom_text"}, status="no_match",
    )
    assert shaped["status"] == "no_match"


def test_dedup_by_key_tie_break_is_deterministic_regardless_of_input_order():
    # G08(2차 리뷰) 잔여 — hops가 같을 때 입력 순서가 바뀌어도 같은 승자를 골라야 한다.
    a = {"id": "d1", "hops": 2, "relation": "HAS_COMPONENT", "tag": "INFERRED"}
    b = {"id": "d1", "hops": 2, "relation": "PARENT_OF", "tag": "EXTRACTED"}
    winner_forward = dedup_by_key([a, b], "id")[0]
    winner_backward = dedup_by_key([b, a], "id")[0]
    # (hops, relation, tag) 키 비교 — "HAS_COMPONENT" < "PARENT_OF" 알파벳상
    # a가 더 작은 키라 항상 a가 이긴다. 중요한 건 "어느 쪽이든" 입력 순서와
    # 무관하게 같은 결과가 나온다는 것.
    assert winner_forward == winner_backward == a


def test_enrich_with_document_metadata_adds_grade_and_archived_flag():
    docs = [{"id": "d1", "title": "t"}, {"id": "d2", "title": "t2"}]
    enrichment = {"d1": {"evidence_grade": "A", "status": "archived"},
                  "d2": {"evidence_grade": "C", "status": "active"}}
    enriched = enrich_with_document_metadata(docs, enrichment)
    assert enriched[0]["evidence_grade"] == "A"
    assert enriched[0]["archived"] is True
    assert enriched[1]["archived"] is False


def test_enrich_with_document_metadata_missing_doc_defaults():
    docs = [{"id": "d1", "title": "t"}]
    enriched = enrich_with_document_metadata(docs, {})
    assert enriched[0]["evidence_grade"] is None
    assert enriched[0]["archived"] is False


def test_enrich_with_document_metadata_distinguishes_pg_missing_from_active():
    # R04(2026-10-13 2차 핸드오프) §4 — PG에서 못 찾은 문서(예: 삭제됨)를
    # "archived=False인 정상 문서"로 보이게 하면 안 된다. pg_missing=True로
    # 구분한다.
    docs = [{"id": "gone"}, {"id": "d2"}]
    enrichment = {"d2": {"evidence_grade": "C", "status": "active"}}
    enriched = enrich_with_document_metadata(docs, enrichment)
    assert enriched[0]["pg_missing"] is True
    assert enriched[0]["archived"] is False  # 누락과는 별개 필드로 분리
    assert enriched[1]["pg_missing"] is False


def test_collapse_duplicate_copies_picks_representative_and_lists_others():
    docs = [
        {"id": "confluence_map:X", "title": "사본"},
        {"id": "tech_repo:X", "title": "원본"},
    ]
    enrichment = {
        "confluence_map:X": {"source_type": "confluence_map", "external_id": "123",
                              "evidence_grade": "C", "status": "active"},
        "tech_repo:X": {"source_type": "tech_repo", "external_id": "123",
                         "evidence_grade": "A", "status": "active"},
    }
    collapsed = collapse_duplicate_copies(docs, enrichment_by_id=enrichment)
    assert len(collapsed) == 1
    assert collapsed[0]["id"] == "tech_repo:X"  # tier 1 소스가 대표
    assert collapsed[0]["copies"] == ["confluence_map:X"]


def test_collapse_duplicate_copies_uses_min_hops_and_route_across_group():
    # G08 잔여 — 대표(tech_repo:X, tier 1)가 2hop으로 도달했어도, 같은 그룹의
    # confluence_map:X가 1hop으로 더 가깝게 도달했으면 묶인 결과는 그 1hop을
    # 보여줘야 한다(대표 선택과 "얼마나 가까운지"는 별개 축).
    docs = [
        {"id": "confluence_map:X", "title": "사본", "hops": 1, "route": ["near"]},
        {"id": "tech_repo:X", "title": "원본", "hops": 2, "route": ["far"]},
    ]
    enrichment = {
        "confluence_map:X": {"source_type": "confluence_map", "external_id": "123",
                              "evidence_grade": "C", "status": "active"},
        "tech_repo:X": {"source_type": "tech_repo", "external_id": "123",
                         "evidence_grade": "A", "status": "active"},
    }
    collapsed = collapse_duplicate_copies(docs, enrichment_by_id=enrichment)
    assert len(collapsed) == 1
    assert collapsed[0]["id"] == "tech_repo:X"  # 대표는 여전히 tier 1 소스
    assert collapsed[0]["hops"] == 1
    assert collapsed[0]["route"] == ["near"]


def test_collapse_duplicate_copies_never_merges_checkitem_en_suffix():
    docs = [
        {"id": "checkitem:FOO", "title": "원본"},
        {"id": "checkitem:FOO_EN", "title": "번역본"},
    ]
    enrichment = {
        "checkitem:FOO": {"source_type": "checkitem", "external_id": "FOO"},
        "checkitem:FOO_EN": {"source_type": "checkitem", "external_id": "FOO_EN"},
    }
    collapsed = collapse_duplicate_copies(docs, enrichment_by_id=enrichment)
    assert len(collapsed) == 2  # checkitem은 collapsible 소스가 아니므로 둘 다 유지


def test_collapse_duplicate_copies_single_copy_passes_through_unchanged():
    docs = [{"id": "tech_repo:X", "title": "원본"}]
    enrichment = {"tech_repo:X": {"source_type": "tech_repo", "external_id": "123"}}
    collapsed = collapse_duplicate_copies(docs, enrichment_by_id=enrichment)
    assert collapsed == docs
    assert "copies" not in collapsed[0]


def test_apply_exposure_policy_excludes_archived_by_default():
    """F05(2026-10-13 최종 핸드오프) §2.5.5 — 기본값은 archived만 뺀다.
    pg_missing은 이 정책의 대상이 아니다(유령 노드는 별도 도구가 다룸) —
    여기서 숨기면 PG 조회가 일시적으로 늦은 정상 케이스까지 숨겨진다."""
    docs = [
        {"id": "a", "archived": False, "pg_missing": False},
        {"id": "b", "archived": True, "pg_missing": False},
        {"id": "c", "archived": False, "pg_missing": True},
    ]
    out = apply_exposure_policy(docs)
    assert [d["id"] for d in out] == ["a", "c"]


def test_apply_exposure_policy_include_archived_keeps_everything():
    docs = [
        {"id": "a", "archived": False, "pg_missing": False},
        {"id": "b", "archived": True, "pg_missing": False},
        {"id": "c", "archived": False, "pg_missing": True},
    ]
    out = apply_exposure_policy(docs, include_archived=True)
    assert [d["id"] for d in out] == ["a", "b", "c"]
