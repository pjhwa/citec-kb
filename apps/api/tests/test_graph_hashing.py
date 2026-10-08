from app.graph.hashing import compute_graph_hash


def test_hash_changes_when_ancestor_ids_change():
    h1 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1", "2"]})
    h2 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1", "2", "3"]})
    assert h1 != h2


def test_hash_stable_for_identical_input():
    h1 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1"]})
    h2 = compute_graph_hash("content-hash-1", {"ancestor_ids": ["1"]})
    assert h1 == h2


def test_hash_changes_when_content_hash_changes_but_extra_same():
    h1 = compute_graph_hash("content-hash-1", {"components": ["Redis"]})
    h2 = compute_graph_hash("content-hash-2", {"components": ["Redis"]})
    assert h1 != h2


def test_hash_ignores_extra_key_ordering():
    h1 = compute_graph_hash("c", {"a": 1, "b": 2})
    h2 = compute_graph_hash("c", {"b": 2, "a": 1})
    assert h1 == h2
