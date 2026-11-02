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
