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
