"""docexp CLI with fake chat / selection / store (no network, no DB)."""

from __future__ import annotations

import json
import time

import pytest

from app.docexp import cli


def _doc(i, body="OOM 발생으로 서버 다운 현상이 있었다. " * 20):
    return {"id": f"id{i}", "external_id": f"ext{i}", "title": f"주간 회의 {i}", "body_md": body, "rule": "meeting", "content_hash": f"h{i}"}


GOOD = json.dumps(
    {"summary": "서버 메모리 부족 종료", "keywords": ["OOM", "서버 다운 현상", "메모리 부족 종료"]},
    ensure_ascii=False,
)


class Env:
    def __init__(self):
        self.upserts = []
        self.existing = set()
        self.chat_calls = []
        self.bad_titles = set()
        self.empty_titles = set()
        self.hash_calls = []
        self.bad_upserts = set()


@pytest.fixture
def env(monkeypatch):
    e = Env()
    docs = [_doc(i) for i in range(5)]

    def fake_chat(messages, **kw):
        text = messages[-1]["content"]
        e.chat_calls.append(text)
        for t in e.bad_titles:
            if t in text:
                raise RuntimeError("boom")
        return GOOD

    monkeypatch.setattr(cli, "_get_chat", lambda: (fake_chat, "test-model"))
    monkeypatch.setattr(cli, "select_documents", lambda limit=None, seed=None: iter(docs[:limit] if limit else docs))
    monkeypatch.setattr(cli.store, "existing_ids", lambda ids, hashes=None: (e.hash_calls.append(hashes), {i for i in ids if i in e.existing})[1])
    def fake_upsert(did, rec, **kw):
        if did in e.bad_upserts:
            raise RuntimeError("db down")
        e.upserts.append((did, rec, kw))

    monkeypatch.setattr(cli.store, "upsert", fake_upsert)
    e.docs = docs
    return e


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_no_commit_means_no_db_write(env, capsys, tmp_path):
    out = tmp_path / "o.json"
    code, res = _run(capsys, "run", "--out", str(out))
    assert code == 0 and env.upserts == []
    assert res["dry_run"] is True and res["committed"] == 0 and res["stats"]["ok"] == 5
    assert len(json.loads(out.read_text())) == 5


def test_commit_writes_with_run_id_and_model(env, capsys, tmp_path):
    code, res = _run(capsys, "run", "--commit", "--out", str(tmp_path / "o.json"))
    assert code == 0 and res["committed"] == 5 and len(env.upserts) == 5
    assert {kw["model"] for _, _, kw in env.upserts} == {"test-model"}
    assert {kw["run_id"] for _, _, kw in env.upserts} == {res["run_id"]}


def test_resume_skips_existing_without_llm_call(env, capsys, tmp_path):
    env.existing = {"id0", "id1", "id2"}
    code, res = _run(capsys, "run", "--commit", "--out", str(tmp_path / "o.json"))
    assert code == 0 and res["skipped_existing"] == 3 and res["committed"] == 2
    assert len(env.chat_calls) == 2
    assert {d for d, _, _ in env.upserts} == {"id3", "id4"}


def test_force_regenerates_existing(env, capsys, tmp_path):
    env.existing = {"id0"}
    code, res = _run(capsys, "run", "--commit", "--force", "--out", str(tmp_path / "o.json"))
    assert res["skipped_existing"] == 0 and len(env.chat_calls) == 5


def test_llm_error_exits_3_and_others_saved(env, capsys, tmp_path):
    env.bad_titles = {"주간 회의 2"}
    code, res = _run(capsys, "run", "--commit", "--out", str(tmp_path / "o.json"))
    assert code == 3
    assert res["stats"]["llm_error"] == 1 and res["stats"]["ok"] == 4 and len(env.upserts) == 4


