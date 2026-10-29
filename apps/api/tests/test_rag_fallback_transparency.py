"""C: a failed generation is reported as such and never raises trust."""
from app.rag.packer import PackedChunk
from app.rag import pipeline as P


def _pk(i, ext, ev=True):
    return PackedChunk(cite_id=f"C{i}", document_id=f"d-{ext}", chunk_id=f"c{i}", title=f"T {ext}",
                       external_id=ext, source_type="support_history", snippet="내용 " * 30,
                       source_uri=None, score=0.1, est_tokens=10, evidence_eligible=ev)


def test_fallback_dedups_documents_and_marks_failure():
    packed = [_pk(1, "CITECTS-1"), _pk(2, "CITECTS-1"), _pk(3, "CITECTS-1"), _pk(4, "CITECTS-2")]
    text = P._snippet_fallback(packed, "생성 모델 호출에 실패해 검색 근거만 요약합니다.")
    assert text.startswith("⚠ 생성 실패")
    assert text.count("CITECTS-1") == 1 and text.count("T CITECTS-2") == 1


def test_finalize_failed_generation_status_and_trust():
    packed = [_pk(1, "CITECTS-1"), _pk(2, "CITECTS-2")]
    ans = P._snippet_fallback(packed, "x")
    r = P._finalize(q="q", mode="fast", packed=packed, citations=[], answer=ans,
                    retrieval_meta={"trust_retrieval": "strong"}, llm_error="boom")
    assert r["generation_status"] == "failed_snippet_fallback"
    assert r["citations_used"] == []
    assert r["trust"]["level"] in {"weak", "abstain"}
    assert any("생성 실패" in x for x in r["trust"]["reasons"])


def test_finalize_ok_generation_unchanged():
    packed = [_pk(1, "CITECTS-1"), _pk(2, "CITECTS-2")]
    r = P._finalize(q="q", mode="fast", packed=packed, citations=[], answer="내용 내용 [C1][C2]",
                    retrieval_meta={"trust_retrieval": "strong"}, llm_error=None)
    assert r["generation_status"] == "ok"
    assert r["citations_used"] == ["C1", "C2"]


def test_empty_completion_status():
    packed = [_pk(1, "CITECTS-1")]
    r = P._finalize(q="q", mode="fast", packed=packed, citations=[], answer="x",
                    retrieval_meta={"trust_retrieval": "strong"}, llm_error="empty_completion")
    assert r["generation_status"] == "empty_snippet_fallback"


def test_clean_snippet_strips_index_header():
    a = "[support_history | CITECTS-976 | [CITECTS-976] [기술지원] G-MES DB Out of Memory 이슈] | [CITECTS-976] 본문 시작 내용"
    assert P._clean_snippet(a, 100) == "[CITECTS-976] 본문 시작 내용"
    b = "[confluence_map | 157895699 | Out Of Memory(OOM)와 OOM Killer]\nOut Of Memory 설명\n둘째 줄"
    assert P._clean_snippet(b, 100) == "Out Of Memory 설명 둘째 줄"
    assert P._clean_snippet("헤더 없는 평범한 문장", 100) == "헤더 없는 평범한 문장"
    assert P._clean_snippet("[x | y | z]", 100) == "[x | y | z]"  # header only: keep, never empty


def test_fallback_trust_reasons_drop_missing_cite_reason():
    packed = [_pk(1, "CITECTS-1"), _pk(2, "CITECTS-2")]
    r = P._finalize(q="q", mode="fast", packed=packed, citations=[], answer="x",
                    retrieval_meta={"trust_retrieval": "strong"}, llm_error="boom")
    assert not any("[C#] 인용이 없거나" in x for x in r["trust"]["reasons"])
    assert any("생성 실패" in x for x in r["trust"]["reasons"])
