"""Run `python -m app.synonyms.cli mine` from the admin page and track it.

The miner is long-running and bills LLM calls, so it runs as a detached subprocess of the api
container (same code path as scripts/synonym_mine.sh). State lives in files under
<data_dir>/synonym_runs/<run_id>.* so it survives api reloads:

  .meta.json    args, started_by/at, pid
  .json         miner result ({run_id, model, items, stats}) — written when the run ends
  .progress.jsonl  one line per finished batch (resume checkpoint)
  .stdout.json  the CLI's one-line summary ({run_id, stats, commit})
  .log          stderr (progress, warnings)
  .exit         exit code, written by the wrapper shell when the process ends

Exit codes follow the CLI: 0 ok, 3 partial (some batches failed), 2 config/IO error.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.settings import get_settings

CLI_MODULE = "app.synonyms.cli"
RUN_ID_RE = re.compile(r"^adm-\d{8}-\d{6}(-\d+)?$")
_LIST_LIMIT = 20


class RunError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def runs_dir() -> Path:
    return Path(get_settings().data_dir) / "synonym_runs"


def _p(run_id: str, suffix: str) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise RunError(404, "run not found")
    return runs_dir() / f"{run_id}{suffix}"


def _pid_alive(pid: int | None, run_id: str | None = None) -> bool:
    """True when pid is a live process that belongs to run_id (its cmdline names the run). After an api restart
    the pid namespace restarts, so a stale pid may now be an unrelated process — that must not count as running."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie still answers kill(0); treat it as ended
        if "Z" in Path(f"/proc/{pid}/status").read_text().split("State:")[1].split()[0]:
            return False
    except (OSError, IndexError):
        return True
    if run_id:
        try:
            return run_id in Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            return True
    return True


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tail(path: Path, n: int = 40) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 16384))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [ln[:400] for ln in data.splitlines() if ln.strip()][-n:]


