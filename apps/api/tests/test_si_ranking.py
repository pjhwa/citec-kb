"""SI ranking unit tests (keyword frame injection)."""

from contextlib import contextmanager

from app.retrieval.search import SearchHit, SearchResponse
from app.si import retrieve as si_retrieve
from app.si.retrieve import _query_tokens, _text_match_boost


def test_query_tokens_filters_short():
    toks = _query_tokens("GPU burst traffic fabric spine congestion")
    assert "gpu" in toks
    assert "fabric" in toks
    assert "spine" in toks


def test_text_match_boost_multi_token():
    blob = "gpu server fabric spine peak traffic redis timeout bfd"
    b = _text_match_boost("GPU burst traffic fabric spine congestion", blob)
    assert b >= 0.55


class _FakeExecResult:
    def all(self):
        return []


class _FakeSession:
    def execute(self, *_args, **_kwargs):
        return _FakeExecResult()


def _hit(document_id: str, source_type: str, title: str) -> SearchHit:
    return SearchHit(
        rank=1,
        score=0.9,
        document_id=document_id,
        chunk_id=f"{document_id}#0",
        title=title,
        snippet=title,
        source_type=source_type,
        external_id=document_id,
        evidence_grade="B",
        domain=None,
        environment=None,
        work_type=None,
        path_l2=None,
        source_uri=f"file://{document_id}",
        fts_rank=1,
        vec_rank=None,
    )


def test_similar_incidents_merges_support_history_and_incident_reports(monkeypatch):
    """Regression + Phase 2 Track A: both source_types must be queried and merged."""
    calls: list[str] = []

    def fake_hybrid_search(_session, request, query_vector=None):
        st = request.filters.source_type
        calls.append(st)
        if st == "support_history":
            hits = [_hit("support_history:CITECTS-1", "support_history", "지원이력 사례")]
        elif st == "incident_reports":
            hits = [_hit("incident_reports:26090761356", "incident_reports", "SWIM 사례")]
        else:
            hits = []
        return SearchResponse(
            query=request.q, exact_tokens=[], total=len(hits), gated=False,
            results=hits, trust_retrieval="medium",
        )

    monkeypatch.setattr(si_retrieve, "embed_query", lambda q: None)
    monkeypatch.setattr(si_retrieve, "hybrid_search", fake_hybrid_search)

    @contextmanager
    def fake_session_scope():
        yield _FakeSession()

    monkeypatch.setattr(si_retrieve, "session_scope", fake_session_scope)

    result = si_retrieve.similar_incidents("이라크 사무소 지역정전 접속 불가", top_k=5)

    assert calls == ["support_history", "incident_reports"]
    doc_ids = {c["document_id"] for c in result["cases"]}
    assert "support_history:CITECTS-1" in doc_ids
    assert "incident_reports:26090761356" in doc_ids
