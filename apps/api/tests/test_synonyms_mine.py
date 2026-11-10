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


def _t(i, text):
    return mine.Ticket(i, "t", "", text)


def test_df_trim_that_breaks_two_ticket_invariant_is_rejected():
    tix = [_t("T-1", "alpha1x bravo2y 발생"), _t("T-2", "alpha1x gamma3z 발생")]
    g = [{"terms": ["alpha1x", "bravo2y", "gamma3z"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    df = FakeDF({"alpha1x": 9999, "bravo2y": 10, "gamma3z": 10})
    items, stats = mine.process_groups(g, tix, term_df=df, params=_params())
    assert items == [] and stats["rej_df"] == 1 and stats["accepted"] == 0


def test_term_df_called_once_per_distinct_term():
    calls = []

    def df(term):
        calls.append(term)
        return 10

    g = [
        {"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "x"},
        {"terms": ["inode2x", "아이노드 고갈", "디스크 경고"], "tickets": ["T-1", "T-2", "T-3"], "reason": "x"},
    ]
    mine.process_groups(g, TICKETS, term_df=df, params=_params())
    assert len(calls) == len(set(calls))


def test_mine_batch_isolates_term_df_errors():
    def chat(messages, **kw):
        return json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "r"}]})

    def boom(term):
        raise RuntimeError("db down")

    items, stats = mine.mine_batch(TICKETS, chat=chat, term_df=boom, params=_params())
    assert items == [] and stats["proc_error"] == 1


def test_mine_batch_counts_parse_fail():
    df = FakeDF({})
    _, s1 = mine.mine_batch(TICKETS, chat=lambda m, **kw: "죄송합니다 모르겠어요", term_df=df, params=_params())
    _, s2 = mine.mine_batch(TICKETS, chat=lambda m, **kw: '{"groups": []}', term_df=df, params=_params())
    assert s1["parse_fail"] == 1 and s2["parse_fail"] == 0


def test_duplicate_groups_in_one_response_second_is_existing():
    g = [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "x"}] * 2
    items, stats = mine.process_groups(g, TICKETS, term_df=FakeDF({"inode2x": 5, "아이노드 고갈": 5}), params=_params())
    assert len(items) == 1 and stats["rej_existing"] == 1


def test_partial_overlap_with_static_group_is_accepted():
    tix = [_t("T-1", "kernel panic 커널 패닉 kpanic77x"), _t("T-2", "kernel panic 커널 패닉 kpanic77x")]
    g = [{"terms": ["kernel panic", "커널 패닉", "kpanic77x"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    df = FakeDF({"kernel panic": 9, "커널 패닉": 9, "kpanic77x": 9})
    items, stats = mine.process_groups(g, tix, term_df=df, params=_params())
    assert len(items) == 1 and stats["rej_static"] == 0


def test_parse_response_input_hygiene():
    raw = json.dumps({"groups": [{"terms": ["abc", None, 5, "def"], "tickets": ["[T-1]", " T-2 ", 3], "reason": None}]})
    assert mine.parse_response(raw) == [{"terms": ["abc", "def"], "tickets": ["T-1", "T-2", "3"], "reason": ""}]
    many = json.dumps({"groups": [{"terms": [f"term{i}" for i in range(30)], "tickets": [f"T-{i}" for i in range(50)]}]})
    out = mine.parse_response(many)[0]
    assert len(out["terms"]) == 12 and len(out["tickets"]) == 20


def test_bracketed_ids_verify_end_to_end():
    g = mine.parse_response(
        json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["[T-1]", "[T-2]"], "reason": "r"}]})
    )
    items, _ = mine.process_groups(g, TICKETS, term_df=FakeDF({"inode2x": 5, "아이노드 고갈": 5}), params=_params())
    assert len(items) == 1


def test_parse_created_year_filter():
    from datetime import date

    assert mine.created_on_or_after({"Created": "2025-03-01 10:00"}, date(2025, 1, 1))
    assert not mine.created_on_or_after({"Created": "2023-03-01"}, date(2025, 1, 1))
    assert mine.created_on_or_after({}, date(2025, 1, 1))  # unknown date -> include


def _run_kw(tmp_path, chat, n=5):
    tickets = [mine.Ticket(f"T-{i}", f"제목 {i}", "", f"OOM 메모리 부족 {i}") for i in range(n)]
    return dict(
        tickets=tickets, batch_size=2, chat=chat, term_df=lambda t: 10,
        params=mine.Params(), run_id="r1", model="m",
        out_path=str(tmp_path / "res.json"), max_batches=None,
    )


def test_run_mine_checkpoint_resume(tmp_path):
    from app.synonyms import cli

    calls = []

    def chat(messages, **kw):
        calls.append(1)
        return json.dumps({"groups": []})

    kw = _run_kw(tmp_path, chat)
    res = cli.run_mine(**kw)
    assert len(calls) == 3 and res["stats"]["batches_done"] == 3
    calls.clear()
    res2 = cli.run_mine(**kw)  # same out/run_id -> everything already checkpointed
    assert calls == [] and res2["stats"]["batches_done"] == 3
    assert json.loads((tmp_path / "res.json").read_text())["run_id"] == "r1"


