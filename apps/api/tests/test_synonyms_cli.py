import contextlib
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.synonyms import cli, mine, store

T = lambda i: mine.Ticket(f"T-{i}", f"제목 {i}", "", f"inode2x 경고 아이노드 고갈 {i}")  # noqa: E731


def _kw(tmp_path, chat, tickets, **over):
    kw = dict(
        tickets=tickets, batch_size=2, chat=chat, term_df=lambda t: 10,
        params=mine.Params(), run_id="r1", model="m",
        out_path=str(tmp_path / "res.json"), max_batches=None,
    )
    kw.update(over)
    return kw


def _empty_chat(calls):
    def chat(messages, **kw):
        calls.append(messages[0]["content"])
        return json.dumps({"groups": []})

    return chat


def test_resume_redoes_batches_when_ticket_set_shifts(tmp_path):
    calls = []
    base = [T(i) for i in range(1, 5)]
    cli.run_mine(**_kw(tmp_path, _empty_chat(calls), base))
    assert len(calls) == 2
    calls.clear()
    cli.run_mine(**_kw(tmp_path, _empty_chat(calls), base))
    assert calls == []  # unchanged ids -> nothing re-called
    shifted = [T(0)] + base
    res = cli.run_mine(**_kw(tmp_path, _empty_chat(calls), shifted))
    assert len(calls) == 3 and res["stats"]["batches_done"] == 3
    calls.clear()
    cli.run_mine(**_kw(tmp_path, _empty_chat(calls), shifted))
    assert calls == []  # the new records superseded the stale ones


def _group_chat(tids):
    def chat(messages, **kw):
        return json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": tids, "reason": "r"}]})

    return chat


def test_same_group_in_two_batches_merged_by_terms_key(tmp_path):
    tickets = [T(i) for i in range(4)]
    seq = iter([["T-0", "T-1"], ["T-2", "T-3"]])

    def chat(messages, **kw):
        return json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": next(seq), "reason": "r"}]})

    res = cli.run_mine(**_kw(tmp_path, chat, tickets))
    assert len(res["items"]) == 1
    assert sorted(res["items"][0]["evidence"]["tickets"]) == ["T-0", "T-1", "T-2", "T-3"]


def test_failed_batch_writes_no_progress_line(tmp_path):
    def chat(messages, **kw):
        raise RuntimeError("down")

    kw = _kw(tmp_path, chat, [T(0), T(1)])
    res = cli.run_mine(**kw)
    assert res["stats"]["llm_error"] == 1
    import os

    prog = kw["out_path"] + ".progress.jsonl"
    assert not os.path.exists(prog) or open(prog).read() == ""


# ---- main() ----------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    import app.llm_chat as llm_chat
    import app.settings as settings

    state = SimpleNamespace(upserts=[], chat=lambda m, **kw: json.dumps({"groups": []}), backend="openrouter")
    monkeypatch.setattr(settings, "get_settings", lambda: SimpleNamespace(
        llm_backend=state.backend, company_model_id="cm", openrouter_model_id="om"))
    monkeypatch.setattr(llm_chat, "chat_complete", lambda m, **kw: state.chat(m, **kw))
    monkeypatch.setattr(mine, "load_tickets", lambda **kw: [T(0), T(1), T(2)])
    monkeypatch.setattr(mine, "make_term_df", lambda: (lambda t: 10))
    monkeypatch.setattr(store, "existing_keys", lambda: set())

    def up(items, run_id, model):
        state.upserts.append(items)
        return {"inserted": len(items)}

    monkeypatch.setattr(store, "upsert_candidates", up)
    state.out = str(tmp_path / "o" / "res.json")
    return state


def _one_json(capsys):
    out = capsys.readouterr()
    lines = [x for x in out.out.splitlines() if x.strip()]
    assert len(lines) == 1
    return json.loads(lines[0]), out.err


