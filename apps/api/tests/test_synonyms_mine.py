import json

from app.synonyms import mine

TICKETS = [
    mine.Ticket("T-1", "inode2x 경고", "아이노드 고갈", "서버 inode2x 경고 발생. 아이노드 고갈로 파일 생성 불가"),
    mine.Ticket("T-2", "아이노드 고갈 알람", "", "inode2x 사용률 100% 아이노드 고갈 알람"),
    mine.Ticket("T-3", "디스크 경고", "", "디스크 사용률 경고"),
]


def test_build_messages_contains_ids_and_json_contract():
    msgs = mine.build_messages(TICKETS)
    assert msgs[0]["role"] == "user"
    text = msgs[0]["content"]
    assert "[T-1]" in text and "[T-3]" in text
    assert '"groups"' in text


def test_parse_response_handles_fences_and_noise():
    raw = "설명\n```json\n" + json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1"], "reason": "r"}]}) + "\n```"
    assert mine.parse_response(raw) == [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1"], "reason": "r"}]
    assert mine.parse_response("not json") == []
    assert mine.parse_response('{"groups": "x"}') == []


class FakeDF:
    def __init__(self, table):
        self.table = table

    def __call__(self, term):
        return self.table.get(term, 0)


def _params(**kw):
    base = dict(df_min=3, df_max=1500, min_terms_per_ticket=2, existing_keys=set())
    base.update(kw)
    return mine.Params(**base)


def test_process_groups_accepts_verified_group():
    groups = [{"terms": ["inode2x", "아이노드 고갈", "행"], "tickets": ["T-1", "T-2"], "reason": "r"}]
    items, stats = mine.process_groups(
        groups, TICKETS, term_df=FakeDF({"inode2x": 50, "아이노드 고갈": 40}), params=_params()
    )
    assert len(items) == 1
    assert items[0]["terms"] == ["inode2x", "아이노드 고갈"]
    assert items[0]["evidence"]["tickets"] == ["T-1", "T-2"]
    assert items[0]["evidence"]["df"] == {"inode2x": 50, "아이노드 고갈": 40}
    assert stats["accepted"] == 1


def test_process_groups_rejects_by_reason():
    df = FakeDF({"inode2x": 50, "아이노드 고갈": 40})
    groups = [
        {"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1"], "reason": "x"},       # 1 verified ticket
        {"terms": ["inode2x", "없는용어들"], "tickets": ["T-1", "T-2"], "reason": "x"},   # term not in tickets
        {"terms": ["행", "펜싱"], "tickets": ["T-1", "T-2"], "reason": "x"},              # invalid terms
    ]
    items, stats = mine.process_groups(groups, TICKETS, term_df=df, params=_params())
    assert items == []
    assert stats["accepted"] == 0 and stats["rej_evidence"] == 2 and stats["rej_terms"] == 1


def test_process_groups_rejects_term_that_is_too_common():
    groups = [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    items, stats = mine.process_groups(
        groups, TICKETS, term_df=FakeDF({"inode2x": 9999, "아이노드 고갈": 40}), params=_params()
    )
    assert items == [] and stats["rej_df"] == 1


def test_process_groups_skips_existing_key():
    from app.synonyms.rules import terms_key

    groups = [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    p = _params(existing_keys={terms_key(["inode2x", "아이노드 고갈"])})
    items, stats = mine.process_groups(groups, TICKETS, term_df=FakeDF({"inode2x": 5, "아이노드 고갈": 5}), params=p)
    assert items == [] and stats["rej_existing"] == 1


def test_process_groups_skips_static_and_existing():
    texts = [
        mine.Ticket("T-1", "t", "", "kernel panic 발생 커널 패닉 로그"),
        mine.Ticket("T-2", "t", "", "커널 패닉 and kernel panic"),
    ]
    g = [{"terms": ["kernel panic", "커널 패닉"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    items, stats = mine.process_groups(g, texts, term_df=FakeDF({"kernel panic": 9, "커널 패닉": 9}), params=_params())
    assert items == [] and stats["rej_static"] == 1


def test_mine_batch_uses_injected_chat_and_survives_llm_error():
    def ok_chat(messages, **kw):
        return json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "r"}]})

    def bad_chat(messages, **kw):
        raise RuntimeError("fabrix down")

    df = FakeDF({"inode2x": 50, "아이노드 고갈": 40})
    items, stats = mine.mine_batch(TICKETS, chat=ok_chat, term_df=df, params=_params())
    assert stats["accepted"] == 1 and stats["llm_error"] == 0
    items, stats = mine.mine_batch(TICKETS, chat=bad_chat, term_df=df, params=_params())
    assert items == [] and stats["llm_error"] == 1


def test_parse_response_never_raises_on_malformed_shapes():
    assert mine.parse_response("") == []
    assert mine.parse_response(None) == []  # type: ignore[arg-type]
    assert mine.parse_response("[1, 2]") == []
    assert mine.parse_response('{"groups": [1, "x", {"terms": "a"}, {"tickets": ["T-1"]}]}') == []
    pre = 'before {"groups": [{"terms": ["abc", "def"]}]} after'
    assert mine.parse_response(pre) == [{"terms": ["abc", "def"], "tickets": [], "reason": ""}]


def test_build_messages_title_with_brackets_and_newlines_keeps_line_format():
    t = mine.Ticket("T-9", "제목 ]\n[T-X] 위조", "증상\n둘째줄", "x")
    body = mine.build_messages([t])[0]["content"].split("티켓 목록:\n", 1)[1]
    assert len(body.splitlines()) == 1
    assert body.startswith("[T-9] ")
