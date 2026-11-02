import app.synonyms.runtime as rt
from app.retrieval import search_synonyms as ss


def _reset(monkeypatch, loader):
    rt.reset_cache()
    monkeypatch.setattr(rt, "_load", loader)


def test_runtime_groups_are_merged_into_search_synonyms(monkeypatch):
    _reset(monkeypatch, lambda: [["inode2x", "아이노드 고갈"]])
    assert "아이노드 고갈" in ss.search_synonyms("inode2x 확인")
    assert "inode2x" in ss.search_synonyms("아이노드 고갈 장애")
    rt.reset_cache()


def test_static_groups_still_work_when_runtime_empty(monkeypatch):
    _reset(monkeypatch, lambda: [])
    assert "Out of Memory" in ss.search_synonyms("Linux OOM")
    rt.reset_cache()


def test_db_failure_falls_back_to_empty_and_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    _reset(monkeypatch, boom)
    assert rt.runtime_groups() == []
    assert "Out of Memory" in ss.search_synonyms("Linux OOM")
    rt.reset_cache()


def test_failure_keeps_previous_good_value(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return [["inode2x", "아이노드 고갈"]]
        raise RuntimeError("db down")

    _reset(monkeypatch, flaky)
    monkeypatch.setattr(rt, "_TTL_S", 0.0)  # force reload every call
    assert rt.runtime_groups() == [["inode2x", "아이노드 고갈"]]
    assert rt.runtime_groups() == [["inode2x", "아이노드 고갈"]]
    rt.reset_cache()


def test_ttl_caches_between_calls(monkeypatch):
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return []

    _reset(monkeypatch, loader)
    rt.runtime_groups()
    rt.runtime_groups()
    assert calls["n"] == 1
    rt.reset_cache()


def test_invalid_db_groups_are_filtered(monkeypatch):
    _reset(monkeypatch, lambda: [["행", "펜싱"], ["inode2x", "아이노드 고갈"]])
    assert rt.runtime_groups() == [["inode2x", "아이노드 고갈"]]
    rt.reset_cache()


def test_failing_load_is_called_once_with_default_ttl(monkeypatch):
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("db down")

    _reset(monkeypatch, boom)
    for _ in range(3):
        assert rt.runtime_groups() == []
    assert calls["n"] == 1
    rt.reset_cache()


def test_load_passes_include_proposed_flag(monkeypatch, real_runtime_load):
    import app.synonyms.store as store

    seen = []
    monkeypatch.setattr(
        store, "load_groups", lambda include_proposed=False: seen.append(include_proposed) or []
    )
    monkeypatch.delenv("SEARCH_SYNONYMS_INCLUDE_PROPOSED", raising=False)
    real_runtime_load()
    monkeypatch.setenv("SEARCH_SYNONYMS_INCLUDE_PROPOSED", "1")
    real_runtime_load()
    assert seen == [False, True]


def test_reentrant_call_during_reload_returns_previous_and_does_not_recurse(monkeypatch):
    state = {"n": 0, "inner": None}

    def loader():
        state["n"] += 1
        if state["n"] == 1:
            return [["inode2x", "아이노드 고갈"]]
        state["inner"] = rt.runtime_groups()  # re-entrant / concurrent caller
        return [["other2x", "다른 용어"]]

    _reset(monkeypatch, loader)
    first = rt.runtime_groups()
    assert first == [["inode2x", "아이노드 고갈"]]
    rt._cache = (-1e9, first)  # expire the entry
    second = rt.runtime_groups()
    assert state["inner"] == first  # got previous value
    assert state["n"] == 2  # inner call did not reach _load
    assert second == [["other2x", "다른 용어"]]
    rt.reset_cache()


def test_ttl_parsing():
    assert rt._parse_ttl("0") == 0.0
    assert rt._parse_ttl("abc") == 60.0
    assert rt._parse_ttl(None) == 60.0
    assert rt._parse_ttl("") == 60.0
    assert rt._parse_ttl("5") == 5.0


def test_runtime_synonym_excluded_from_like_terms(monkeypatch):
    """no_like in search._fts_search is built from search_synonyms(base) (8ce9bf8).

    Exercising the real function needs a DB session (not refactored here), so
    assert on the search_synonyms() output it consumes, plus the source wiring.
    """
    import inspect

    from app.retrieval import search

    _reset(monkeypatch, lambda: [["inode2x", "아이노드 고갈"]])
    no_like = {x.lower() for x in ss.search_synonyms("inode2x")}
    assert "아이노드 고갈" in no_like
    src = inspect.getsource(search)
    assert "no_like = {x.lower() for x in search_synonyms(base)}" in src
    assert "t.lower() not in no_like" in src
    rt.reset_cache()
