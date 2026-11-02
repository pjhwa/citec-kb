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
    kw = _kw(tmp_path, lambda m, **k: "  \n", [T(0), T(1)])
    res = cli.run_mine(**kw)
    assert res["stats"]["empty_response"] == 1 and res["stats"]["batches_done"] == 0
    assert res["items"] == [] and len(res["empty_samples"]) == 1
    import os

    prog = kw["out_path"] + ".progress.jsonl"
    assert not os.path.exists(prog) or open(prog).read() == ""


def test_mine_batch_empty_is_not_parse_success():
    items, st = mine.mine_batch([T(0), T(1)], chat=lambda m, **k: "", term_df=lambda t: 10, params=mine.Params())
    assert items == [] and st["empty_response"] == 1 and st["parse_fail"] == 0


def test_main_empty_exit_3_warning_and_commit_skipped(env, capsys):
    env.chat = lambda m, **kw: ""
    assert cli.main(["mine", "--out", env.out, "--commit"]) == 3
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
    res = cli.run_mine(**_kw(tmp_path, _marker_chat(calls), ts, batch_size=40, split_on_empty=True))
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
    res = cli.run_mine(**_kw(tmp_path, _marker_chat(calls), ts, batch_size=40))
    assert len(calls) == 1 and "blocked_chunks" not in res


def test_main_split_flag_prints_extra_calls(env, capsys):
    env.chat = lambda m, **kw: ""
    monkey_tickets = [T(i) for i in range(12)]
    mine.load_tickets = lambda **kw: monkey_tickets  # env fixture's monkeypatch restores the original
    assert cli.main(["mine", "--out", env.out, "--batch-size", "12", "--split-on-empty"]) == 3
    data, err = _one_json(capsys)
    assert "extra (billable) LLM calls" in err and data["blocked_chunks"] and data["extra_calls"] == 2
