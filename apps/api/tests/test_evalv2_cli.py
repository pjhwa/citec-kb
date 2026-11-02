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


@pytest.mark.parametrize("cmd", ["prep-real", "gen-synth", "pool", "judge", "sheet", "import-labels"])
def test_every_subcommand_has_help(cmd, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([cmd, "--help"])
    assert e.value.code == 0
    assert "--out" in capsys.readouterr().out
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
    assert cli.main(["pool", "--queries", str(qs), "--out", str(out)]) == 0
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
