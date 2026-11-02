import json

from app.evalv2 import synth


DOC = {"external_id": "SEED-1", "title": "방화벽 인터페이스 업다운 반복으로 접속 불가", "excerpt": "인터페이스 flapping 으로 세션 단절"}


def test_build_messages_per_slice_forbids_title_words():
    for sl in ("vocab", "pointer", "symptom"):
        text = synth.build_messages(sl, DOC, n_queries=2)[0]["content"]
        assert DOC["title"] in text and "그대로 쓰지" in text and '"queries"' in text
    try:
        synth.build_messages("nope", DOC)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown slice must raise")


def test_parse_queries_is_tolerant():
    assert synth.parse_queries('```json\n{"queries": ["a b c d e", "  f g h i j "]}\n```') == ["a b c d e", "f g h i j"]
    assert synth.parse_queries("x") == []
    assert synth.parse_queries(json.dumps({"queries": [1, None, "valid query here"]})) == ["valid query here"]


def test_accept_queries_filters_easy_short_duplicates_and_known():
    known = {"이미 있는 질의입니다 정말"}
    qs = [
        "방화벽 인터페이스 업다운 반복",        # too easy (all title words)
        "망 장비 포트가 계속 끊김",             # ok
        "망 장비 포트가 계속 끊김",             # duplicate
        "짧음",                                 # too short
        "이미 있는 질의입니다 정말",            # known
    ]
    ok = synth.accept_queries(qs, DOC["title"], known={synth.qf.norm(k) for k in known}, seen=set())
    assert ok == ["망 장비 포트가 계속 끊김"]


def test_generate_builds_items_with_seed_doc_and_ids():
    def chat(messages, **kw):
        return json.dumps({"queries": ["망 장비 포트가 계속 끊김", "세션이 자꾸 단절되는 현상"]})

    items = synth.generate(chat, "symptom", [DOC], known=set(), id_prefix="s-symptom", max_per_doc=2)
    assert [i["id"] for i in items] == ["s-symptom-001", "s-symptom-002"]
    assert items[0]["slice"] == "symptom" and items[0]["source"] == "synthetic"
    assert items[0]["seed_doc"]["external_id"] == "SEED-1"


def test_generate_skips_docs_when_llm_fails_or_is_empty():
    def boom(messages, **kw):
        raise RuntimeError("x")

    assert synth.generate(boom, "vocab", [DOC], known=set(), id_prefix="s") == []
    assert synth.generate(lambda m, **k: "", "vocab", [DOC], known=set(), id_prefix="s") == []


def test_id_prefix_is_seed_dependent_and_stable():
    assert synth.id_prefix("vocab", "a") == synth.id_prefix("vocab", "a")
    assert synth.id_prefix("vocab", "a") != synth.id_prefix("vocab", "b")
    p = synth.id_prefix("pointer", "a")
    assert p.startswith("s-pointer-") and len(p) == len("s-pointer-") + 4


def test_vocab_prompt_asks_for_opposite_language_and_has_examples():
    text = synth.build_messages("vocab", DOC)[0]["content"]
    assert "영문" in text and "약어" in text and "나쁜 예" in text and "좋은 예" in text
    for sl in ("pointer", "symptom"):
        t = synth.build_messages(sl, DOC)[0]["content"]
        assert "나쁜 예" in t and "좋은 예" in t and "발췌" in t


def test_accept_queries_rejects_excerpt_copies():
    title = "방화벽 정책 점검 절차"
    excerpt = "세션 테이블 임계치 초과 시 신규 연결 거부 현상 확인 후 조치"
    copy = "세션 테이블 임계치 초과 시 신규 연결 거부"
    fresh = "새 연결이 갑자기 안 붙는 문제"
    assert synth.accept_queries([copy, fresh], title, known=set(), seen=set(), excerpt=excerpt) == [fresh]
    assert synth.accept_queries([copy], title, known=set(), seen=set()) == [copy]  # no excerpt -> no check


def test_generate_stops_after_want_docs_docs_yield_queries():
    docs = [dict(DOC, external_id=f"D{i}") for i in range(5)]
    calls = []

    def chat(messages, **kw):
        calls.append(1)
        return json.dumps({"queries": [f"망 장비 포트 증상 {len(calls)} 번째"]})

    items = synth.generate(chat, "symptom", docs, known=set(), id_prefix="s", max_per_doc=1, want_docs=2)
    assert len(items) == 2 and len(calls) == 2
    # docs whose LLM output is rejected do not count toward want_docs
    n = []

    def flaky(messages, **kw):
        n.append(1)
        return "" if len(n) == 1 else json.dumps({"queries": [f"세션이 끊기는 증상 {len(n)}"]})

    items = synth.generate(flaky, "symptom", docs, known=set(), id_prefix="s", max_per_doc=1, want_docs=2)
    assert len(items) == 2 and len(n) == 3
