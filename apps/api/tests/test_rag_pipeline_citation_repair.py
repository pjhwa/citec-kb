"""Unit tests for app.rag.pipeline._finalize's citation-count parsing (no DB).

P1-C (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §10,
REVIEW.md item 9): the "자동 보강 근거"/candidate-append block used to format
each bullet as "[C1] title: snippet" — exactly what _extract_citation_ids()
looks for — so an answer with zero real inline citations could still read
as citing C1/C2/C3 once the auto-append ran, inflating trust.
"""

from __future__ import annotations

from app.rag.packer import PackedChunk
from app.rag.pipeline import _finalize


def _packed(cite_id: str, *, evidence_eligible: bool = True) -> PackedChunk:
    return PackedChunk(
        cite_id=cite_id,
        document_id=f"doc-{cite_id}",
        chunk_id=f"chunk-{cite_id}",
        title=f"title {cite_id}",
        external_id=cite_id,
        source_type="support_history",
        snippet="근거 스니펫 텍스트",
        source_uri=None,
        score=0.05,
        est_tokens=10,
        evidence_eligible=evidence_eligible,
    )


_RETRIEVAL_META = {"trust_retrieval": "medium"}


def test_new_candidate_append_format_is_not_parsed_as_citations():
    """The current (fixed) bracket-free format must not be picked up by the
    same [C#] parser that decides which citations the model "used"."""
    packed = [_packed("C1"), _packed("C2")]
    answer = (
        "일반적인 설명입니다.\n\n"
        "(자동 첨부 — 모델이 인용하지 않은 검색 후보. 사실 근거로 확정하지 말 것)\n"
        "- 후보 C1: title C1 — 근거 스니펫 텍스트\n"
        "- 후보 C2: title C2 — 근거 스니펫 텍스트"
    )
    result = _finalize(
        q="q",
        mode="fast",
        packed=packed,
        citations=[],
        retrieval_meta=_RETRIEVAL_META,
        answer=answer,
        llm_error=None,
    )
    assert result["citations_used"] == []
    # evidence stays weak — no inline citation was actually used
    assert result["trust"]["evidence"] == "weak"


def test_old_bracket_format_would_have_been_misread_as_citations():
    """Documents the exact bug being fixed: the OLD format (kept here only
    as a negative-control string, not produced by the current code) is
    exactly what _extract_citation_ids picks up — proving the new format
    change in test_new_candidate_append_format_is_not_parsed_as_citations
    is the thing that matters, not an accident of unrelated wording."""
    packed = [_packed("C1"), _packed("C2")]
    old_style_answer = (
        "일반적인 설명입니다.\n\n"
        "(자동 보강 근거)\n"
        "- [C1] title C1: 근거 스니펫 텍스트\n"
        "- [C2] title C2: 근거 스니펫 텍스트"
    )
    result = _finalize(
        q="q",
        mode="fast",
        packed=packed,
        citations=[],
        retrieval_meta=_RETRIEVAL_META,
        answer=old_style_answer,
        llm_error=None,
    )
    assert result["citations_used"] == ["C1", "C2"]  # the bug, if reintroduced
