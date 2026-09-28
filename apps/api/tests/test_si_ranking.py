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


def test_title_coverage_beats_a_resolution_only_redis_mention():
    from app.si.retrieve import _title_coverage_boost

    q = "Redis 타임아웃 유사 장애"
    assert _title_coverage_boost(q, "[CITECTS-2502] 모니모 Redis TimeOut 이슈") >= 1.5
    assert _title_coverage_boost(q, "[삼성SDS] SCP 사용자 포털 신규 로그인 불가") == 0.0


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


def _hit(document_id: str, source_type: str, title: str, score: float = 0.9) -> SearchHit:
    return SearchHit(
        rank=1,
        score=score,
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


def test_similar_incidents_ranks_by_score_across_source_types(monkeypatch):
    """Phase 2 fix-prompt bug 2 regression.

    Before the fix, similar_incidents() concatenated each source_type's hit
    list in query order (support_history first, then incident_reports) and
    used position-in-that-list as the base rank score. So even a top-scoring
    incident_reports hit landed after every support_history hit and got
    pushed out of top_k. Queried second on purpose here (SI_SOURCE_TYPES is
    ("support_history", "incident_reports")) but must still outrank lower
    scoring support_history hits.
    """

    def fake_hybrid_search(_session, request, query_vector=None):
        st = request.filters.source_type
        if st == "support_history":
            hits = [
                _hit(f"support_history:CITECTS-{i}", "support_history", f"사례 {i}", score=0.5)
                for i in range(5)
            ]
        elif st == "incident_reports":
            hits = [_hit("incident_reports:26090761356", "incident_reports", "SWIM 최상위 사례", score=0.99)]
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

    result = si_retrieve.similar_incidents("이라크 사무소 지역정전 접속 불가", top_k=3)

    doc_ids = [c["document_id"] for c in result["cases"]]
    assert len(doc_ids) == 3
    assert "incident_reports:26090761356" in doc_ids


def _patched(monkeypatch, hits_by_type, *, session=None, seen_filters=None):
    def fake_hybrid_search(_session, request, query_vector=None):
        st = request.filters.source_type
        if seen_filters is not None:
            seen_filters.append(request.filters)
        hits = hits_by_type.get(st, [])
        return SearchResponse(
            query=request.q, exact_tokens=[], total=len(hits), gated=False,
            results=hits, trust_retrieval="medium",
        )

    monkeypatch.setattr(si_retrieve, "embed_query", lambda q: None)
    monkeypatch.setattr(si_retrieve, "hybrid_search", fake_hybrid_search)

    @contextmanager
    def fake_session_scope():
        yield session or _FakeSession()

    monkeypatch.setattr(si_retrieve, "session_scope", fake_session_scope)


def test_environment_is_a_soft_signal_not_a_hard_search_filter(monkeypatch):
    """92% of SWIM docs have no environment, so filtering on it hid them."""
    seen = []
    unknown_env = _hit("incident_reports:1", "incident_reports", "Oracle 블럭 손상 접속 불가", 0.9)
    _patched(monkeypatch, {"incident_reports": [unknown_env]}, seen_filters=seen)

    result = si_retrieve.similar_incidents("Oracle 블럭 손상", top_k=3, environment="csp")

    assert all(f.environment is None for f in seen)
    assert [c["document_id"] for c in result["cases"]] == ["incident_reports:1"]


def test_environment_match_moves_above_mismatch_and_unknown_is_neutral(monkeypatch):
    def hit(doc_id, env):
        h = _hit(doc_id, "incident_reports", "동일 제목 장애 사례", 0.9)
        h.environment = env
        return h

    _patched(
        monkeypatch,
        {
            "incident_reports": [
                hit("incident_reports:unknown", None),
                hit("incident_reports:mismatch", "onprem"),
                hit("incident_reports:match", "csp"),
            ]
        },
    )
    result = si_retrieve.similar_incidents("동일 제목 장애", top_k=3, environment="csp")
    # search order is unknown > mismatch > match; the adjustment moves the
    # match above the mismatch but does not let it overturn a much better rank
    assert [c["document_id"] for c in result["cases"]] == [
        "incident_reports:unknown",
        "incident_reports:match",
        "incident_reports:mismatch",
    ]
    # a mismatch is demoted, never dropped
    assert len(result["cases"]) == 3


def test_source_types_narrows_the_search_and_cases_report_their_source(monkeypatch):
    calls = []
    hits = {
        "support_history": [_hit("support_history:CITECTS-1", "support_history", "지원이력 사례")],
        "incident_reports": [_hit("incident_reports:9", "incident_reports", "SWIM 사례")],
    }

    def fake_hybrid_search(_session, request, query_vector=None):
        calls.append(request.filters.source_type)
        h = hits[request.filters.source_type]
        return SearchResponse(query=request.q, exact_tokens=[], total=len(h), gated=False,
                              results=h, trust_retrieval="medium")

    monkeypatch.setattr(si_retrieve, "embed_query", lambda q: None)
    monkeypatch.setattr(si_retrieve, "hybrid_search", fake_hybrid_search)

    @contextmanager
    def fake_session_scope():
        yield _FakeSession()

    monkeypatch.setattr(si_retrieve, "session_scope", fake_session_scope)

    result = si_retrieve.similar_incidents("사례", top_k=5, source_types=["incident_reports"])
    assert calls == ["incident_reports"]
    assert [c["source_type"] for c in result["cases"]] == ["incident_reports"]

    calls.clear()
    both = si_retrieve.similar_incidents("사례", top_k=5)
    assert calls == ["support_history", "incident_reports"]
    assert {c["source_type"] for c in both["cases"]} == {"support_history", "incident_reports"}


def test_unsupported_source_type_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="unsupported source_types"):
        si_retrieve.similar_incidents("사례", source_types=["tech_repo"])


def test_frame_injection_runs_once_per_source_type(monkeypatch):
    """The token-match injection used to be support_history only."""
    from sqlalchemy.dialects import postgresql

    class _Recording(_FakeSession):
        def __init__(self):
            self.sql = []

        def execute(self, stmt, *a, **k):
            self.sql.append(
                str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            )
            return _FakeExecResult()

    rec = _Recording()
    _patched(monkeypatch, {}, session=rec)
    si_retrieve.similar_incidents("Oracle 블럭 손상 접속 불가", top_k=3)
    injections = [s for s in rec.sql if "issue_frames.quality >=" in s and "ILIKE" in s.upper()]
    assert len(injections) == 2
    assert any("'support_history'" in s for s in injections)
    assert any("'incident_reports'" in s for s in injections)

    rec.sql.clear()
    si_retrieve.similar_incidents("Oracle 블럭 손상 접속 불가", top_k=3, source_types=["incident_reports"])
    injections = [s for s in rec.sql if "issue_frames.quality >=" in s and "ILIKE" in s.upper()]
    assert len(injections) == 1 and "'incident_reports'" in injections[0]
