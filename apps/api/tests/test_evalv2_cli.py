import json

import pytest

from app.evalv2 import cli


def test_prep_real_end_to_end(tmp_path, capsys):
    tsv = tmp_path / "log.tsv"
    tsv.write_text(
        "2026-10-20\tkb_search\t3\tq=Redis 타임아웃 원인 확인\n"
        "2026-10-20\tkb_search\t1\tq=CITECTS-2634\n", encoding="utf-8")
    out = tmp_path / "real.json"
    assert cli.main(["prep-real", "--tsv", str(tsv), "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert [i["q"] for i in d["items"]] == ["Redis 타임아웃 원인 확인"]
    assert d["stats"]["identifier_only"] == 1


def test_prep_real_warns_on_stderr_but_exits_zero_when_hints_flagged(tmp_path, capsys):
    tsv = tmp_path / "log.tsv"
    tsv.write_text("2026-10-20\tkb_search\t3\tq=장애 문의 담당자 hong@example.com 확인 요청\n", encoding="utf-8")
    out = tmp_path / "real.json"
    assert cli.main(["prep-real", "--tsv", str(tsv), "--out", str(out)]) == 0
    cap = capsys.readouterr()
    assert "WARNING" in cap.err and "WARNING" not in cap.out


OPTIONS = {
    "prep-real": ["--tsv", "--holdout", "--evalset", "--out"],
    "gen-synth": ["--slice", "--n", "--per-doc", "--seed", "--holdout", "--evalset", "--out"],
    "pool": ["--queries", "--base-url", "--top-n", "--max-pool", "--out", "--resume", "--force"],
    "judge": ["--pooled", "--out", "--resume", "--force"],
    "sheet": ["--judged", "--doc-base", "--out"],
    "import-labels": ["--judged", "--review", "--evalset-id", "--out-dir"],
}


@pytest.mark.parametrize("cmd", sorted(OPTIONS))
def test_every_subcommand_has_help_with_its_exact_options(cmd, capsys):
    import re

    with pytest.raises(SystemExit) as e:
        cli.main([cmd, "--help"])
    assert e.value.code == 0
    shown = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", capsys.readouterr().out)) - {"--help"}
    assert shown == set(OPTIONS[cmd])
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0


def _queries(tmp_path):
    qs = tmp_path / "q.json"
    qs.write_text(json.dumps({"items": [
        {"id": "r-001", "q": "질의 하나 입니다", "slice": "real", "source": "real"},
        {"id": "s-001", "q": "질의 둘 입니다", "slice": "symptom", "source": "synthetic",
         "seed_doc": {"external_id": "SEED", "title": "seed", "source_type": "support_history"}},
    ]}), encoding="utf-8")
    return qs


def test_pool_command_uses_injected_search_and_seed_docs(tmp_path, monkeypatch, capsys):
    qs = _queries(tmp_path)

    def fake_search(q, filters, multi_query, top_k):
        return [{"external_id": "A", "title": "a", "source_type": "support_history", "evidence_eligible": True}]

    monkeypatch.setattr(cli.pool, "rest_search", lambda base: fake_search)
    out = tmp_path / "pooled.json"
    assert cli.main(["pool", "--queries", str(qs), "--base-url", "http://x", "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    by = {q["id"]: q for q in d["queries"]}
    assert [c["external_id"] for c in by["r-001"]["candidates"]] == ["A"]
    assert {c["external_id"] for c in by["s-001"]["candidates"]} == {"A", "SEED"}
    cap = capsys.readouterr()
    assert "r-001" in cap.err and cap.out == ""            # progress goes to stderr
    assert d["run_id"] == cli.run_id_for(json.loads(qs.read_text(encoding="utf-8"))["items"])


def test_pool_survives_a_query_whose_pooling_raises(tmp_path, monkeypatch, capsys):
    qs = tmp_path / "q.json"
    qs.write_text(json.dumps({"items": [
        {"id": "s-001", "q": "질의 하나 입니다", "slice": "symptom", "source": "synthetic",
         "seed_doc": "not-a-dict"},                                   # breaks the extra/seed path
        {"id": "r-002", "q": "질의 둘 입니다", "slice": "real", "source": "real"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(cli.pool, "rest_search", lambda base: (
        lambda q, f, mq, k: [{"external_id": "A", "title": "a", "source_type": "x", "evidence_eligible": True}]))
    out = tmp_path / "pooled.json"
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out)]) == 3    # a pool_error -> exit 3
    by = {q["id"]: q for q in json.loads(out.read_text(encoding="utf-8"))["queries"]}
    assert by["s-001"]["candidates"] == [] and by["s-001"]["pool_error"]
    assert [c["external_id"] for c in by["r-002"]["candidates"]] == ["A"]
    assert "pool failed" in capsys.readouterr().err


def _pooled(tmp_path, ids=("r-001",)):
    p = tmp_path / "pooled.json"
    p.write_text(json.dumps({"queries": [{
        "id": i, "q": "질의 " + i, "slice": "real", "source": "real",
        "candidates": [{"external_id": "A", "title": "a", "source_type": "x", "evidence_eligible": True, "arms": {"mq_on": 1}}],
    } for i in ids]}), encoding="utf-8")
    return p


def test_judge_command_attaches_llm_grades(tmp_path, monkeypatch, capsys):
    pooled = _pooled(tmp_path)
    monkeypatch.setattr(cli, "_load_bodies", lambda eids: {"A": "본문에 Out of Memory killer 동작 기록"})
    monkeypatch.setattr(cli, "_get_chat", lambda: (lambda messages, **kw: json.dumps(
        {"judgments": [{"id": "c1", "grade": 2, "evidence": "Out of Memory killer 동작"}]})), raising=True)
    monkeypatch.setattr(cli, "_model_name", lambda: "fake-model")
    out = tmp_path / "judged.json"
    assert cli.main(["judge", "--pooled", str(pooled), "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["model"] == "fake-model"
    assert d["queries"][0]["candidates"][0]["llm"]["grade"] == 2
    assert "fake-model" in capsys.readouterr().err


def _ok_search(q, f, mq, k):
    return [{"external_id": "A", "title": "a", "source_type": "x", "evidence_eligible": True}]


def test_run_id_is_deterministic_and_order_independent():
    a = [{"id": "q1", "q": "하나"}, {"id": "q2", "q": "둘"}]
    assert cli.run_id_for(a) == cli.run_id_for(list(reversed(a))) and len(cli.run_id_for(a)) == 10
    assert cli.run_id_for(a) != cli.run_id_for([{"id": "q1", "q": "하나"}, {"id": "q2", "q": "셋"}])


def test_pool_duplicate_ids_exit_2_and_write_nothing(tmp_path, monkeypatch, capsys):
    q1, q2 = _queries(tmp_path), tmp_path / "q2.json"
    q2.write_text(json.dumps({"items": [{"id": "r-001", "q": "다른 질의", "slice": "real", "source": "real"}]}), encoding="utf-8")
    monkeypatch.setattr(cli.pool, "rest_search", lambda base: _ok_search)
    out = tmp_path / "p.json"
    assert cli.main(["pool", "--queries", str(q1), str(q2), "--out", str(out)]) == 2
    assert "r-001" in capsys.readouterr().err and not out.exists()


def test_pool_exists_needs_resume_or_force_and_resume_skips_done(tmp_path, monkeypatch, capsys):
    qs = _queries(tmp_path)
    calls = []
    monkeypatch.setattr(cli.pool, "rest_search", lambda base: (lambda q, f, mq, k: calls.append(q) or _ok_search(q, f, mq, k)))
    out = tmp_path / "p.json"
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out)]) == 0
    n = len(calls)
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out)]) == 2          # refuse overwrite
    assert len(calls) == n
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out), "--resume"]) == 0
    assert len(calls) == n                                                            # nothing redone
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out), "--force"]) == 0
    assert len(calls) == 2 * n
    other = tmp_path / "o.json"
    other.write_text(json.dumps({"items": [{"id": "z", "q": "전혀 다른 질의", "slice": "real", "source": "real"}]}), encoding="utf-8")
    assert cli.main(["pool", "--queries", str(other), "--out", str(out), "--resume"]) == 2   # run_id mismatch


