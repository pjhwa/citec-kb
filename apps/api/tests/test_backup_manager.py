"""scripts/backup_manager.py (fake dump command, temp dirs) and the /v1/ops/backups endpoints."""

from __future__ import annotations

import importlib.util
import json
import os
import tarfile
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("backup_manager", Path(__file__).resolve().parents[3] / "scripts" / "backup_manager.py")
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)

GOOD_DUMP = "printf 'CREATE TABLE t();\\n-- PostgreSQL database dump complete\\n'"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "data" / "raw").mkdir(parents=True)
    (root / "data" / "raw" / "a.txt").write_text("hello")
    (root / "data" / "system_registrants.json").write_text("{}")
    monkeypatch.setattr(bm, "ROOT", root)
    monkeypatch.setattr(bm, "DATA", root / "data")
    monkeypatch.setattr(bm, "STATUS_FILE", root / "data" / "backup_status.json")
    monkeypatch.setattr(bm, "REQ_DIR", root / "data" / "backup_requests")
    monkeypatch.setenv("BACKUP_DIR", str(root / "data" / "backups"))
    monkeypatch.setenv("BACKUP_DUMP_CMD", GOOD_DUMP)
    monkeypatch.setenv("BACKUP_MIN_FREE_GB", "0")
    return root


def _sets(root):
    return sorted(p.name for p in (root / "data" / "backups").iterdir() if p.is_dir() and not p.name.startswith("."))


def test_run_creates_verified_set_and_excludes_backups_dir(env):
    res = bm.do_backup("scheduled", 5)
    assert res["ok"], res
    (set_id,) = _sets(env)
    d = env / "data" / "backups" / set_id
    assert {p.name for p in d.iterdir()} == {"db.sql.gz", "data.tar.gz", "SHA256SUMS", "manifest.json"}
    names = tarfile.open(d / "data.tar.gz").getnames()
    assert "data/raw/a.txt" in names and "data/system_registrants.json" in names
    assert not any("backups" in n or "backup_status" in n for n in names)           # never archives itself
    assert bm.verify(None) == 0
    st = json.loads((env / "data" / "backup_status.json").read_text())
    assert st["sets"][0]["id"] == set_id and st["last_run"]["ok"] is True and st["disk"]["free"] > 0


def test_truncated_dump_is_a_failure_and_leaves_no_set(env, monkeypatch):
    monkeypatch.setenv("BACKUP_DUMP_CMD", "printf 'CREATE TABLE t();\\n'")           # no completion marker
    res = bm.do_backup("scheduled", 5)
    assert not res["ok"] and "완료 표식" in res["error"]
    assert _sets(env) == [] and not list((env / "data" / "backups").glob(".partial-*"))
    last = json.loads((env / "data" / "backups" / "last_run.json").read_text())
    assert last["ok"] is False


def test_failing_dump_command_reports_error(env, monkeypatch):
    monkeypatch.setenv("BACKUP_DUMP_CMD", "echo 'connection refused' >&2; exit 3")
    res = bm.do_backup("scheduled", 5)
    assert not res["ok"] and "connection refused" in res["error"] and _sets(env) == []


def test_rotation_keeps_newest_n_and_ignores_incomplete(env):
    bdir = env / "data" / "backups"
    for i in range(4):
        sid = f"2026110{i + 1}_030000-scheduled"
        (bdir / sid).mkdir(parents=True)
        (bdir / sid / "manifest.json").write_text(json.dumps({"id": sid, "kind": "scheduled", "finished_at": "2026-11-0%dT03:00:00+09:00" % (i + 1),
                                                              "db": {"size": 1}, "data": {"size": 1}}))
    (bdir / "20261031_030000-scheduled").mkdir()                                    # no manifest: incomplete, must not count or be restored
    res = bm.do_backup("manual", 3)
    assert res["ok"]
    kept = _sets(env)
    complete = [k for k in kept if (bdir / k / "manifest.json").exists()]
    assert len(complete) == 3 and res["id"] in complete and "20261101_030000-scheduled" not in kept


def test_free_space_guard(env, monkeypatch):
    monkeypatch.setenv("BACKUP_MIN_FREE_GB", "100000000")
    res = bm.do_backup("scheduled", 5)
    assert not res["ok"] and "디스크 여유 부족" in res["error"] and _sets(env) == []


