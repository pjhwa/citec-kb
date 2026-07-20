"""Context packer tests."""

from types import SimpleNamespace

from app.rag.packer import estimate_tokens, format_context_block, pack_chunks


def test_pack_respects_budget():
    hits = [
        SimpleNamespace(
            document_id=f"d{i}",
            chunk_id=f"c{i}",
            title=f"Title {i}",
            external_id=f"ID-{i}",
            source_type="support_history",
            snippet="본문 " * 200,
            source_uri=None,
            score=0.1,
        )
        for i in range(20)
    ]
    packed = pack_chunks(hits, max_context_tokens=500, per_chunk_chars=400)
    assert 1 <= len(packed) < 20
    assert packed[0].cite_id == "C1"
    total = sum(p.est_tokens for p in packed)
    assert total <= 500 + 100  # small slack for first chunk


def test_format_contains_cite_ids():
    hits = [
        SimpleNamespace(
            document_id="d1",
            chunk_id="c1",
            title="T",
            external_id="E",
            source_type="checkitem",
            snippet="hello",
            source_uri=None,
            score=1.0,
        )
    ]
    packed = pack_chunks(hits, max_context_tokens=2000)
    block = format_context_block(packed)
    assert "[C1]" in block
    assert estimate_tokens("한글") >= 1