def test_pool_resume_redoes_errored_queries_and_flushes_each_query(tmp_path, monkeypatch):
    qs = _queries(tmp_path)
    out = tmp_path / "p.json"
    seen_on_disk = []

    def search(q, f, mq, k):
        if out.exists():
            seen_on_disk.append(len(json.loads(out.read_text(encoding="utf-8"))["queries"]))
        return _ok_search(q, f, mq, k)

    monkeypatch.setattr(cli.pool, "rest_search", lambda base: search)
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out)]) == 0
    assert 1 in seen_on_disk                      # after the first query one result was already on disk
    d = json.loads(out.read_text(encoding="utf-8"))
    d["queries"][0]["pool_error"] = "x"
    d["queries"][0]["candidates"] = []
    out.write_text(json.dumps(d), encoding="utf-8")
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out), "--resume"]) == 0
    d2 = json.loads(out.read_text(encoding="utf-8"))
    assert all(not q.get("pool_error") and q["candidates"] for q in d2["queries"])


def test_pool_all_failed_exits_3(tmp_path, monkeypatch):
    qs = tmp_path / "q.json"
    qs.write_text(json.dumps({"items": [{"id": "a", "q": "하나", "slice": "real", "source": "real", "seed_doc": "bad"}]}), encoding="utf-8")
    monkeypatch.setattr(cli.pool, "rest_search", lambda base: _ok_search)
    assert cli.main(["pool", "--queries", str(qs), "--out", str(tmp_path / "p.json")]) == 3


