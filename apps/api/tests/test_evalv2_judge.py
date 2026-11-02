import json

import pytest

from app.evalv2 import judge

BODY_A = "서버 재기동 후 Out of Memory killer 가 동작했고 메모리 부족 알람이 발생했다."
BODY_B = "디스크 사용률 경고 임계치를 조정했다."


def _cands():
    return [
        {"external_id": "DOC-A", "title": "OOM 장애", "excerpt": BODY_A},
        {"external_id": "DOC-B", "title": "디스크 경고", "excerpt": BODY_B},
    ]


def test_build_messages_lists_candidates_with_short_ids():
    msgs = judge.build_messages("메모리 부족 원인", _cands())
    text = msgs[0]["content"]
    assert "[c1]" in text and "[c2]" in text and "메모리 부족 원인" in text and '"judgments"' in text


def test_verify_evidence_normalises_whitespace_and_needs_length():
    assert judge.verify_evidence("Out of Memory  killer", BODY_A)
    assert not judge.verify_evidence("없는 문장입니다 정말로", BODY_A)
    assert not judge.verify_evidence("짧음", "짧음 짧음")


def test_judge_candidates_accepts_verified_and_rejects_fabricated_evidence():
    def chat(messages, **kw):
        return json.dumps({"judgments": [
            {"id": "c1", "grade": 2, "evidence": "Out of Memory killer 가 동작했고"},
            {"id": "c2", "grade": 2, "evidence": "본문에 전혀 없는 지어낸 문장입니다"},
        ]})

    out = judge.judge_candidates(chat, "q", _cands(), {"DOC-A": BODY_A, "DOC-B": BODY_B})
    assert out["DOC-A"] == {"grade": 2, "evidence": "Out of Memory killer 가 동작했고", "status": "ok"}
    assert out["DOC-B"]["grade"] is None and out["DOC-B"]["status"] == "unverified"


def test_grade_zero_needs_no_evidence_and_invalid_grade_is_none():
    def chat(messages, **kw):
        return json.dumps({"judgments": [{"id": "c1", "grade": 0}, {"id": "c2", "grade": 7}]})

    out = judge.judge_candidates(chat, "q", _cands(), {"DOC-A": BODY_A, "DOC-B": BODY_B})
    assert out["DOC-A"]["grade"] == 0 and out["DOC-A"]["status"] == "ok"
    assert out["DOC-B"]["grade"] is None and out["DOC-B"]["status"] == "invalid"


def test_empty_response_is_retried_once_then_marked_empty():
    calls = {"n": 0}

    def chat(messages, **kw):
        calls["n"] += 1
        return ""

    out = judge.judge_candidates(chat, "q", _cands(), {}, sleep=lambda s: None)
    assert calls["n"] == 2
    assert all(v["status"] == "empty" and v["grade"] is None for v in out.values())


def test_missing_candidate_in_answer_and_chunking():
    seen = []

    def chat(messages, **kw):
        seen.append(messages[0]["content"])
        return json.dumps({"judgments": []})

    many = [{"external_id": f"D{i}", "title": "t", "excerpt": "x"} for i in range(10)]
    out = judge.judge_candidates(chat, "q", many, {}, chunk_size=8, sleep=lambda s: None)
    assert len(seen) == 2  # 8 + 2
    assert all(v["status"] == "missing" for v in out.values())


def test_chat_exception_propagates_not_swallowed():
    # Design: LLM transport/auth errors are NOT swallowed here; the CLI decides retry/skip policy.
    def chat(messages, **kw):
        raise RuntimeError("llm down")

    with pytest.raises(RuntimeError, match="llm down"):
        judge.judge_candidates(chat, "q", _cands(), {}, sleep=lambda s: None)
