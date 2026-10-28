"""kb_title_tokens (A1) — API는 `buckets` 키로 토큰을 돌려준다 (HTTP 모의, DB 불필요)."""

import asyncio

import httpx

import server


def _mock(monkeypatch, payload):
    monkeypatch.setattr(
        server,
        "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        ),
    )


def _run(**kw):
    return asyncio.run(server.kb_title_tokens(**kw))


def test_buckets_key_is_read(monkeypatch):
    _mock(monkeypatch, {
        "document_n": 289, "unique_tokens": 812, "top_k": 2, "source_type": "support_history",
        "buckets": [
            {"token": "기술지원", "count": 120, "share": 0.4152},
            {"token": "SCP", "count": 40, "share": 0.1384},
        ],
    })
    out = _run(top_k=2)
    lines = out.splitlines()
    assert lines[0].startswith("title_tokens top_k=2")
    assert "document_n=289" in lines[0] and "unique_tokens=812" in lines[0]
    assert "- 기술지원: 120 (41.5%)" in out
    assert "- SCP: 40 (13.8%)" in out


def test_legacy_tokens_and_items_keys_still_work(monkeypatch):
    _mock(monkeypatch, {"tokens": [{"token": "a", "count": 3}]})
    assert "- a: 3" in _run()
    _mock(monkeypatch, {"items": [{"key": "b", "count": 2}]})
    assert "- b: 2" in _run()


def test_empty_result_is_stated_not_silent(monkeypatch):
    _mock(monkeypatch, {"document_n": 0, "unique_tokens": 0, "buckets": []})
    out = _run(component="없는컴포넌트")
    assert "토큰이 없습니다" in out and "document_n=0" in out


def test_missing_keys_do_not_crash(monkeypatch):
    _mock(monkeypatch, {})
    out = _run()
    assert "토큰이 없습니다" in out


def test_top_k_truncates(monkeypatch):
    _mock(monkeypatch, {"document_n": 5, "unique_tokens": 5,
                        "buckets": [{"token": f"t{i}", "count": 5 - i} for i in range(5)]})
    out = _run(top_k=3)
    assert "- t2: 3" in out and "t3" not in out
