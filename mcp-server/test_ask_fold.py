"""kb_ask must not report a snippet fallback as a bare error string."""

from ask_fold import KbAskError, fold_ask_events


def _done(answer: str, llm_error: str | None = None) -> dict:
    return {
        "type": "done",
        "result": {
            "answer": answer,
            "llm_error": llm_error,
            "citations": [
                {
                    "id": "C1",
                    "title": "t",
                    "external_id": "CITECTS-1",
                    "source_type": "support_history",
                    "path": "support_history/CITECTS-1.md",
                }
            ],
        },
    }


def test_error_then_done_returns_snippet_not_the_error_string():
    text = fold_ask_events(
        [
            {"type": "error", "error": "stream requires openrouter, got fabrix"},
            _done("생성 모델 호출에 실패해 검색 근거만 요약합니다.\n- [C1] t", "stream requires openrouter, got fabrix"),
        ]
    )
    assert "stream requires openrouter" not in text.split("\n", 1)[0] or "스니펫" in text
    assert text.startswith("[스니펫 요약]")
    assert "검색 근거만 요약" in text
    assert "support_history/CITECTS-1.md" in text


def test_three_templates_share_the_same_fallback_shape():
    for template in ("general", "general", "support_history"):
        text = fold_ask_events(
            [
                {"type": "error", "error": f"stream requires openrouter, got fabrix ({template})"},
                _done("생성 모델 호출에 실패해 검색 근거만 요약합니다.", "stream requires openrouter, got fabrix"),
            ]
        )
        assert text.startswith("[스니펫 요약]")
        assert "CITECTS-1" in text


def test_stream_without_done_raises():
    try:
        fold_ask_events([{"type": "error", "error": "stream requires openrouter, got fabrix"}])
    except KbAskError as exc:
        assert "openrouter" in str(exc)
    else:
        raise AssertionError("expected KbAskError")


def test_failed_generation_first_line_and_dedup_sources():
    cit = {"id": "C1", "external_id": "CITECTS-1", "source_type": "support_history", "title": "t"}
    events = [
        {"type": "done", "result": {
            "answer": "⚠ 생성 실패 — 아래는 LLM 답변이 아니라 검색 스니펫 요약입니다.\n헤더\n- [C1] t: x",
            "llm_error": "boom", "generation_status": "failed_snippet_fallback",
            "citations": [cit, dict(cit, id="C2"), dict(cit, id="C3")],
        }},
    ]
    text = fold_ask_events(events)
    first = text.split("\n", 1)[0]
    assert first.startswith("[스니펫 요약]") and "생성 실패" in first
    assert "failed_snippet_fallback" in first
    assert text.count("support_history/CITECTS-1.md") == 2  # path line + mcp: line, once per doc
    assert text.count("⚠ 생성 실패") == 1


def test_ok_generation_has_no_failure_header():
    text = fold_ask_events([_done("정상 답변 [C1]", None) | {}]) if False else fold_ask_events(
        [{"type": "done", "result": {"answer": "정상 답변 [C1]", "llm_error": None, "generation_status": "ok", "citations": []}}]
    )
    assert "생성 실패" not in text