def _progress(run_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Batches finished vs expected (ticket count comes from the CLI's first stderr line)."""
    done = 0
    try:
        with open(_p(run_id, ".json.progress.jsonl"), encoding="utf-8") as fh:
            done = sum(1 for ln in fh if ln.strip())
    except OSError:
        pass
    tickets = None
    for ln in _tail(_p(run_id, ".log"), 400):
        m = re.search(r"tickets: (\d+)", ln)
        if m:
            tickets = int(m.group(1))
            break
    total = None
    if tickets is not None:
        bs = int(meta["args"].get("batch_size") or 100)
        total = -(-tickets // bs)
        mb = meta["args"].get("max_batches")
        if mb:
            total = min(total, int(mb))
    return {"tickets": tickets, "batches_done": done, "batches_total": total}


def _status(run_id: str, meta: dict[str, Any]) -> tuple[str, int | None]:
    exit_file = _p(run_id, ".exit")
    if exit_file.exists():
        try:
            code = int(exit_file.read_text().strip())
        except ValueError:
            code = None
        if meta.get("cancelled"):
            return "cancelled", code
        return {0: "done", 3: "partial", 2: "config_error"}.get(code, "failed"), code
    if _pid_alive(meta.get("pid"), run_id):
        return "running", None
    return ("cancelled" if meta.get("cancelled") else "interrupted"), None


def describe(run_id: str, *, detail: bool = False) -> dict[str, Any]:
    meta = _read_json(_p(run_id, ".meta.json"))
    if not isinstance(meta, dict):
        raise RunError(404, "run not found")
    status, code = _status(run_id, meta)
    summary = _read_json(_p(run_id, ".stdout.json")) or {}
    result = _read_json(_p(run_id, ".json")) if status != "running" else None
    out: dict[str, Any] = {
        "run_id": run_id, "status": status, "exit_code": code,
        "started_at": meta.get("started_at"), "started_by": meta.get("started_by"), "args": meta.get("args"),
        "stats": (summary.get("stats") or (result or {}).get("stats")),
        "commit": summary.get("commit"),
        "committed": bool(meta.get("imported")) or bool(summary.get("commit")),
        "items_found": len((result or {}).get("items") or []) if result else None,
        "progress": _progress(run_id, meta),
    }
    if status in ("partial", "failed", "config_error"):
        out["hint"] = {
            "partial": "일부 배치가 실패했습니다(LLM 오류/빈 응답). 같은 설정으로 다시 실행하면 실패한 배치만 재시도합니다.",
            "config_error": "설정/입출력 오류입니다. LLM 백엔드가 설정돼 있는지, 로그를 확인하세요.",
            "failed": "비정상 종료했습니다. 로그를 확인하세요.",
        }[status]
    if detail:
        out["log_tail"] = _tail(_p(run_id, ".log"))
        if result:
            out["empty_samples"] = result.get("empty_samples")
            out["model"] = result.get("model")
    return out


def list_runs() -> list[dict[str, Any]]:
    d = runs_dir()
    if not d.is_dir():
        return []
    ids = sorted((p.name[: -len(".meta.json")] for p in d.glob("adm-*.meta.json")), reverse=True)
    out = []
    for rid in ids[:_LIST_LIMIT]:
        try:
            out.append(describe(rid))
        except RunError:
            continue
    return out


def _active() -> str | None:
    for r in list_runs():
        if r["status"] == "running":
            return r["run_id"]
    return None


def _clamp(v: float | int | None, lo: float, hi: float, name: str):
    if v is None:
        return None
    if not lo <= v <= hi:
        raise RunError(422, f"{name}은(는) {lo:g}~{hi:g} 범위여야 합니다.")
    return v


def start(*, since_years: float = 2.0, limit_tickets: int | None = None, batch_size: int = 4,
          max_batches: int | None = None, commit: bool = False, split_on_empty: bool = True,
          min_terms_per_ticket: int = 1, stop_after_empty: int = 3, started_by: str = "?", run_id: str | None = None) -> dict[str, Any]:
    since_years = _clamp(since_years, 0.1, 10, "since_years")
    limit_tickets = _clamp(limit_tickets, 1, 100000, "limit_tickets")
    batch_size = _clamp(batch_size, 1, 500, "batch_size")
    max_batches = _clamp(max_batches, 1, 10000, "max_batches")
    if min_terms_per_ticket not in (1, 2):
        raise RunError(422, "min_terms_per_ticket은 1 또는 2여야 합니다.")
    stop_after_empty = int(_clamp(stop_after_empty, 0, 50, "stop_after_empty") or 0)
    d = runs_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".start.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active = _active()
        if active:
            raise RunError(409, f"이미 실행 중인 마이닝이 있습니다({active}). 끝나거나 중단한 뒤 시작하세요.")
        now = datetime.now(timezone.utc)
        base = run_id or "adm-" + now.strftime("%Y%m%d-%H%M%S")
        rid, n = base, 1
        while _p(rid, ".meta.json").exists():
            n += 1
            rid = f"{base}-{n}"
        out_path = _p(rid, ".json")
        argv = [sys.executable, "-m", CLI_MODULE, "mine", "--since-years", str(since_years),
                "--batch-size", str(batch_size), "--run-id", rid, "--out", str(out_path),
                "--min-terms-per-ticket", str(min_terms_per_ticket), "--stop-after-empty", str(stop_after_empty)]
        if limit_tickets:
            argv += ["--limit-tickets", str(int(limit_tickets))]
        if max_batches:
            argv += ["--max-batches", str(int(max_batches))]
        if split_on_empty:
            argv.append("--split-on-empty")
        if commit:
            argv.append("--commit")
        exit_file = _p(rid, ".exit")
        stdout_f = open(_p(rid, ".stdout.json"), "wb")
        log_f = open(_p(rid, ".log"), "wb")
        # `"$@"; echo $? > exitfile` records the exit code even if the api process is gone by then.
        proc = subprocess.Popen(
            ["/bin/sh", "-c", '"$@"; echo $? > "$0"', str(exit_file), *argv],
            stdout=stdout_f, stderr=log_f, stdin=subprocess.DEVNULL, start_new_session=True,
        )
        stdout_f.close()
        log_f.close()
        meta = {"run_id": rid, "pid": proc.pid, "started_at": now.isoformat(timespec="seconds"), "started_by": started_by,
                "args": {"since_years": since_years, "limit_tickets": limit_tickets, "batch_size": batch_size,
                         "max_batches": max_batches, "commit": commit, "split_on_empty": split_on_empty,
                         "min_terms_per_ticket": min_terms_per_ticket, "stop_after_empty": stop_after_empty}}
        _p(rid, ".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    threading.Thread(target=proc.wait, daemon=True).start()  # reap so a finished run isn't a zombie
    return describe(rid)


def cancel(run_id: str) -> dict[str, Any]:
    meta = _read_json(_p(run_id, ".meta.json"))
    if not isinstance(meta, dict):
        raise RunError(404, "run not found")
    status, _ = _status(run_id, meta)
    if status != "running":
        raise RunError(409, f"실행 중이 아닙니다({status}).")
    meta["cancelled"] = True
    _p(run_id, ".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    try:  # _status() just verified the pid belongs to this run, so the group is ours
        os.killpg(os.getpgid(meta["pid"]), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    return describe(run_id)


def import_result(run_id: str) -> dict[str, Any]:
    """Import a finished run's items as 'proposed' (what `cli import` does) — for dry-run results."""
    from app.synonyms import store

    meta = _read_json(_p(run_id, ".meta.json"))
    if not isinstance(meta, dict):
        raise RunError(404, "run not found")
    status, _ = _status(run_id, meta)
    if status == "running":
        raise RunError(409, "실행 중에는 가져올 수 없습니다.")
    result = _read_json(_p(run_id, ".json"))
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise RunError(409, "가져올 결과 파일이 없습니다(실행이 결과를 쓰기 전에 끝났을 수 있습니다).")
    res = store.upsert_candidates(result["items"], run_id=result.get("run_id") or run_id, model=result.get("model"))
    meta["imported"] = res
    _p(run_id, ".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return res
