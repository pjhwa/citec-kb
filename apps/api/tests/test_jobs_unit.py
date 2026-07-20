from app.jobs.queue import ALLOWED_TYPES


def test_allowed_job_types():
    assert "ping" in ALLOWED_TYPES
    assert "lexicon_seed" in ALLOWED_TYPES
