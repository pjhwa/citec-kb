#!/usr/bin/env python3
"""cron_snapshot.py — dump the host crontab and each job's last-run state to logs/cron_status.json.

The api container cannot see the host crontab, but ./logs is mounted into it, so the admin
page (GET /v1/ops/cron) reads the JSON this script writes.

Per job: schedule, command, log file (from the `>> file` redirect), log mtime/size, last
lines, whether the script is running now, the schedule's most recent expected run, and a
status:  running | ok | failed | missed | unknown
  missed  = the log was not touched since the most recent expected run (>10 min ago)
  failed  = an error marker (❌, Traceback, ERROR, 실패, 비정상 종료) in the log tail
  unknown = the job has no log redirect, so nothing can be said about it
Exit codes are not recorded by cron; "failed" is a log heuristic.
History: recent timestamped logs (`<prefix>_<STAMP>.log`) when the script writes them.

Cron example (every 5 minutes, from the project's crontab):
  */5 * * * * /home/citec/dev/citec-kb/scripts/cron_snapshot.py >> /home/citec/dev/citec-kb/logs/cron_snapshot.log 2>&1

Usage: scripts/cron_snapshot.py [--out FILE] [--crontab-file FILE] [--logs-dir DIR]
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
MACROS = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *",
}
ERROR_RE = re.compile(r"❌|Traceback|\bERROR\b|\bFATAL\b|실패|비정상 종료", re.IGNORECASE)
HISTORY_N = 5
MISSED_GRACE = timedelta(minutes=10)


def _expand(field: str, lo: int, hi: int) -> set[int] | None:
    """Cron field → value set; None for `*` (unrestricted)."""
    if field == "*":
        return None
    out: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            a, b = int(a_s), int(b_s)
        else:
            a = int(part)
            b = hi if step != 1 else a
        out.update(range(a, b + 1, step))
    return out


def parse_schedule(expr: str):
    f = expr.split()
    if len(f) != 5:
        return None
    try:
        minute = _expand(f[0], 0, 59)
        hour = _expand(f[1], 0, 23)
        dom = _expand(f[2], 1, 31)
        month = _expand(f[3], 1, 12)
        dow = _expand(f[4], 0, 7)
    except ValueError:
        return None
    if dow is not None:
        dow = {d % 7 for d in dow}
    return minute, hour, dom, month, dow


def matches(sched, t: datetime) -> bool:
    minute, hour, dom, month, dow = sched
    if minute is not None and t.minute not in minute:
        return False
    if hour is not None and t.hour not in hour:
        return False
    if month is not None and t.month not in month:
        return False
    cron_dow = (t.weekday() + 1) % 7  # Mon=1 … Sun=0
    dom_ok = dom is None or t.day in dom
    dow_ok = dow is None or cron_dow in dow
    if dom is not None and dow is not None:  # vixie cron: either field may match
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def prev_expected(sched, now: datetime, horizon_days: int = 45):
    t = now.replace(second=0, microsecond=0)
    for _ in range(horizon_days * 24 * 60):
        if matches(sched, t):
            return t
        t -= timedelta(minutes=1)
    return None


def parse_crontab(text: str) -> list[dict]:
    jobs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line):
            continue
        if line.startswith("@"):
            head, _, cmd = line.partition(" ")
            expr = MACROS.get(head.lower(), "")
            if not expr:  # @reboot etc.: no schedule to evaluate
                jobs.append({"schedule": head, "command": cmd.strip(), "sched": None})
                continue
        else:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            expr, cmd = " ".join(parts[:5]), parts[5]
        jobs.append({"schedule": expr if not line.startswith("@") else head, "command": cmd.strip(),
                     "sched": parse_schedule(expr)})
    return jobs


def tail_lines(path: Path, n: int = 30) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 16384))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [ln for ln in data.splitlines() if ln.strip()][-n:]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def script_of(cmd: str) -> Path | None:
    for tok in cmd.split():
        if tok.endswith((".sh", ".py")):
            p = Path(tok)
            return p if p.is_absolute() else PROJECT_DIR / p
    return None


def is_running(script: Path | None) -> bool:
    """True when a process is executing the script (as argv[0], or argv[1] of a shell/python)."""
    if script is None:
        return False
    try:
        r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    me = str(os.getpid())
    target = str(script)
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] == me:
            continue
        argv = parts[1:]
        if argv[0] == target:
            return True
        if len(argv) > 1 and Path(argv[0]).name in ("bash", "sh", "python", "python3") and argv[1] == target:
            return True
    return False


def history_for(script: Path | None, logs_dir: Path) -> list[dict]:
    if script is None or not script.is_file():
        return []
    try:
        src = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    prefixes = sorted(set(re.findall(r"\$\{?LOG_DIR\}?/([A-Za-z0-9_]+)_\$\{?STAMP\}?\.log", src)))
    files: list[Path] = []
    for pre in prefixes:
        files += [p for p in logs_dir.glob(f"{pre}_*.log") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:HISTORY_N]:
        st = p.stat()
        tail = tail_lines(p, 20)
        out.append({
            "file": p.name, "mtime": iso(st.st_mtime), "size": st.st_size,
            "last_line": tail[-1][:300] if tail else "",
            "error": any(ERROR_RE.search(ln) for ln in tail),
        })
    return out


def build(crontab_text: str, logs_dir: Path, now: datetime) -> dict:
    now = now.astimezone()  # cron schedules are in the host's local time
    jobs_out = []
    for j in parse_crontab(crontab_text):
        cmd = j["command"]
        m = re.search(r">>?\s*(\S+)", cmd)
        log = Path(m.group(1)) if m else None
        script = script_of(cmd)
        entry = {
            "schedule": j["schedule"], "command": cmd,
            "script": script.name if script else None,
            "log_file": str(log) if log else None,
            "log_mtime": None, "log_size": None, "last_lines": [], "history": history_for(script, logs_dir),
        }
        error = False
        if log is not None and log.is_file():
            st = log.stat()
            entry["log_mtime"], entry["log_size"] = iso(st.st_mtime), st.st_size
            tail = tail_lines(log)
            entry["last_lines"] = [ln[:300] for ln in tail[-3:]]
            error = any(ERROR_RE.search(ln) for ln in tail)
            mtime = datetime.fromtimestamp(st.st_mtime).astimezone()
        else:
            mtime = None
        expected = prev_expected(j["sched"], now) if j["sched"] else None
        entry["expected_last_run"] = expected.astimezone().isoformat(timespec="seconds") if expected else None
        running = is_running(script)
        entry["running"] = running
        if running:
            status = "running"
        elif expected and now - expected > MISSED_GRACE and (mtime is None or mtime < expected - timedelta(seconds=60)):
            status = "missed" if log is not None else "unknown"
        elif log is None:
            status = "unknown"
        elif error:
            status = "failed"
        elif mtime is None:
            status = "unknown"
        else:
            status = "ok"
        entry["status"] = status
        jobs_out.append(entry)
    return {"generated_at": now.astimezone().isoformat(timespec="seconds"), "host": os.uname().nodename,
            "jobs": jobs_out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs-dir", default=str(PROJECT_DIR / "logs"))
    ap.add_argument("--out", default=None, help="default: <logs-dir>/cron_status.json")
    ap.add_argument("--crontab-file", default=None, help="read this file instead of `crontab -l` (tests)")
    a = ap.parse_args()
    logs_dir = Path(a.logs_dir)
    if a.crontab_file:
        text = Path(a.crontab_file).read_text(encoding="utf-8")
    else:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        text = r.stdout if r.returncode == 0 else ""  # "no crontab for user" → empty list
    snap = build(text, logs_dir, datetime.now(timezone.utc))
    out = Path(a.out) if a.out else logs_dir / "cron_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    print(f"[{snap['generated_at']}] cron_snapshot: {len(snap['jobs'])} jobs → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
