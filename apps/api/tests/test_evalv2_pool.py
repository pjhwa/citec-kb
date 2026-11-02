import io
import urllib.error

import pytest

from app.evalv2 import llmjson, pool


def test_extract_json_fences_prose_and_garbage():
    assert llmjson.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llmjson.extract_json('설명 {"a": [1, 2]} 끝') == {"a": [1, 2]}
    assert llmjson.extract_json("not json") is None
    assert llmjson.extract_json("") is None
    assert llmjson.extract_json(None) is None


def _fake_search(table):
    def search(q, filters, multi_query, top_k):
        return table[multi_query][:top_k]
    return search


def _r(eid, title="t", st="support_history", ee=True):
    return {"external_id": eid, "title": title, "source_type": st, "evidence_eligible": ee}


def test_pool_merges_arms_and_orders_by_agreement():
    table = {
        True: [_r("A#1"), _r("B"), _r("C")],
        False: [_r("B"), _r("D"), _r("A")],
    }
    out = pool.pool_candidates(_fake_search(table), "q", {}, top_n=3, max_pool=10)
    ids = [c["external_id"] for c in out]
    assert set(ids) == {"A", "B", "C", "D"}
    # A and B are found by both arms, so they come before C and D
    assert set(ids[:2]) == {"A", "B"}
    b = next(c for c in out if c["external_id"] == "B")
    assert b["arms"] == {"mq_on": 2, "mq_off": 1}


def test_pool_dedupes_chunk_suffix_and_respects_cap():
    table = {True: [_r("A#1"), _r("A#2"), _r("B")], False: [_r("C"), _r("D"), _r("E")]}
    out = pool.pool_candidates(_fake_search(table), "q", {}, top_n=3, max_pool=3)
    assert len(out) == 3
    assert [c["external_id"] for c in out].count("A") <= 1


def test_pool_keeps_seed_docs_even_beyond_cap():
    table = {True: [_r("A"), _r("B"), _r("C")], False: [_r("D"), _r("E"), _r("F")]}
    seed = [{"external_id": "ZZZ", "title": "seed", "source_type": "support_history", "evidence_eligible": True}]
    out = pool.pool_candidates(_fake_search(table), "q", {}, top_n=3, max_pool=3, extra=seed)
    assert "ZZZ" in [c["external_id"] for c in out]
    assert len(out) == 3
    z = next(c for c in out if c["external_id"] == "ZZZ")
    assert "seed" in z["arms"]


def test_pool_survives_search_failure_on_one_arm():
    def search(q, filters, multi_query, top_k):
        if multi_query:
            raise RuntimeError("boom")
        return [_r("X")]

    out = pool.pool_candidates(search, "q", {}, top_n=3, max_pool=5)
    assert [c["external_id"] for c in out] == ["X"]


def _boom(q, filters, multi_query, top_k):
    raise RuntimeError("boom")


def test_all_arms_failed_raises():
    with pytest.raises(RuntimeError, match="all search arms failed"):
        pool.pool_candidates(_boom, "q", {}, top_n=3, max_pool=5)


def test_all_arms_failed_with_seed_returns_seeds_only():
    seed = [{"external_id": "S1", "title": "s", "source_type": "x", "evidence_eligible": True}]
    out = pool.pool_candidates(_boom, "q", {}, top_n=3, max_pool=5, extra=seed)
    assert [c["external_id"] for c in out] == ["S1"]


class _Resp:
    def __init__(self, payload=b'{"results": [{"external_id": "A"}]}'):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_err(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


def test_rest_search_does_not_retry_4xx(monkeypatch):
    calls = {"n": 0}

    def fake_open(req, timeout=None):
        calls["n"] += 1
        raise _http_err(400)

    monkeypatch.setattr(pool.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(pool, "_sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        pool.rest_search("http://x", attempts=3)("q", {}, True, 5)
    assert calls["n"] == 1


def test_rest_search_retries_5xx_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_open(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_err(503)
        return _Resp()

    monkeypatch.setattr(pool.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(pool, "_sleep", lambda s: None)
    out = pool.rest_search("http://x", attempts=3)("q", {}, True, 5)
    assert calls["n"] == 2 and out[0]["external_id"] == "A"


def test_rest_search_does_not_retry_bad_json(monkeypatch):
    calls = {"n": 0}

    def fake_open(req, timeout=None):
        calls["n"] += 1
        return _Resp(b"not json")

    monkeypatch.setattr(pool.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(pool, "_sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        pool.rest_search("http://x", attempts=3)("q", {}, True, 5)
    assert calls["n"] == 1
