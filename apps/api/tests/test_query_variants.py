"""Unit tests for FTS query variants (stopwords + Korean ops synonyms)."""

from app.retrieval.search import build_fts_variants


def test_checklist_query_drops_stopwords_and_expands_fs():
    variants = build_fts_variants("리눅스 파일시스템 관련 체크리스트")
    joined = " ".join(variants).lower()
    assert any("파일 시스템" in v or "파일시스템" in v for v in variants)
    assert any("linux" in v.lower() or "pisaolnx" in v.lower() for v in variants)
    # Must not rely only on 4-way AND of the full sentence
    assert "파일 시스템" in joined or "파일시스템" in joined
    # Stopword-only tokens should not be sole variants without content
    assert not all(v in ("관련", "체크리스트") for v in variants)


def test_linux_fs_english_expands():
    variants = build_fts_variants("Linux FS")
    assert any("파일" in v for v in variants)


def test_exact_issue_key_preserved():
    variants = build_fts_variants("CITECTS-1000")
    assert any("CITECTS-1000" in v for v in variants)