def test_dry_run_does_not_upsert_and_stdout_single_json(env, capsys):
    env.chat = _group_chat(["T-0", "T-1"])
    assert cli.main(["mine", "--out", env.out, "--batch-size", "2"]) == 0
    data, err = _one_json(capsys)
    assert env.upserts == [] and data["commit"] is None and "run_id=" in err
    assert data["run_id"] and data["stats"]["accepted"] >= 1


def test_commit_upserts(env, capsys):
    env.chat = _group_chat(["T-0", "T-1"])
    assert cli.main(["mine", "--out", env.out, "--commit"]) == 0
    data, _ = _one_json(capsys)
    assert len(env.upserts) == 1 and data["commit"] == {"inserted": 1}


def test_default_run_id_is_deterministic_per_out(env, capsys):
    cli.main(["mine", "--out", env.out])
    a, _ = _one_json(capsys)
    cli.main(["mine", "--out", env.out])
    b, _ = _one_json(capsys)
    assert a["run_id"] == b["run_id"]


def test_partial_run_exit_3_and_commit_gated(env, capsys):
    env.chat = lambda m, **kw: "not json at all"
    assert cli.main(["mine", "--out", env.out, "--commit"]) == 3
    data, err = _one_json(capsys)
    assert env.upserts == [] and data["commit"] is None and "partial" in err
    env.chat = _group_chat(["T-0", "T-1"])
    # one parse_fail batch + one good batch, allow-partial commits
    calls = iter(["garbage", json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-0", "T-1"]}]})])
    env.chat = lambda m, **kw: next(calls)
    assert cli.main(["mine", "--out", env.out + "2", "--batch-size", "2", "--commit", "--allow-partial"]) == 3
    data, _ = _one_json(capsys)
    assert len(env.upserts) == 1 and data["commit"] is not None


def test_max_batches_warns(env, capsys):
    assert cli.main(["mine", "--out", env.out, "--batch-size", "1", "--max-batches", "1"]) == 0
    _, err = _one_json(capsys)
    assert "--max-batches" in err


def test_backend_none_returns_2(env, capsys):
    env.backend = "none"
    assert cli.main(["mine", "--out", env.out]) == 2


def test_unwritable_out_returns_2_before_llm(env, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    called = []
    env.chat = lambda m, **kw: called.append(1) or "{}"
    assert cli.main(["mine", "--out", str(blocker / "sub" / "res.json")]) == 2
    assert called == [] and "cannot write" in capsys.readouterr().err


def test_df_max_at_cap_rejected(env):
    with pytest.raises(SystemExit) as e:
        cli.main(["mine", "--out", env.out, "--df-max", str(mine.DF_COUNT_CAP)])
    assert e.value.code == 2


def test_load_tickets_date_window(monkeypatch):
    import app.db.session as sess

    old = (date.today() - timedelta(days=900)).isoformat()
    new = (date.today() - timedelta(days=10)).isoformat()
    rows = [
        ("A", "old", "b", {"Created": old}, None),
        ("B", "new", "body", {"Created": new}, "sym"),
        ("C", "nodate", "body", {}, None),
        ("D", "  ", "b", {"Created": new}, None),
    ]

    class FakeSession:
        def execute(self, q):
            return SimpleNamespace(all=lambda: rows)

    @contextlib.contextmanager
    def fake_scope():
        yield FakeSession()

    monkeypatch.setattr(sess, "session_scope", fake_scope)
    got = mine.load_tickets(since_years=1)
    assert [t.external_id for t in got] == ["B", "C"]
    assert got[0].symptom == "sym" and got[1].symptom == "body"
    assert [t.external_id for t in mine.load_tickets(since_years=1, limit=1)] == ["B"]


# ---- empty / filter-blocked responses -----------------------------------------

_GOOD = json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-0", "T-1"]}]})


def test_empty_response_stat_and_not_checkpointed(tmp_path):
    kw = _kw(tmp_path, lambda m, **k: "  \n", [T(0), T(1)], sleep=lambda s: None)
    res = cli.run_mine(**kw)
    assert res["stats"]["empty_response"] == 1 and res["stats"]["batches_done"] == 0
    assert res["items"] == [] and len(res["empty_samples"]) == 1
    import os

    prog = kw["out_path"] + ".progress.jsonl"
    assert not os.path.exists(prog) or open(prog).read() == ""


def test_mine_batch_empty_is_not_parse_success():
    items, st = mine.mine_batch([T(0), T(1)], chat=lambda m, **k: "", term_df=lambda t: 10, params=mine.Params(), sleep=lambda s: None)
    assert items == [] and st["empty_response"] == 1 and st["parse_fail"] == 0 and st["empty_retried"] == 3


def test_main_empty_exit_3_warning_and_commit_skipped(env, capsys):
    env.chat = lambda m, **kw: ""
    assert cli.main(["mine", "--out", env.out, "--commit", "--empty-retries", "0"]) == 3
    data, err = _one_json(capsys)
    assert "LLM returned empty content (possibly a content-filter block) — results are incomplete" in err
    assert env.upserts == [] and data["commit"] is None and data["stats"]["empty_response"] >= 1
    assert "empty_samples" in data


def _marker_chat(calls):
    def chat(messages, **kw):
        txt = messages[0]["content"]
        calls.append(txt)
        if "BADMARK" in txt:
            return ""
        ids = [f"T-{i}" for i in range(40) if f"[T-{i}]" in txt]
        return json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ids[:2]}]})

    return chat


