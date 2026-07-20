from app.rag.pipeline import _extract_citation_ids, _looks_like_abstain, _snippet_fallback
from app.rag.packer import PackedChunk


def test_extract_citations_order_unique():
    text = "원인 A [C2]. 조치 B [c1]. 다시 [C2]."
    assert _extract_citation_ids(text) == ["C2", "C1"]


def test_abstain_markers():
    assert _looks_like_abstain("제공된 근거로는 확답할 수 없습니다.")
    assert not _looks_like_abstain("Redis 타임아웃 원인은 peak traffic 입니다 [C1].")


def test_snippet_fallback_has_cites():
    p = PackedChunk(
        cite_id="C1",
        document_id="d",
        chunk_id="c",
        title="T",
        external_id="E",
        source_type="support_history",
        snippet="hello world",
        source_uri=None,
        score=1.0,
        est_tokens=10,
    )
    s = _snippet_fallback([p], "header")
    assert "[C1]" in s and "hello" in s
