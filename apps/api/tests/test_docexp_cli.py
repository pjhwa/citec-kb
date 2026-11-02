"""docexp CLI with fake chat / selection / store (no network, no DB)."""

from __future__ import annotations

import json

import pytest

from app.docexp import cli


def _doc(i, body="OOM 발생으로 서버 다운 현상이 있었다. " * 20):
    return {"id": f"id{i}", "external_id": f"ext{i}", "title": f"주간 회의 {i}", "body_md": body, "rule": "meeting"}


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
    monkeypatch.setattr(cli.store, "existing_ids", lambda ids: {i for i in ids if i in e.existing})
    monkeypatch.setattr(cli.store, "upsert", lambda did, rec, **kw: e.upserts.append((did, rec, kw)))
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
