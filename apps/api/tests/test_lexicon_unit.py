from app.lexicon.seed import load_lexicon_seed_file


def test_lexicon_seed_has_gro():
    data = load_lexicon_seed_file()
    cans = {t["canonical"] for t in data["terms"]}
    assert "GRO" in cans
    assert "Redis" in cans
