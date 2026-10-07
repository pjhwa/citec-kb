from app.graph.extract import Edge, extract_hierarchy


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