def test_split_on_empty_isolates_bad_ticket(tmp_path):
    ts = [T(i) for i in range(40)]
    ts[13] = mine.Ticket("T-13", "BADMARK", "", "inode2x 아이노드 고갈 BADMARK")
    calls = []
    res = cli.run_mine(**_kw(tmp_path, _marker_chat(calls), ts, batch_size=40, split_on_empty=True, empty_retries=0))
    assert len(res["blocked_chunks"]) == 1
    chunk = res["blocked_chunks"][0]
    assert "T-13" in chunk and 5 <= len(chunk) <= 10
    assert all(isinstance(x, str) and x.startswith("T-") for x in chunk)
    assert len(res["items"]) == 1  # other tickets' groups still mined
    assert res["stats"]["empty_response"] == 1 and res["extra_calls"] == len(calls) - 1 >= 4


def test_split_default_off_makes_no_extra_calls(tmp_path):
    ts = [T(i) for i in range(40)]
    ts[13] = mine.Ticket("T-13", "BADMARK", "", "x")
    calls = []
    res = cli.run_mine(**_kw(tmp_path, _marker_chat(calls), ts, batch_size=40, empty_retries=0))
    assert len(calls) == 1 and "blocked_chunks" not in res


def test_main_split_flag_prints_extra_calls(env, capsys):
    env.chat = lambda m, **kw: ""
    monkey_tickets = [T(i) for i in range(12)]
    mine.load_tickets = lambda **kw: monkey_tickets  # env fixture's monkeypatch restores the original
    assert cli.main(["mine", "--out", env.out, "--batch-size", "12", "--split-on-empty", "--empty-retries", "0"]) == 3
    data, err = _one_json(capsys)
    assert "extra (billable) LLM calls" in err and data["blocked_chunks"] and data["extra_calls"] == 2


# ---- retry on empty -------------------------------------------------------------


def _seq_chat(seq, calls):
    it = iter(seq)

    def chat(messages, **kw):
        calls.append(1)
        return next(it)

    return chat


def _mb(chat, **kw):
    delays = []
    kw.setdefault("sleep", delays.append)
    kw.setdefault("jitter", lambda: 0.5)
    items, st = mine.mine_batch([T(0), T(1)], chat=chat, term_df=lambda t: 10, params=mine.Params(), **kw)
    return items, st, delays


def test_retry_recovers_after_two_empties():
    calls = []
    items, st, delays = _mb(_seq_chat(["", " ", _GOOD], calls))
    assert len(calls) == 3 and len(items) == 1
    assert st["empty_retried"] == 2 and st["empty_recovered"] == 1 and st["empty_response"] == 0
    assert delays == [2.5, 4.5] and delays == sorted(delays)


