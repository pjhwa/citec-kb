"""Admin mining runner: lifecycle with a fake CLI (no LLM, no DB for the process part)."""

from __future__ import annotations

import json
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.synonyms.runner as runner
from app.routers.synonym_candidates import router
from app.settings import get_settings

ADMIN = {"Authorization": "Bearer adm"}

FAKE_CLI = '''
import argparse, json, sys, time
p = argparse.ArgumentParser(); p.add_argument("cmd")
for a in ("--since-years","--batch-size","--run-id","--out","--limit-tickets","--max-batches"): p.add_argument(a)
p.add_argument("--commit", action="store_true"); p.add_argument("--split-on-empty", action="store_true")
p.add_argument("--min-terms-per-ticket", type=int, default=2); p.add_argument("--stop-after-empty", type=int, default=0)
a = p.parse_args()
print(f"run_id={a.run_id} tickets: 250 backend=fake model=m", file=sys.stderr)
mode = open(a.out + ".mode").read() if __import__("os").path.exists(a.out + ".mode") else "ok"
if mode == "slow":
    time.sleep(60)
with open(a.out + ".progress.jsonl", "a") as f:
    f.write(json.dumps({"run_id": a.run_id, "batch": 0}) + "\\n")
json.dump({"run_id": a.run_id, "model": "m", "items": [{"terms": ["a1", "b1"], "evidence": {"tickets": ["T"]}}], "stats": {"batches_done": 1}}, open(a.out, "w"))
print(json.dumps({"run_id": a.run_id, "stats": {"batches_done": 1}, "commit": None}))
sys.exit(3 if mode == "partial" else 0)
'''


@pytest.fixture()
def env(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv("AUTH_TOKENS_JSON", '{"adm": {"sub":"a","name":"boss","roles":["admin"]}, "sen": {"sub":"s","name":"sen","roles":["senior"]}}')
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    import app.auth.principal as pr
    monkeypatch.setattr(pr, "_token_cache", None)
    cli = tmp_path / "fakecli.py"
    cli.write_text(FAKE_CLI)
    monkeypatch.setattr(runner.sys, "executable", sys.executable)
    monkeypatch.setattr(runner, "CLI_MODULE", None)  # replaced below by argv rewrite
    real_popen = runner.subprocess.Popen

    def popen(argv, **kw):  # run the fake script instead of `python -m app.synonyms.cli`
        i = argv.index("-m")
        argv = argv[:i] + [str(cli)] + argv[i + 2:]
        return real_popen(argv, **kw)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    app = FastAPI(); app.include_router(router)
    yield TestClient(app), tmp_path
    get_settings.cache_clear()


def _wait(c, rid, until, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        d = c.get(f"/v1/lexicon/synonym-candidates/mine-runs/{rid}", headers=ADMIN).json()
        if d["status"] in until:
            return d
        time.sleep(0.1)
    raise AssertionError(d)


def test_run_done_with_progress_and_result(env):
    c, _ = env
    r = c.post("/v1/lexicon/synonym-candidates/mine-runs", json={"since_years": 1, "limit_tickets": 250, "batch_size": 100}, headers=ADMIN)
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    d = _wait(c, rid, {"done", "partial", "failed", "config_error"})
    assert d["status"] == "done" and d["exit_code"] == 0
    assert d["items_found"] == 1 and d["stats"] == {"batches_done": 1}
    assert d["progress"] == {"tickets": 250, "batches_done": 1, "batches_total": 3}
    assert d["started_by"] == "boss" and d["args"]["batch_size"] == 100
    assert any("tickets: 250" in ln for ln in d["log_tail"])
    assert [x["run_id"] for x in c.get("/v1/lexicon/synonym-candidates/mine-runs", headers=ADMIN).json()["runs"]] == [rid]


def test_partial_exit_code_is_reported(env):
    c, tmp = env
    # the fake reads <out>.mode; pre-create it via the predictable run id
    rid = "adm-20260101-000000"
    (tmp / "data" / "synonym_runs").mkdir()
    (tmp / "data" / "synonym_runs" / f"{rid}.json.mode").write_text("partial")
    import app.synonyms.runner as rn
    d = rn.start(started_by="t", run_id=rid)
    d = _wait(c, rid, {"done", "partial", "failed"})
    assert d["status"] == "partial" and d["exit_code"] == 3 and "다시 실행" in d["hint"]


def test_only_one_run_at_a_time_and_cancel(env):
    c, tmp = env
    rid = "adm-20260101-000001"
    (tmp / "data" / "synonym_runs").mkdir()
    (tmp / "data" / "synonym_runs" / f"{rid}.json.mode").write_text("slow")
    runner.start(started_by="t", run_id=rid)
    assert c.post("/v1/lexicon/synonym-candidates/mine-runs", json={}, headers=ADMIN).status_code == 409
    r = c.post(f"/v1/lexicon/synonym-candidates/mine-runs/{rid}/cancel", headers=ADMIN)
    assert r.status_code == 200
    d = _wait(c, rid, {"cancelled", "interrupted", "failed"})
    assert d["status"] == "cancelled"
    assert c.post(f"/v1/lexicon/synonym-candidates/mine-runs/{rid}/cancel", headers=ADMIN).status_code == 409


def test_auth_and_validation(env):
    c, _ = env
    url = "/v1/lexicon/synonym-candidates/mine-runs"
    assert c.post(url, json={}).status_code == 401
    assert c.post(url, json={}, headers={"Authorization": "Bearer sen"}).status_code == 403  # senior is not enough
    assert c.post(url, json={"batch_size": 5}, headers=ADMIN).status_code == 422
    assert c.get(url + "/../../etc", headers=ADMIN).status_code in (404, 405)
    assert c.get(url + "/not-a-run", headers=ADMIN).status_code == 404