def test_atomic_write_leaves_old_file_intact_when_serialization_fails(tmp_path):
    p = tmp_path / "x.json"
    cli._write_json(str(p), {"a": 1})
    with pytest.raises(TypeError):
        cli._write_json(str(p), {"a": object()})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]


def test_judge_duplicate_ids_exit_2(tmp_path, monkeypatch, capsys):
    pooled = _pooled(tmp_path, ("r-001", "r-001"))
    monkeypatch.setattr(cli, "_get_chat", lambda: (lambda *a, **k: ""))
    monkeypatch.setattr(cli, "_model_name", lambda: "m")
    out = tmp_path / "j.json"
    assert cli.main(["judge", "--pooled", str(pooled), "--out", str(out)]) == 2
    assert "r-001" in capsys.readouterr().err and not out.exists()


def test_judge_copies_run_id_and_empty_status_exits_3_with_counts(tmp_path, monkeypatch, capsys):
    pooled = _pooled(tmp_path)
    d = json.loads(pooled.read_text(encoding="utf-8"))
    d["run_id"] = "abc123"
    pooled.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(cli, "_load_bodies", lambda keys: {"A": "본문"})
    monkeypatch.setattr(cli, "_get_chat", lambda: (lambda messages, **kw: ""))      # content-filter style empty reply
    monkeypatch.setattr(cli, "_model_name", lambda: "m")
    monkeypatch.setattr(cli.judge_mod.time, "sleep", lambda s: None)
    out = tmp_path / "j.json"
    assert cli.main(["judge", "--pooled", str(pooled), "--out", str(out)]) == 3
    j = json.loads(out.read_text(encoding="utf-8"))
    assert j["run_id"] == "abc123" and j["queries"][0]["candidates"][0]["llm"]["status"] == "empty"
    assert "empty=1" in capsys.readouterr().err


def test_judge_resume_skips_done_and_redoes_llm_error_and_requires_flag(tmp_path, monkeypatch):
    pooled = _pooled(tmp_path, ("r-001", "r-002"))
    monkeypatch.setattr(cli, "_load_bodies", lambda keys: {"A": "본문에 Out of Memory killer 동작 기록"})
    monkeypatch.setattr(cli, "_model_name", lambda: "m")
    asked = []

    def chat(messages, **kw):
        asked.append(messages[-1]["content"])
        if len(asked) == 1:
            raise RuntimeError("boom")
        return json.dumps({"judgments": [{"id": "c1", "grade": 2, "evidence": "Out of Memory killer 동작"}]})

    monkeypatch.setattr(cli, "_get_chat", lambda: chat)
    out = tmp_path / "j.json"
    assert cli.main(["judge", "--pooled", str(pooled), "--out", str(out)]) == 3
    assert cli.main(["judge", "--pooled", str(pooled), "--out", str(out)]) == 2        # refuses overwrite
    n = len(asked)
    assert cli.main(["judge", "--pooled", str(pooled), "--out", str(out), "--resume"]) == 0
    assert len(asked) == n + 1                                                          # only r-001 redone
    j = json.loads(out.read_text(encoding="utf-8"))
    assert [q["id"] for q in j["queries"]] == ["r-001", "r-002"]
    assert all(q["candidates"][0]["llm"]["grade"] == 2 for q in j["queries"])


