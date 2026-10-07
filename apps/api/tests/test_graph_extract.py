from app.graph.extract import (
    Edge,
    extract_business_entities,
    extract_hierarchy,
    extract_lexicon_components,
    extract_references,
    extract_structured_components,
)


def test_extract_hierarchy_returns_parent_of_edges_in_order():
    doc = {"id": "d1", "metadata": {"ancestor_ids": ["root", "mid", "d1"]}}
    edges = extract_hierarchy(doc)
    assert edges == [
        Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
             target_value="root", tag="EXTRACTED"),
        Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
             target_value="mid", tag="EXTRACTED"),
    ]


def test_extract_hierarchy_returns_empty_when_no_ancestor_ids():
    doc = {"id": "d1", "metadata": {}}
    assert extract_hierarchy(doc) == []


def test_extract_hierarchy_excludes_self_from_ancestor_chain():
    # adapters.py appends the page's own id as the last element of ancestor_ids
    doc = {"id": "d1", "metadata": {"ancestor_ids": ["d1"]}}
    assert extract_hierarchy(doc) == []


def test_extract_structured_components_from_issue_frame():
    doc = {"id": "d1", "source_type": "incident_reports"}
    issue_frame = {"components": ["Redis", "Network"]}
    edges = extract_structured_components(doc, issue_frame=issue_frame, checkitem=None)
    assert edges == [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="Redis", tag="EXTRACTED"),
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="Network", tag="EXTRACTED"),
    ]


def test_extract_structured_components_from_checkitem_area():
    doc = {"id": "d2", "source_type": "checkitem"}
    checkitem = {"area": "3PAR", "category_1": "구성"}
    edges = extract_structured_components(doc, issue_frame=None, checkitem=checkitem)
    assert edges == [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="3PAR", tag="EXTRACTED"),
    ]
    # category_1("구성"/"Configuration" 등 5개 PISA 평가축)은 컴포넌트가 아니므로 안 나온다


def test_extract_structured_components_returns_empty_when_no_frame_or_checkitem():
    doc = {"id": "d3", "source_type": "tech_repo"}
    assert extract_structured_components(doc, issue_frame=None, checkitem=None) == []


def test_extract_structured_components_dedupes_empty_strings():
    doc = {"id": "d4", "source_type": "incident_reports"}
    issue_frame = {"components": ["Redis", "", "Redis"]}
    edges = extract_structured_components(doc, issue_frame=issue_frame, checkitem=None)
    assert [e.target_value for e in edges] == ["Redis"]


def test_extract_business_entities_includes_business_and_platform_only():
    doc = {"id": "d1"}
    document_entities = [
        {"entity_id": "sys:monimo", "entity_type": "business_system"},
        {"entity_id": "sys:scp", "entity_type": "platform"},
        {"entity_id": "sys:redis", "entity_type": "component"},
    ]
    edges = extract_business_entities(doc, document_entities=document_entities)
    assert edges == [
        Edge(rel_type="MENTIONS_ENTITY", target_label="BusinessEntity", target_key="id",
             target_value="sys:monimo", tag="EXTRACTED"),
        Edge(rel_type="MENTIONS_ENTITY", target_label="BusinessEntity", target_key="id",
             target_value="sys:scp", tag="EXTRACTED"),
    ]


def test_extract_business_entities_empty_when_no_rows():
    assert extract_business_entities({"id": "d1"}, document_entities=[]) == []


def test_extract_lexicon_components_matches_canonical_and_variant():
    doc = {"id": "d1", "body_md": "Redis timeout 발생, 레디스 재기동함"}
    lexicon_map = {
        "redis": ["Redis", "레디스", "redis"],
        "레디스": ["Redis", "레디스", "redis"],
        "timeout": ["timeout", "타임아웃", "time-out", "timed out"],
    }
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    canonicals = sorted({e.target_value for e in edges})
    assert canonicals == ["Redis", "timeout"]
    assert all(e.tag == "INFERRED" for e in edges)


def test_extract_lexicon_components_empty_body_returns_empty():
    doc = {"id": "d1", "body_md": ""}
    assert extract_lexicon_components(doc, lexicon_map={"redis": ["Redis"]}) == []


def test_extract_lexicon_components_no_match_returns_empty():
    doc = {"id": "d1", "body_md": "전혀 관련 없는 본문"}
    assert extract_lexicon_components(doc, lexicon_map={"redis": ["Redis"]}) == []


def test_extract_references_finds_ticket_id_and_resolves_via_index():
    doc = {"id": "d1", "body_md": "CITECTS-1234 사례와 유사함. citects-5678도 참고"}
    index = {"citects-1234": "doc-a", "citects-5678": "doc-b"}
    edges = extract_references(doc, external_id_index=index)
    assert sorted(e.target_value for e in edges) == ["doc-a", "doc-b"]
    assert all(e.rel_type == "REFERENCES" and e.target_label == "Document" for e in edges)


def test_extract_references_skips_unresolved_ids():
    doc = {"id": "d1", "body_md": "CITECTS-9999 참고"}
    edges = extract_references(doc, external_id_index={})
    assert edges == []


def test_extract_references_empty_body_returns_empty():
    doc = {"id": "d1", "body_md": ""}
    assert extract_references(doc, external_id_index={"citects-1": "x"}) == []
