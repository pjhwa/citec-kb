from app.docexp import gen


def test_build_messages_fences_body_and_truncates():
    body = "가" * 5000
    msgs = gen.build_messages("제목", body)
    user = msgs[-1]["content"]
    assert "<doc>" in user and "</doc>" in user
    assert user.count("가") == gen.BODY_CHARS
    assert "지시" in msgs[0]["content"]  # 본문 속 지시는 따르지 않는다는 문구


def test_parse_response_ok_and_garbage():
    assert gen.parse_response('{"summary":"요약","keywords":["a b","c"]}') == {"summary": "요약", "keywords": ["a b", "c"]}
    assert gen.parse_response("not json") is None
    assert gen.parse_response('{"summary":1,"keywords":"x"}') is None
    assert gen.parse_response("") is None
    assert gen.parse_response(None) is None


def test_validate_grounded_split_and_filters():
    body = "OOM killer 가 동작해서 메모리 부족으로 프로세스가 종료됨"
    parsed = {"summary": "메모리 부족 종료 사례", "keywords": ["OOM", "메모리 부족", "서버 다운", "행", "x"]}
    rec = gen.validate(parsed, body)
    assert rec["grounded"] == ["OOM", "메모리 부족"]          # 본문 근거 있음
    assert "서버 다운" in rec["keywords"]                     # all 모드용으로 보관
    assert "행" not in rec["keywords"] and "x" not in rec["keywords"]  # 1~2자 금지


def test_validate_dedupes_case_insensitively():
    rec = gen.validate({"summary": "s", "keywords": ["OOM", "oom", "메모리 부족", "커널 로그"]}, "")
    assert rec["keywords"] == ["OOM", "메모리 부족", "커널 로그"]
    assert rec["grounded"] == []  # 근거 없어도 keywords 3개 이상이면 보관


def test_validate_discards_when_too_few_keywords():
    # "하나"는 한글 2자라 무효 -> 유효 키워드 1개 (<3)
    assert gen.validate({"summary": "s", "keywords": ["하나", "OOM"]}, "본문 OOM") is None


def test_validate_caps_lengths():
    rec = gen.validate({"summary": "가" * 900, "keywords": [f"키워드{i}번" for i in range(30)]}, "키워드0번 " * 3)
    assert len(rec["summary"]) <= gen.SUMMARY_MAX and len(rec["keywords"]) <= gen.KEYWORDS_MAX


def test_expand_one_empty_retry_then_ok():
    calls = []

    def chat(messages, **kw):
        calls.append(1)
        return "" if len(calls) < 3 else '{"summary":"요약","keywords":["OOM","메모리 부족","커널 로그"]}'

    doc = {"title": "t", "body_md": "OOM 메모리 부족 커널 로그"}
    rec, st = gen.expand_one(doc, chat=chat, sleep=lambda s: None)
    assert rec and st["ok"] == 1 and st["empty_retried"] == 2 and st["empty_recovered"] == 1


def test_expand_one_llm_error_and_parse_fail():
    def boom(messages, **kw):
        raise RuntimeError("x")

    assert gen.expand_one({"title": "t", "body_md": "b"}, chat=boom)[1]["llm_error"] == 1
    assert gen.expand_one({"title": "t", "body_md": "b"}, chat=lambda m, **k: "nope")[1]["parse_fail"] == 1


def test_expand_one_discarded():
    raw = '{"summary":"요약","keywords":["OOM"]}'
    rec, st = gen.expand_one({"title": "t", "body_md": "b"}, chat=lambda m, **k: raw)
    assert rec is None and st["discarded"] == 1 and st["ok"] == 0


def test_expand_one_empty_exhausted():
    rec, st = gen.expand_one({"title": "t", "body_md": "b"}, chat=lambda m, **k: "", empty_retries=1, sleep=lambda s: None)
    assert rec is None and st["empty_response"] == 1
