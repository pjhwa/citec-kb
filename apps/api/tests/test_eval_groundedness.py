"""Unit tests for app.eval.groundedness.evaluate_one/run_eval (no DB — mocks
run_fast_rag directly).

D13 (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §10,
REVIEW.md item 9): a provider failure whose caller falls back to a bare
snippet-with-citation used to score `ok=True` — llm_error was captured in
the row but never checked before deciding "ok".
"""

from __future__ import annotations

import app.eval.groundedness as gmod


def _fake_result(*, answer, citations_used, citations, llm_error=None, abstained=False, trust_level="weak"):
    return {
        "answer": answer,
        "citations_used": citations_used,
        "citations": citations,
        "abstained": abstained,
        "trust": {"level": trust_level},
        "retrieval": {"trust_retrieval": "weak"},
        "llm_error": llm_error,
    }


def test_llm_error_with_citation_is_not_ok(monkeypatch):
    """The exact D13 shape: has_citation=True, llm_error set — must not
    read as a real generation success."""
    monkeypatch.setattr(
        gmod,
        "run_fast_rag",
        lambda *a, **kw: _fake_result(
            answer="[C1] 검색 근거 스니펫",
            citations_used=["C1"],
            citations=[{"snippet": "검색 근거 스니펫"}],
            llm_error="provider unavailable",
        ),
    )
    row = gmod.evaluate_one({"id": "d13", "q": "sample"})
    assert row["citation_format_ok"] is True  # the old check still "passes"
    assert row["llm_error"] == "provider unavailable"
    assert row["ok"] is False  # but overall ok must not


def test_real_generation_with_citation_and_no_error_is_ok(monkeypatch):
    monkeypatch.setattr(
        gmod,
        "run_fast_rag",
        lambda *a, **kw: _fake_result(
            answer="[C1] 실제 생성된 답변입니다.",
            citations_used=["C1"],
            citations=[{"snippet": "실제 생성된 답변입니다."}],
            llm_error=None,
        ),
    )
    row = gmod.evaluate_one({"id": "ok1", "q": "sample"})
    assert row["citation_format_ok"] is True
    assert row["ok"] is True


def test_expected_abstain_with_llm_error_is_still_ok(monkeypatch):
    """A provider failure during a query that SHOULD abstain isn't a false
    "ok" — there was nothing to generate in the first place."""
    monkeypatch.setattr(
        gmod,
        "run_fast_rag",
        lambda *a, **kw: _fake_result(
            answer="",
            citations_used=[],
            citations=[],
            llm_error="provider unavailable",
            abstained=True,
            trust_level="abstain",
        ),
    )
    row = gmod.evaluate_one({"id": "abstain1", "q": "sample", "expect_abstain": True})
    assert row["ok"] is True


def test_run_eval_llm_error_rate_and_gate(monkeypatch):
    """run_eval must surface llm_error_rate and the pass gate must not pass
    a run where every answered query had a provider failure, even if
    citation_rate/overlap_rate alone would clear their thresholds."""

    def fake_run_fast_rag(q, **kw):
        return _fake_result(
            answer="[C1] 검색 근거 스니펫",
            citations_used=["C1"],
            citations=[{"snippet": "검색 근거 스니펫 relevant text"}],
            llm_error="provider unavailable",
        )

    monkeypatch.setattr(gmod, "run_fast_rag", fake_run_fast_rag)
    gold = {
        "queries": [{"id": f"q{i}", "q": "검색 근거"} for i in range(12)],
        "gate": {"min_answered": 10, "citation_rate_min": 0.6, "overlap_ok_rate_min": 0.0},
    }
    report = gmod.run_eval(gold)
    assert report["citation_rate"] == 1.0
    assert report["llm_error_rate"] == 1.0
    assert report["row_ok_rate"] == 0.0
    assert report["pass"] is False
