"""Unit tests for ranking fusion / exact boost / quality gate (shipped functions)."""

from app.retrieval.fusion import (
    RankedHit,
    apply_exact_boost,
    extract_exact_tokens,
    quality_gate,
    reciprocal_rank_fusion,
)


def test_extract_exact_tokens_issue_key():
    toks = extract_exact_tokens("CITECTS-979 성능 이슈 원인")
    assert "CITECTS-979" in toks


def test_extract_exact_tokens_pisa_and_sysctl():
    toks = extract_exact_tokens("PISAOLNX_01.01.01 과 vm.min_free_kbytes 확인")
    assert any(t.startswith("PISAOLNX_") for t in toks)
    assert "vm.min_free_kbytes" in toks


def test_rrf_prefers_items_high_in_both_lists():
    fts = ["a", "b", "c"]
    vec = ["c", "a", "d"]
    scores = reciprocal_rank_fusion([fts, vec], k=60)
    # a and c appear in both → higher than b or d
    assert scores["a"] > scores["b"]
    assert scores["c"] > scores["d"]
    assert scores["a"] > scores["d"] or scores["c"] > scores["b"]


def test_exact_boost_raises_matching_chunk():
    base = {"c1": 0.1, "c2": 0.11}
    texts = {
        "c1": "title CITECTS-979 root cause analysis",
        "c2": "unrelated storage note",
    }
    out = apply_exact_boost(base, id_to_text=texts, exact_tokens=["CITECTS-979"], boost=0.15)
    assert out["c1"] > out["c2"]
    assert out["c1"] == 0.1 + 0.15


def test_quality_gate_empties_weak_top_score():
    hits = [
        RankedHit(chunk_id="a", document_id="d1", score=0.001),
        RankedHit(chunk_id="b", document_id="d2", score=0.0005),
    ]
    assert quality_gate(hits, min_top_score=0.012) == []


def test_quality_gate_keeps_strong_hits():
    hits = [
        RankedHit(chunk_id="a", document_id="d1", score=0.05),
        RankedHit(chunk_id="b", document_id="d2", score=0.04),
        RankedHit(chunk_id="c", document_id="d3", score=0.03),
    ]
    kept = quality_gate(hits, min_top_score=0.012, max_results=2)
    assert len(kept) == 2
    assert kept[0].chunk_id == "a"
