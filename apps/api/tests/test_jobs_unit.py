from app.jobs.queue import ALLOWED_TYPES


def test_allowed_job_types():
    assert "ping" in ALLOWED_TYPES
    assert "lexicon_seed" in ALLOWED_TYPES
    assert "insight_reindex" not in ALLOWED_TYPES  # retired 2026-11-05
    assert "decision_staleness_scan" in ALLOWED_TYPES
    assert "embed_document" in ALLOWED_TYPES
