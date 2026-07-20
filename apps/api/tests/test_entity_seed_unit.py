from app.entities.seed import load_entity_seed


def test_entity_seed_has_monimo():
    data = load_entity_seed()
    ids = {e["id"] for e in data["entities"]}
    assert "sys:monimo" in ids
    monimo = next(e for e in data["entities"] if e["id"] == "sys:monimo")
    assert any("모니모" in a or a.lower() == "monimo" for a in monimo["aliases"])