def test_verify_detects_corruption(env):
    assert bm.do_backup("scheduled", 5)["ok"]
    (set_id,) = _sets(env)
    with open(env / "data" / "backups" / set_id / "data.tar.gz", "ab") as fh:
        fh.write(b"garbage")
    assert bm.verify(set_id) == 1


def test_process_requests_runs_manual_backup_once(env):
    bm.REQ_DIR.mkdir(parents=True)
    for n in ("20261105-100000-aaaa", "20261105-100001-bbbb"):
        (bm.REQ_DIR / f"{n}.json").write_text(json.dumps({"id": n, "requested_by": "boss", "status": "pending"}))
    assert bm.process_requests(5) == 0
    assert len(_sets(env)) == 1 and _sets(env)[0].endswith("-manual")
    assert not list(bm.REQ_DIR.glob("*.json"))
    done = [json.loads(p.read_text()) for p in (bm.REQ_DIR / "done").glob("*.json")]
    assert len(done) == 2 and all(d["status"] == "done" and d["set_id"] for d in done)


def test_failed_request_is_recorded(env, monkeypatch):
    monkeypatch.setenv("BACKUP_DUMP_CMD", "exit 1")
    bm.REQ_DIR.mkdir(parents=True)
    (bm.REQ_DIR / "20261105-100000-aaaa.json").write_text(json.dumps({"id": "x", "status": "pending"}))
    assert bm.process_requests(5) == 1
    (d,) = [json.loads(p.read_text()) for p in (bm.REQ_DIR / "done").glob("*.json")]
    assert d["status"] == "failed" and d["error"]


# ---------------------------------------------------------------- API

@pytest.fixture()
def api(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.auth.principal as pr
    from app.routers.ops import router
    from app.settings import get_settings

    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv("AUTH_TOKENS_JSON", '{"adm": {"sub":"a","name":"boss","roles":["admin"]}, "sen": {"sub":"s","name":"sen","roles":["senior"]}}')
    monkeypatch.setenv("DATA_DIR", str(env / "data"))
    get_settings.cache_clear()
    monkeypatch.setattr(pr, "_token_cache", None)
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app), {"Authorization": "Bearer adm"}
    get_settings.cache_clear()


def test_api_unavailable_then_ok_after_backup(api, env):
    c, h = api
    assert c.get("/v1/ops/backups", headers=h).json()["available"] is False
    assert c.get("/v1/ops/backups").status_code == 401
    assert c.get("/v1/ops/backups", headers={"Authorization": "Bearer sen"}).status_code == 403
    bm.do_backup("scheduled", 5)
    d = c.get("/v1/ops/backups", headers=h).json()
    assert d["available"] and d["overall"] == "ok" and d["warnings"] == [] and len(d["sets"]) == 1


def test_api_warnings(api, env):
    c, h = api
    bm.do_backup("scheduled", 5)
    st_path = env / "data" / "backup_status.json"
    st = json.loads(st_path.read_text())
    st["sets"][0]["finished_at"] = "2020-01-01T00:00:00+00:00"
    st["last_run"] = {"ok": False, "error": "pg_dump 실패"}
    st_path.write_text(json.dumps(st))
    old = time.time() - 3600
    os.utime(st_path, (old, old))
    d = c.get("/v1/ops/backups", headers=h).json()
    texts = " ".join(w["text"] for w in d["warnings"])
    assert d["overall"] == "bad" and "시간 전" in texts and "pg_dump 실패" in texts and "러너" in texts


def test_api_request_flow(api, env):
    c, h = api
    r = c.post("/v1/ops/backups/request", headers=h)
    assert r.status_code == 200 and r.json()["requested_by"] == "boss" and r.json()["runner_warning"]   # no status file yet
    assert c.post("/v1/ops/backups/request", headers=h).status_code == 409                                # one at a time
    assert c.post("/v1/ops/backups/request").status_code == 401
    bm.process_requests(5)                                                                                 # host runner picks it up
    d = c.get("/v1/ops/backups", headers=h).json()
    assert d["pending_request"] is None and d["recent_requests"][0]["status"] == "done"
    assert c.post("/v1/ops/backups/request", headers=h).status_code == 200
