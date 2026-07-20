from app.bundles.store import delete_bundle, get_bundle, normalize_bundle, save_bundle


def test_normalize_bundle():
    b = normalize_bundle({"name": "demo", "title": "Demo", "checklist": ["a"]})
    assert b["id"] == "bundle:demo"
    assert b["checklist"] == ["a"]


def test_save_and_delete_bundle(tmp_path, monkeypatch):
    # force writable dir
    import app.bundles.store as store

    monkeypatch.setattr(store, "_writable_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_seed_dirs", lambda: [tmp_path])
    # also patch match load path
    import app.bundles.match as match

    monkeypatch.setattr(match, "_seed_dirs", lambda: [tmp_path])

    saved = save_bundle(
        {
            "name": "unit-test-pack",
            "title": "Unit test pack",
            "symptom_hints": ["xyzzy-unique-hint"],
            "checklist": ["step1"],
            "commands": ["echo ok"],
            "related_components": ["Test"],
        }
    )
    assert saved["bundle"]["name"] == "unit-test-pack"
    assert (tmp_path / "unit-test-pack.json").is_file()
    assert get_bundle("unit-test-pack") is not None
    assert delete_bundle("unit-test-pack") is True
    assert get_bundle("unit-test-pack") is None
