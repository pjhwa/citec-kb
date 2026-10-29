"""B1: PISAOLNX code-prefix expansion is dropped when the request is already
restricted, and OOM-style synonym groups reach the search variants."""
from app.retrieval.search import SearchFilters, _code_prefix_restricted, build_fts_variants
from app.retrieval.search_synonyms import SYNONYM_GROUPS, search_synonyms


def test_prefix_present_when_unrestricted():
    assert "PISAOLNX" in build_fts_variants("Linux OOM")


def test_prefix_dropped_when_restricted():
    v = build_fts_variants("Linux OOM", restricted=True)
    assert "PISAOLNX" not in v
    assert "Linux" in v or "Out of Memory" in v


def test_prefix_dropped_for_linux_filesystem_when_restricted():
    assert "PISAOLNX" not in build_fts_variants("리눅스 파일시스템 체크리스트", restricted=True)
    assert "PISAOLNX" in build_fts_variants("리눅스 파일시스템 체크리스트")


def test_restricted_predicate():
    assert _code_prefix_restricted(SearchFilters(source_type="checkitem"))
    assert _code_prefix_restricted(SearchFilters(domain="linux"))
    assert not _code_prefix_restricted(SearchFilters())
    assert not _code_prefix_restricted(SearchFilters(source_type="support_history"))


def test_oom_synonyms_reach_variants():
    v = [x.lower() for x in build_fts_variants("Linux OOM", restricted=True)]
    assert "out of memory" in v and "메모리 부족" in v


def test_synonyms_bidirectional_and_word_bounded():
    assert "OOM" in search_synonyms("Out of Memory 로그")
    assert "커널 패닉" in search_synonyms("kernel panic 원인")
    assert search_synonyms("room boom") == []  # 'oom' inside a word must not hit
    assert search_synonyms("k8s") == ["Kubernetes", "쿠버네티스"]


def test_no_short_korean_terms():
    for g in SYNONYM_GROUPS:
        for t in g:
            if any("가" <= c <= "힣" for c in t):
                assert len(t.replace(" ", "")) >= 3, t


def test_synonym_variants_are_excluded_from_title_ilike_scan():
    """The ILIKE branch is a seq scan; synonym expansions must not feed it."""
    import inspect
    import app.retrieval.search as S

    src = inspect.getsource(S.fts_search)
    assert "no_like" in src and "search_synonyms(base)" in src