def test_stdout_single_json_and_stats_consistent(env, capsys, tmp_path):
    env.existing = {"id0"}
    env.bad_titles = {"주간 회의 3"}
    code = cli.main(["run", "--commit", "--out", str(tmp_path / "o.json")])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    res = json.loads(lines[0])
    st = res["stats"]
    assert res["total"] == 5
    assert res["skipped_existing"] + sum(st[k] for k in cli.OUTCOMES) == res["total"]
    assert res["committed"] == st["ok"]


def test_workers_gt_one(env, capsys, tmp_path):
    code, res = _run(capsys, "run", "--commit", "--workers", "3", "--out", str(tmp_path / "o.json"))
    assert code == 0 and res["stats"]["ok"] == 5 and len(env.upserts) == 5


def test_backend_none_exits_2(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_get_chat", lambda: None)
    code = cli.main(["run"])
    assert code == 2


def test_sample_writes_skimmable_list_and_stores_only_on_commit(env, capsys, tmp_path):
    out = tmp_path / "s.json"
    code, res = _run(capsys, "sample", "--n", "3", "--out", str(out))
    assert code == 0 and env.upserts == []
    rows = json.loads(out.read_text())
    assert len(rows) == 3
    assert set(rows[0]) == {"external_id", "title", "rule", "summary", "keywords", "grounded"}
    code, res = _run(capsys, "sample", "--n", "3", "--commit", "--out", str(out))
    assert len(env.upserts) == 3


def test_count_by_rule(env, capsys):
    code, res = _run(capsys, "count")
    assert res["total"] == 5 and res["by_rule"]["meeting"]["docs"] == 5 and res["body_chars"] > 0


def test_hash_passed_to_existing_ids_and_upsert(env, capsys, tmp_path):
    _run(capsys, "run", "--commit", "--out", str(tmp_path / "o.json"))
    assert env.hash_calls and env.hash_calls[0] == {f"id{i}": f"h{i}" for i in range(5)}
    assert {d: kw["content_hash"] for d, _, kw in env.upserts} == {f"id{i}": f"h{i}" for i in range(5)}


def test_upsert_failure_counts_error_continues_exit_3(env, capsys, tmp_path):
    env.bad_upserts = {"id1"}
    out = tmp_path / "o.json"
    code, res = _run(capsys, "run", "--commit", "--out", str(out))
    st = res["stats"]
    assert code == 3 and st["error"] == 1 and st["ok"] == 4 and res["committed"] == 4
    assert res["skipped_existing"] + sum(st.get(k, 0) for k in cli.OUTCOMES) == res["total"]
    assert len(json.loads(out.read_text())) == 4


def test_exception_inside_expand_one_counts_error_and_continues(env, monkeypatch, capsys, tmp_path):
    real = cli.gen.parse_response
    n = []

    def boom(raw):
        n.append(1)
        if len(n) == 2:
            raise ValueError("bad parse")
        return real(raw)

    monkeypatch.setattr(cli.gen, "parse_response", boom)
    code, res = _run(capsys, "run", "--commit", "--workers", "1", "--out", str(tmp_path / "o.json"))
    assert code == 3 and res["stats"]["error"] == 1 and res["stats"]["ok"] == 4 and len(env.upserts) == 4


def test_out_written_on_keyboard_interrupt(env, capsys, tmp_path):
    out = tmp_path / "o.json"
    orig = cli.store.upsert

    def interrupt(did, rec, **kw):
        if did == "id2":
            raise KeyboardInterrupt
        return orig(did, rec, **kw)

    cli.store.upsert = interrupt
    try:
        with pytest.raises(KeyboardInterrupt):
            cli.main(["run", "--commit", "--workers", "1", "--out", str(out)])
    finally:
        cli.store.upsert = orig
    assert out.exists() and len(json.loads(out.read_text())) >= 2


def test_three_consecutive_fatal_aborts_exit_4(env, monkeypatch, capsys, tmp_path):
    from app import llm_chat

    def dead(messages, **kw):
        env.chat_calls.append(1)
        time.sleep(0.02)
        raise llm_chat.LLMChatError("LLM HTTP 402: payment required")

    monkeypatch.setattr(cli, "_get_chat", lambda: (dead, "m"))
    monkeypatch.setattr(cli, "select_documents", lambda limit=None, seed=None: iter([_doc(i) for i in range(200)]))
    out = tmp_path / "o.json"
    code, res = _run(capsys, "run", "--commit", "--workers", "1", "--out", str(out))
    assert code == 4 and "402" in res["aborted"]
    assert res["stats"]["fatal"] == 3 and len(env.chat_calls) < 50  # first chunk only, queue cancelled
    assert json.loads(out.read_text()) == []


def test_fatal_resets_on_success_no_abort(env, monkeypatch, capsys, tmp_path):
    from app import llm_chat

    n = []

    def flaky(messages, **kw):
        n.append(1)
        if len(n) % 3:  # fatal, fatal, ok, ...
            raise llm_chat.LLMChatError("LLM HTTP 401: x")
        return GOOD

    monkeypatch.setattr(cli, "_get_chat", lambda: (flaky, "m"))
    code, res = _run(capsys, "run", "--workers", "1", "--out", str(tmp_path / "o.json"))
    assert code == 3 and "aborted" not in res and res["stats"]["fatal"] == 4


def _row(i, **kw):
    r = {"source_type": "wiki", "external_id": f"ext{i}", "title": "t", "content_hash": f"h{i}",
         "summary": "서버 메모리 부족", "keywords": ["OOM", "메모리 부족", "커널 로그"], "grounded": ["OOM"],
         "rule": "meeting", "model": "m", "run_id": "orig"}
    r.update(kw)
    return r


def _write(path, rows):
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


@pytest.fixture
def imp(env, monkeypatch):
    docs = {("wiki", f"ext{i}"): {"id": f"id{i}", "content_hash": f"h{i}"} for i in range(3)}
    docs[("wiki", "ext2")]["content_hash"] = "NEW"
    monkeypatch.setattr(cli.store, "find_active_documents", lambda keys: {k: docs[k] for k in keys if k in docs})
    return env


def test_import_dry_run_counts(imp, capsys, tmp_path):
    f = tmp_path / "in.jsonl"
    _write(f, [_row(0), _row(1), _row(2), _row(9), "not json", _row(3, keywords="x"), _row(4, grounded=["zzz zzz"])])
    code, res = _run(capsys, "import", "--in", str(f))
    assert code == 0 and imp.upserts == []
    assert (res["read"], res["matched"], res["missing_doc"], res["hash_mismatch"], res["invalid"]) == (7, 3, 1, 1, 3)
    assert res["written"] == 0 and res["dry_run"] is True


def test_import_commit_and_ignore_hash(imp, capsys, tmp_path):
    f = tmp_path / "in.jsonl"
    _write(f, [_row(0), _row(2)])
    code, res = _run(capsys, "import", "--in", str(f), "--commit")
    assert res["written"] == 1 and res["hash_mismatch"] == 1
    did, rec, kw = imp.upserts[0]
    assert did == "id0" and kw["run_id"] == "import:orig" and kw["content_hash"] == "h0" and kw["model"] == "m"
    imp.upserts.clear()
    code, res = _run(capsys, "import", "--in", str(f), "--commit", "--ignore-hash")
    assert res["written"] == 2 and res["hash_mismatch"] == 0
    assert {d: kw["content_hash"] for d, _, kw in imp.upserts}["id2"] == "NEW"


def test_import_skip_existing(imp, capsys, tmp_path):
    imp.existing = {"id0"}
    f = tmp_path / "in.jsonl"
    _write(f, [_row(0), _row(1)])
    code, res = _run(capsys, "import", "--in", str(f), "--commit", "--skip-existing")
    assert res["skipped_existing"] == 1 and res["written"] == 1


def test_export_streams_jsonl(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.store, "iter_export", lambda: iter([_row(0), _row(1)]))
    out = tmp_path / "sub" / "e.jsonl"
    code, res = _run(capsys, "export", "--out", str(out))
    assert code == 0 and res["exported"] == 2
    assert [json.loads(x)["external_id"] for x in out.read_text().splitlines()] == ["ext0", "ext1"]