def test_always_empty_makes_exactly_1_plus_retries_calls():
    calls = []
    items, st, delays = _mb(lambda m, **k: calls.append(1) or "")
    assert len(calls) == 4 and items == []
    assert st["empty_response"] == 1 and st["empty_retried"] == 3 and st["empty_recovered"] == 0
    assert delays == [2.5, 4.5, 8.5]


def test_empty_retries_zero_single_call():
    calls = []
    _, st, delays = _mb(lambda m, **k: calls.append(1) or "", empty_retries=0)
    assert len(calls) == 1 and delays == [] and st["empty_response"] == 1 and st["empty_retried"] == 0


def test_parse_fail_not_retried():
    calls = []
    _, st, delays = _mb(lambda m, **k: calls.append(1) or "garbage")
    assert len(calls) == 1 and st["parse_fail"] == 1 and delays == []


def test_llm_exception_not_retried():
    calls = []

    def chat(m, **k):
        calls.append(1)
        raise RuntimeError("x")

    _, st, _ = _mb(chat)
    assert len(calls) == 1 and st["llm_error"] == 1


def test_run_mine_retry_stats_and_total_calls(tmp_path):
    calls = []
    kw = _kw(tmp_path, _seq_chat(["", _GOOD], calls), [T(0), T(1)], sleep=lambda s: None)
    res = cli.run_mine(**kw)
    assert res["stats"]["empty_retried"] == 1 and res["stats"]["empty_recovered"] == 1
    assert res["stats"]["batches_done"] == 1 and res["total_llm_calls"] == 2
    kw2 = _kw(tmp_path, lambda m, **k: "", [T(0), T(1)], sleep=lambda s: None, empty_retries=1,
              out_path=str(tmp_path / "r2.json"))
    res2 = cli.run_mine(**kw2)
    assert res2["total_llm_calls"] == 2 and res2["stats"]["empty_response"] == 1


def test_split_after_retries_counts_calls(tmp_path):
    ts = [T(i) for i in range(12)]
    res = cli.run_mine(**_kw(tmp_path, lambda m, **k: "", ts, batch_size=12, split_on_empty=True,
                             empty_retries=1, sleep=lambda s: None))
    # whole (2 calls) -> two halves of 6, each 2 calls; halves cannot split further
    assert res["total_llm_calls"] == 6 and len(res["blocked_chunks"]) == 2


def test_main_passes_empty_retries_flag(env, capsys, monkeypatch):
    seen = []
    orig = mine.mine_batch

    def spy(*a, **kw):
        seen.append(kw.get("empty_retries"))
        kw["sleep"] = lambda s: None
        return orig(*a, **kw)

    monkeypatch.setattr(mine, "mine_batch", spy)
    env.chat = lambda m, **kw: ""
    assert cli.main(["mine", "--out", env.out, "--batch-size", "5", "--empty-retries", "2"]) == 3
    data, err = _one_json(capsys)
    assert seen == [2] and data["stats"]["empty_retried"] == 2 and data["total_llm_calls"] == 3
    assert "still empty after 2 retries" in err
    env.chat = lambda m, **kw: ""
    cli.main(["mine", "--out", env.out + "b", "--batch-size", "5"])
    assert seen[-1] == 3


def test_stop_after_empty_skips_remaining_batches(tmp_path):
    ts = [T(i) for i in range(40)]
    calls = []

    def chat(messages, **kw):
        calls.append(1)
        return ""  # every batch blocked

    res = cli.run_mine(**_kw(tmp_path, chat, ts, batch_size=10, empty_retries=0, stop_after_empty=2))
    assert len(calls) == 2 and res["stopped_early"] is True and res["stats"]["empty_response"] == 2
    calls.clear()
    (tmp_path / "b").mkdir()
    res = cli.run_mine(**_kw(tmp_path / "b", chat, ts, batch_size=10, empty_retries=0))
    assert len(calls) == 4 and "stopped_early" not in res