def test_load_bodies_pairs_are_passed_to_loader_with_source_type(tmp_path, monkeypatch):
    pooled = _pooled(tmp_path)
    got = []
    monkeypatch.setattr(cli, "_load_bodies", lambda keys: got.append(keys) or {})
    monkeypatch.setattr(cli, "_get_chat", lambda: (lambda *a, **k: json.dumps({"judgments": []})))
    monkeypatch.setattr(cli, "_model_name", lambda: "m")
    cli.main(["judge", "--pooled", str(pooled), "--out", str(tmp_path / "j.json")])
    assert got == [[("x", "A")]]


def test_import_labels_preserves_frozen_at_on_reimport_and_prints_new_keys(tmp_path, capsys):
    judged = {"run_id": "r", "model": "m", "queries": [{
        "id": "r-001", "q": "질의 하나 입니다", "slice": "real", "source": "real",
        "candidates": [{"external_id": "A", "title": "a", "source_type": "x", "evidence_eligible": True,
                        "arms": {"mq_on": 1}, "llm": {"grade": 2, "evidence": "", "status": "ok"}}]}]}
    jp, rp, out = tmp_path / "j.json", tmp_path / "r.json", tmp_path / "out"
    jp.write_text(json.dumps(judged), encoding="utf-8")
    rp.write_text(json.dumps({"reviewer": "r", "reviewed_on": "2026-11-05", "labels": {"r-001": {"A": "O"}}}), encoding="utf-8")
    out.mkdir()
    (out / "evalset-v2-frozen.json").write_text(json.dumps({"frozen_at_utc": "2026-01-01T00:00:00+00:00", "cases": []}), encoding="utf-8")
    args = ["import-labels", "--judged", str(jp), "--review", str(rp), "--evalset-id", "t", "--out-dir", str(out)]
    assert cli.main(args) == 0
    assert json.loads((out / "evalset-v2-frozen.json").read_text(encoding="utf-8"))["frozen_at_utc"] == "2026-01-01T00:00:00+00:00"
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "unlabeled_candidates" in printed and printed["kept_by_slice"] == {"real": 1}


def test_sheet_creates_parent_dir(tmp_path):
    jp = tmp_path / "j.json"
    jp.write_text(json.dumps({"run_id": "r", "queries": []}), encoding="utf-8")
    assert cli.main(["sheet", "--judged", str(jp), "--out", str(tmp_path / "a" / "b" / "s.html")]) == 0


def test_judge_marks_llm_error_per_query_continues_and_exits_3(tmp_path, monkeypatch, capsys):
    pooled = _pooled(tmp_path, ("r-001", "r-002"))
    monkeypatch.setattr(cli, "_load_bodies", lambda eids: {"A": "본문에 Out of Memory killer 동작 기록"})

    def chat(messages, **kw):
        if "r-001" in messages[-1]["content"]:
            raise RuntimeError("boom")
        return json.dumps({"judgments": [{"id": "c1", "grade": 2, "evidence": "Out of Memory killer 동작"}]})

    monkeypatch.setattr(cli, "_get_chat", lambda: chat)
    monkeypatch.setattr(cli, "_model_name", lambda: "fake-model")
    out = tmp_path / "judged.json"
    assert cli.main(["judge", "--pooled", str(pooled), "--out", str(out)]) == 3
    by = {q["id"]: q for q in json.loads(out.read_text(encoding="utf-8"))["queries"]}   # output still written
    assert by["r-001"]["candidates"][0]["llm"] == {"grade": None, "status": "llm_error", "evidence": ""}
    assert by["r-002"]["candidates"][0]["llm"]["grade"] == 2
    assert "llm_error" in capsys.readouterr().err


