import pytest

import app.synonyms.runtime as _rt

_REAL_LOAD = _rt._load


@pytest.fixture
def real_runtime_load():
    return _REAL_LOAD


@pytest.fixture(autouse=True)
def _no_runtime_synonym_db(monkeypatch):
    """Unit tests must never reach a real DB through search_synonyms."""
    import app.synonyms.runtime as rt

    rt.reset_cache()
    monkeypatch.setattr(rt, "_load", lambda: [])
    yield
    rt.reset_cache()
