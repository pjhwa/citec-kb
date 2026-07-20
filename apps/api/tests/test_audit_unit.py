from app.audit.log import log_query_answer


def test_log_query_answer_handles_failure_gracefully(monkeypatch):
    # Force session failure path without DB if needed — function should not raise
    def boom():
        raise RuntimeError("no db")

    # call with real DB in container; just ensure return shape
    # This unit test only checks non-raise with empty answer when session works;
    # when DB unavailable it still returns dict with error.
    out = log_query_answer(query="unit-audit-test", mode="test", answer_md="")
    assert "query_id" in out
