from app.graph.extract import Edge, extract_hierarchy, extract_structured_components


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