def test_gen_synth_prints_model_name(tmp_path, monkeypatch, capsys):
    import contextlib

    @contextlib.contextmanager
    def fake_scope():
        yield object()

    import app.db.session as dbs
    monkeypatch.setattr(dbs, "session_scope", fake_scope)
    monkeypatch.setattr(cli.synth, "sample_docs", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_get_chat", lambda: (lambda *a, **k: ""))
    monkeypatch.setattr(cli, "_model_name", lambda: "fake-model")
    out = tmp_path / "s.json"
    assert cli.main(["gen-synth", "--slice", "pointer", "--n", "1", "--out", str(out)]) == 0
    assert "fake-model" in capsys.readouterr().err
    assert json.loads(out.read_text(encoding="utf-8"))["items"] == []


def _patch_synth(monkeypatch, docs, chat):
    import contextlib

    import app.db.session as dbs

    @contextlib.contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr(dbs, "session_scope", fake_scope)
    monkeypatch.setattr(cli.synth, "sample_docs", lambda *a, **k: docs)
    monkeypatch.setattr(cli, "_get_chat", lambda: chat)
    monkeypatch.setattr(cli, "_model_name", lambda: "fake-model")


DOC = {"external_id": "E1", "title": "정기 배포 사전 점검 안내", "source_type": "confluence_map", "excerpt": "본문"}


def test_gen_synth_uses_hashed_id_prefix_and_exits_0_on_success(tmp_path, monkeypatch):
    _patch_synth(monkeypatch, [DOC], lambda *a, **k: json.dumps({"queries": ["릴리즈 직전 확인할 것들 정리"]}))
    out = tmp_path / "s.json"
    assert cli.main(["gen-synth", "--slice", "pointer", "--n", "1", "--seed", "S", "--out", str(out)]) == 0
    items = json.loads(out.read_text(encoding="utf-8"))["items"]
    assert items and items[0]["id"].startswith(cli.synth.id_prefix("pointer", "S") + "-")


def test_gen_synth_exit_3_when_llm_fails_or_nothing_produced(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    _patch_synth(monkeypatch, [DOC], boom)
    assert cli.main(["gen-synth", "--slice", "pointer", "--n", "1", "--out", str(tmp_path / "a.json")]) == 3
    _patch_synth(monkeypatch, [DOC], lambda *a, **k: json.dumps({"queries": []}))
    assert cli.main(["gen-synth", "--slice", "pointer", "--n", "1", "--out", str(tmp_path / "b.json")]) == 3
    _patch_synth(monkeypatch, [], lambda *a, **k: "")
    assert cli.main(["gen-synth", "--slice", "pointer", "--n", "1", "--out", str(tmp_path / "c.json")]) == 0   # no docs: not an LLM failure


def test_sheet_and_import_labels_roundtrip(tmp_path):
    judged = {"run_id": "r", "model": "m", "queries": [{
        "id": "r-001", "q": "질의 하나 입니다", "slice": "real", "source": "real",
        "candidates": [{"external_id": "A", "title": "a", "source_type": "x", "evidence_eligible": True,
                        "arms": {"mq_on": 1}, "llm": {"grade": 2, "evidence": "", "status": "ok"}}]}]}
    jp = tmp_path / "judged.json"
    jp.write_text(json.dumps(judged), encoding="utf-8")
    html = tmp_path / "review.html"
    assert cli.main(["sheet", "--judged", str(jp), "--out", str(html)]) == 0
    assert "r-001" in html.read_text(encoding="utf-8")
    rp = tmp_path / "review-labels.json"
    rp.write_text(json.dumps({"reviewer": "r", "reviewed_on": "2026-11-05", "labels": {"r-001": {"A": "O"}}}), encoding="utf-8")
    outdir = tmp_path / "out"
    assert cli.main(["import-labels", "--judged", str(jp), "--review", str(rp), "--evalset-id", "t", "--out-dir", str(outdir)]) == 0
    rep = json.loads((outdir / "report.json").read_text(encoding="utf-8"))
    assert rep["kept"] == 1
    assert (outdir / "evalset-v2-dev.json").exists() and (outdir / "evalset-v2-frozen.json").exists()


def test_import_labels_rejects_invalid_review(tmp_path):
    jp = tmp_path / "judged.json"
    jp.write_text(json.dumps({"queries": [{"id": "q", "q": "a", "slice": "real", "source": "real", "candidates": []}]}), encoding="utf-8")
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps({"reviewer": "", "reviewed_on": "x", "labels": {}}), encoding="utf-8")
    assert cli.main(["import-labels", "--judged", str(jp), "--review", str(rp), "--evalset-id", "t", "--out-dir", str(tmp_path / "o")]) == 2
