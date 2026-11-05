import json

from app.decisions import refine
from app.decisions.scan import Proposal


def _p():
    return Proposal(
        detector="deadline", source_key="deadline:x", question="'PIXEL 제공'의 기한은 제목대로 2026.11.30입니까?",
        options=[{"key": "as_titled", "label": "2026.11.30", "summary": "제목 기한이 현재 기준"},
                 {"key": "changed", "label": "연장·변경됨", "summary": "기한이 달라졌다"}],
        evidence=[{"quote": "PIXEL 제공 2026.11.30 까지 제공", "stance": "supports", "option_key": "as_titled"}])


def _good():
    return {"question": "PIXEL 제공 기한은 2026.11.30이 현재 기준입니까?",
            "options": [{"key": "as_titled", "label": "2026.11.30", "summary": "제목에 적힌 기한이 맞다"},
                        {"key": "changed", "label": "변경됨", "summary": "기한이 연장·변경되었다"}]}


def _run(data):
    p = _p()
    ok = refine.refine(p, complete=lambda *a, **k: data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
    return p, ok


def test_adopts_valid_rewording_and_keeps_evidence_and_rule_text():
    p, ok = _run(_good())
    assert ok and p.question.startswith("PIXEL 제공 기한은")
    assert [o["key"] for o in p.options] == ["as_titled", "changed"]
    assert p.evidence[0]["quote"] == "PIXEL 제공 2026.11.30 까지 제공"
    assert p.source_ref["llm_refined"] and p.source_ref["rule_question"].startswith("'PIXEL 제공'")


def test_rejects_changed_date():
    d = _good(); d["question"] = d["question"].replace("2026.11.30", "2026.12.31")
    p, ok = _run(d)
    assert not ok and p.question.startswith("'PIXEL 제공'") and "llm_refined" not in p.source_ref


def test_rejects_changed_option_keys_or_count():
    d = _good(); d["options"][1]["key"] = "other_key"
    assert not _run(d)[1]
    d = _good(); d["options"].append({"key": "extra", "label": "추가", "summary": "x"})
    assert not _run(d)[1]


def test_rejects_changed_option_label_date():
    d = _good(); d["options"][0]["label"] = "2026.12.01"
    assert not _run(d)[1]


def test_garbage_and_llm_failure_fall_back_to_rules():
    assert not _run("not json")[1]

    def boom(*a, **k):
        raise RuntimeError("HTTP 402")
    p = _p()
    assert refine.refine(p, complete=boom) is False and p.question.startswith("'PIXEL 제공'")


def test_parses_fenced_json():
    p, ok = _run("```json\n" + json.dumps(_good(), ensure_ascii=False) + "\n```")
    assert ok


def test_preview_has_no_side_effects_and_reports_reason():
    p = _p()
    before = (p.question, list(p.options), dict(p.source_ref))
    ok = refine.preview(p, complete=lambda *a, **k: json.dumps(_good(), ensure_ascii=False))
    assert ok["adopted"] and ok["llm"]["question"].startswith("PIXEL 제공 기한은") and ok["rule"]["question"] == before[0]
    bad = _good(); bad["question"] = bad["question"].replace("2026.11.30", "2027.01.01")
    no = refine.preview(p, complete=lambda *a, **k: json.dumps(bad, ensure_ascii=False))
    assert not no["adopted"] and "숫자" in no["reason"] and "llm" not in no
    assert refine.preview(p, complete=lambda *a, **k: "nope")["reason"].startswith("LLM 응답")
    assert (p.question, p.options, p.source_ref) == before


def test_preview_reports_llm_failure():
    def boom(*a, **k):
        raise RuntimeError("HTTP 402")
    assert "402" in refine.preview(_p(), complete=boom)["reason"]