def test_run_mine_resumes_after_interruption(tmp_path):
    from app.synonyms import cli

    calls = []

    def chat(messages, **kw):
        calls.append(messages[0]["content"])
        return json.dumps({"groups": []})

    kw = _run_kw(tmp_path, chat)
    cli.run_mine(**{**kw, "max_batches": 1})  # "interrupted" after batch 0
    assert len(calls) == 1
    calls.clear()
    res = cli.run_mine(**kw)
    assert len(calls) == 2 and res["stats"]["batches_done"] == 3
    assert not any("[T-0]" in c for c in calls)  # batch 0 skipped


def test_run_mine_ignores_truncated_progress_line(tmp_path):
    from app.synonyms import cli

    calls = []

    def chat(messages, **kw):
        calls.append(1)
        return json.dumps({"groups": []})

    kw = _run_kw(tmp_path, chat)
    cli.run_mine(**{**kw, "max_batches": 2})
    with open(kw["out_path"] + ".progress.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"run_id": "r1", "batch": 2, "ite')  # killed mid-write
    calls.clear()
    res = cli.run_mine(**kw)
    assert len(calls) == 1 and res["stats"]["batches_done"] == 3


def test_run_mine_retries_failed_batches_on_resume(tmp_path):
    from app.synonyms import cli

    state = {"fail": True, "n": 0}

    def chat(messages, **kw):
        state["n"] += 1
        if state["fail"]:
            raise RuntimeError("boom")
        return json.dumps({"groups": []})

    kw = _run_kw(tmp_path, chat, n=2)
    res = cli.run_mine(**kw)
    assert res["stats"]["llm_error"] == 1 and res["stats"]["batches_done"] == 0
    state["fail"] = False
    res2 = cli.run_mine(**kw)
    assert state["n"] == 2 and res2["stats"]["batches_done"] == 1


def test_cli_import_missing_items(tmp_path, capsys):
    from app.synonyms import cli

    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"run_id": "x"}))
    assert cli.main(["import", "--file", str(f)]) == 2
    assert "items" in capsys.readouterr().err
    f.write_text("not json")
    assert cli.main(["import", "--file", str(f)]) == 2


def test_cli_mine_help_exits_zero(capsys):
    import pytest

    from app.synonyms import cli

    with pytest.raises(SystemExit) as e:
        cli.main(["mine", "--help"])
    assert e.value.code == 0
    assert "--commit" in capsys.readouterr().out


def test_evidence_records_cooccurrence_and_min1_accepts_separate_tickets():
    ts = [mine.Ticket("S-1", "inode2x 발생", "", "클러스터 inode2x 발생"),
          mine.Ticket("S-2", "아이노드 지연", "", "DB 아이노드 지연"),
          mine.Ticket("S-3", "inode2x 와 아이노드", "", "inode2x 아이노드 둘 다 언급")]
    g = [{"terms": ["inode2x", "아이노드"], "tickets": ["S-1", "S-2"], "reason": "r"}]
    df = FakeDF({"inode2x": 70, "아이노드": 20})
    items, stats = mine.process_groups(g, ts, term_df=df, params=_params(min_terms_per_ticket=2))
    assert items == [] and stats["rej_evidence"] == 1  # never together -> strict rule rejects a true synonym pair
    items, _ = mine.process_groups(g, ts, term_df=df, params=_params(min_terms_per_ticket=1))
    assert items[0]["evidence"]["cooccur_tickets"] == 0
    g[0]["tickets"] = ["S-1", "S-2", "S-3"]
    items, _ = mine.process_groups(g, ts, term_df=df, params=_params(min_terms_per_ticket=1))
    assert items[0]["evidence"]["cooccur_tickets"] == 1


def test_parse_salvages_complete_groups_from_cut_off_json():
    raw = ('```json\n{\n "groups": [\n  {"terms": ["접속 지연", "접속지연"], "tickets": ["T-1", "T-2"], "reason": "r"},\n'
           '  {"terms": ["메모리 부족", "OO')
    got = mine._parse(raw)
    assert got and got[0]["terms"] == ["접속 지연", "접속지연"] and got[0]["tickets"] == ["T-1", "T-2"]
    assert len(got) == 1
    assert mine._parse('```json\n{"groups": [ {"terms": ["a') is None


def test_process_groups_reports_rejected_groups_with_reason_and_df():
    df = FakeDF({"inode2x": 9999, "아이노드 고갈": 40})
    groups = [
        {"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "same thing"},  # df too high
        {"terms": ["행", "펜싱"], "tickets": ["T-1", "T-2"], "reason": "x"},                         # invalid terms
    ]
    rej: list = []
    mine.process_groups(groups, TICKETS, term_df=df, params=_params(), rejected=rej)
    by = {r["reason"]: r for r in rej}
    assert by["df"]["terms"] == ["inode2x", "아이노드 고갈"] and by["df"]["df"]["inode2x"] == 9999
    assert by["df"]["llm_reason"] == "same thing"
    assert by["terms"]["terms"] == ["행", "펜싱"]
