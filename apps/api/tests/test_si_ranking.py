"""SI ranking unit tests (keyword frame injection)."""

from app.si.retrieve import _query_tokens, _text_match_boost


def test_query_tokens_filters_short():
    toks = _query_tokens("GPU burst traffic fabric spine congestion")
    assert "gpu" in toks
    assert "fabric" in toks
    assert "spine" in toks


def test_text_match_boost_multi_token():
    blob = "gpu server fabric spine peak traffic redis timeout bfd"
    b = _text_match_boost("GPU burst traffic fabric spine congestion", blob)
    assert b >= 0.55
