import json

from app.evalv2 import qfilter as qf


def test_extract_q_plain_and_with_other_keys_and_truncation():
    assert qf.extract_q("q=Linux OOM 점검") == ("Linux OOM 점검", False)
    assert qf.extract_q("q=메모리 부족 symptom_text=abc") == ("메모리 부족", False)
    assert qf.extract_q("anchor_value=123") == ("", False)
    q, trunc = qf.extract_q("q=" + "가" * 600)
    assert trunc is True


def test_parse_log_tsv_handles_quotes_and_bad_rows():
    text = '2026-10-20\tkb_search\t3\tq=Redis 타임아웃 원인\n' \
           '2026-10-21\tkb_search\t1\t"q=쉼표, 따옴표"" 포함"\n' \
           'bad row\n' \
           '2026-10-22\tkb_graph_explore\t2\tanchor_value=9\n'
    rows = qf.parse_log_tsv(text)
    assert [r["q"] for r in rows] == ["Redis 타임아웃 원인", '쉼표, 따옴표" 포함']
    assert rows[0]["n"] == 3


def test_exclusion_reasons():
    known = {qf.norm("이미 있는 질문입니다")}
    assert qf.exclusion_reason("abc", False, known) == "too_short"
    assert qf.exclusion_reason("x" * 301, False, known) == "too_long"
    assert qf.exclusion_reason("CITECTS-2634", False, known) == "identifier_only"
    assert qf.exclusion_reason("PISAOLNX_04.04.03", False, known) == "identifier_only"
    assert qf.exclusion_reason("1234567", False, known) == "identifier_only"
    assert qf.exclusion_reason("이미  있는 질문입니다", False, known) == "known_case"
    assert qf.exclusion_reason("정상 질의입니다", True, known) == "truncated"
    assert qf.exclusion_reason("정상 질의입니다 서버", False, known) is None


def test_ordinary_sentences_are_not_identifiers():
    for q in ("Linux OOM", "메모리 부족 원인", "SCP Nuri 방화벽"):
        assert qf.exclusion_reason(q, False, set()) is None, q


def test_prepare_real_dedupes_counts_and_flags_hints():
    rows = [
        {"date": "d", "tool": "t", "n": 2, "q": "Redis  타임아웃 원인", "truncated": False},
        {"date": "d", "tool": "t", "n": 3, "q": "redis 타임아웃 원인", "truncated": False},
        {"date": "d", "tool": "t", "n": 1, "q": "문의: a.b@corp.com 10.1.2.3 서버", "truncated": False},
        {"date": "d", "tool": "t", "n": 1, "q": "abc", "truncated": False},
    ]
    items, stats = qf.prepare_real(rows, known=set())
    assert items[0]["count"] == 5 and items[0]["id"] == "r-001"
    assert stats["too_short"] == 1 and stats["kept"] == 2
    hint_item = next(i for i in items if "corp" in i["q"])
    assert set(hint_item["hints"]) >= {"email", "ip"}


def test_title_overlap_handles_korean_particles():
    assert qf.title_overlap("방화벽이 자꾸 끊깁니다", "방화벽 인터페이스 업/다운") > 0.3
    assert qf.title_overlap("완전히 다른 질문", "방화벽 인터페이스") == 0.0
    assert qf.too_easy("방화벽 인터페이스 업다운", "방화벽 인터페이스 업다운 반복", max_ratio=0.5)
    assert not qf.too_easy("접속이 안 됨", "방화벽 인터페이스 업다운 반복", max_ratio=0.5)


def test_load_known_questions(tmp_path):
    h = tmp_path / "h.json"
    h.write_text(json.dumps({"items": [{"question": "홀드아웃 질문입니다"}]}), encoding="utf-8")
    e = tmp_path / "e.json"
    e.write_text(json.dumps({"cases": [{"q": "평가셋 질의입니다"}]}), encoding="utf-8")
    known = qf.load_known_questions(str(h), [str(e)])
    assert qf.norm("홀드아웃 질문입니다") in known and qf.norm("평가셋 질의입니다") in known
