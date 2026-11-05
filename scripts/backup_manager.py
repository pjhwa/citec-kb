#!/usr/bin/env python3
"""backup_manager.py — host-side backup of citec-kb (Postgres dump + data/), with rotation and status.

One backup *set* is a directory under $BACKUP_DIR (default <repo>/data/backups):
    <YYYYmmdd_HHMMSS>-<scheduled|manual>/
        db.sql.gz       pg_dump of the Postgres container's database (plain SQL, --clean --if-exists)
        data.tar.gz     <repo>/data/ (raw files, seeds, synonym runs, admin overrides, hashed tokens),
                        EXCLUDING data/backups itself
        SHA256SUMS      checksums of the two files
        manifest.json   written last; a set without it is incomplete and never counted or restored
What is NOT backed up (by design): .env and config/auth.json (secrets — keep them in your secret store),
models/ (re-downloadable), Neo4j (rebuild with scripts/graph_sync.sh), logs/.

Commands
    run [--kind scheduled|manual] [--keep N]   make a set, verify it, then rotate (keeps the newest N, default 5)
    process-requests                           run a manual backup if the admin page left a request (cron, every minute)
    status                                     refresh <repo>/data/backup_status.json (the admin page reads it)
    verify [SET_ID]                            re-check checksums / gzip / tar listing of a set (default: newest)

Cron (host crontab):
    30 3 * * * /usr/bin/flock -n /tmp/citec-kb-backup.lock <repo>/scripts/backup_manager.py run >> <repo>/logs/cron_backup.log 2>&1
    * * * * *  <repo>/scripts/backup_manager.py process-requests >> <repo>/logs/cron_backup_requests.log 2>&1

Env: BACKUP_DIR, BACKUP_KEEP, BACKUP_MIN_FREE_GB (default 2), BACKUP_DUMP_CMD (tests only: shell command that writes SQL to stdout).
Exit codes: 0 ok (or nothing to do), 1 backup failed, 2 usage.  Standard library only.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATUS_FILE = DATA / "backup_status.json"
REQ_DIR = DATA / "backup_requests"
SET_RE = re.compile(r"^\d{8}_\d{6}-(scheduled|manual)$")
DUMP_DONE_MARK = b"PostgreSQL database dump complete"
DEFAULT_KEEP = 5
KEEP_REQUESTS = 20


def backup_dir() -> Path:
    return Path(os.environ.get("BACKUP_DIR") or DATA / "backups")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}", flush=True)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_bytes(path: Path, skip: Path | None = None) -> tuple[int, int]:
    """(total bytes, file count) of a tree, skipping `skip`; unreadable entries are ignored."""
    total = n = 0
    for dp, dns, fns in os.walk(path):
        if skip is not None:
            dns[:] = [d for d in dns if Path(dp, d) != skip]
        for f in fns:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
                n += 1
            except OSError:
                pass
    return total, n


# ---------------------------------------------------------------- sets / status

def list_sets(bdir: Path) -> list[dict]:
    out = []
    if not bdir.is_dir():
        return out
    for d in sorted(bdir.iterdir(), reverse=True):
        if not (d.is_dir() and SET_RE.match(d.name)):
            continue
        m = read_json(d / "manifest.json")
        if isinstance(m, dict):
            out.append(m)
    return out


def refresh_status(keep: int | None = None) -> dict:
    bdir = backup_dir()
    keep = keep or int(os.environ.get("BACKUP_KEEP") or DEFAULT_KEEP)
    sets = list_sets(bdir)
    probe = bdir if bdir.exists() else (bdir.parent if bdir.parent.exists() else ROOT)
    du = shutil.disk_usage(probe)
    stored = sum((s.get("db", {}).get("size", 0) + s.get("data", {}).get("size", 0)) for s in sets)
    status = {
        "version": 1, "generated_at": now_iso(), "host": socket.gethostname(),
        "backup_dir": str(bdir), "keep": keep,
        "disk": {"total": du.total, "free": du.free, "used_pct": round(100 * (du.total - du.free) / du.total, 1)},
        "backups_bytes": stored,
        "sets": sets,
        "last_run": read_json(bdir / "last_run.json"),
    }
    write_json(STATUS_FILE, status)
    return status


# ---------------------------------------------------------------- backup

class BackupError(Exception):
    pass


def dump_db(dest: Path) -> dict:
    custom = os.environ.get("BACKUP_DUMP_CMD")
    cmd = custom if custom else [
        "docker", "compose", "exec", "-T", "postgres", "sh", "-c",
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --clean --if-exists',
    ]
    log("pg_dump 시작")
    proc = subprocess.Popen(cmd, shell=bool(custom), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with gzip.open(dest, "wb", compresslevel=6) as gz:
        shutil.copyfileobj(proc.stdout, gz, 1 << 20)
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    if proc.wait() != 0:
        raise BackupError(f"pg_dump 실패(exit {proc.returncode}): {err[-400:] or '오류 출력 없음'}")
    # sanity: the dump must end with pg_dump's completion marker (a truncated stream would not)
    with gzip.open(dest, "rb") as gz:
        tail = b""
        for chunk in iter(lambda: gz.read(1 << 20), b""):
            tail = (tail + chunk)[-4096:]
    if DUMP_DONE_MARK not in tail:
        raise BackupError("덤프가 완료 표식 없이 끝났습니다(중간에 끊김). 디스크 여유와 postgres 상태를 확인하세요.")
    return {"file": dest.name, "size": dest.stat().st_size, "sha256": sha256_of(dest), "complete": True}


def archive_data(dest: Path) -> dict:
    bdir = backup_dir().resolve()
    log("data/ 압축 시작")
    # `tar` exit 1 = "file changed as we read it" (ingest may be writing): acceptable; >=2 is a failure.
    cmd = ["tar", "-czf", str(dest), "--warning=no-file-changed", f"--exclude=data/backups", "--exclude=data/backup_requests",
           "--exclude=data/backup_status.json", "--exclude=data/*.tmp"]
    if bdir.is_relative_to(DATA.resolve()):
        cmd.append(f"--exclude=data/{bdir.relative_to(DATA.resolve())}")
    cmd += ["-C", str(ROOT), "data"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode >= 2:
        raise BackupError(f"tar 실패(exit {r.returncode}): {r.stderr.strip()[-300:]}")
    with tarfile.open(dest, "r:gz") as tf:  # listing the whole archive proves it is readable end to end
        n = sum(1 for _ in tf)
    return {"file": dest.name, "size": dest.stat().st_size, "sha256": sha256_of(dest), "entries": n,
            "changed_while_reading": r.returncode == 1}


def estimate_need(bdir: Path) -> int:
    data_bytes, _ = dir_bytes(DATA, skip=bdir.resolve() if bdir.resolve().is_relative_to(DATA.resolve()) else None)
    return int(data_bytes * 1.1) + int(float(os.environ.get("BACKUP_MIN_FREE_GB") or 2) * (1 << 30))


def rotate(bdir: Path, keep: int) -> list[str]:
    removed = []
    for m in list_sets(bdir)[keep:]:
        d = bdir / m["id"]
        if SET_RE.match(d.name) and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(m["id"])
    for p in bdir.glob(".partial-*"):  # crashed runs
        if time.time() - p.stat().st_mtime > 86400:
            shutil.rmtree(p, ignore_errors=True)
    return removed


def do_backup(kind: str, keep: int, requested_by: str | None = None) -> dict:
    bdir = backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    lock = open(bdir / ".lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("다른 백업이 실행 중 — 건너뜀")
        return {"skipped": True}
    started = now_iso()
    set_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"-{kind}"
    tmp = bdir / f".partial-{set_id}"
    result: dict = {"id": set_id, "kind": kind, "started_at": started, "ok": False}
    try:
        need, free = estimate_need(bdir), shutil.disk_usage(bdir).free
        if free < need:
            raise BackupError(f"디스크 여유 부족: 필요 약 {need >> 20}MB, 여유 {free >> 20}MB (BACKUP_MIN_FREE_GB로 조정)")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        db = dump_db(tmp / "db.sql.gz")
        data = archive_data(tmp / "data.tar.gz")
        (tmp / "SHA256SUMS").write_text(f"{db['sha256']}  db.sql.gz\n{data['sha256']}  data.tar.gz\n")
        manifest = {"version": 1, "id": set_id, "kind": kind, "started_at": started, "finished_at": now_iso(),
                    "host": socket.gethostname(), "requested_by": requested_by, "db": db, "data": data}
        write_json(tmp / "manifest.json", manifest)  # last: its presence marks the set complete
        os.rename(tmp, bdir / set_id)
        removed = rotate(bdir, keep)
        result.update(ok=True, finished_at=manifest["finished_at"], removed=removed,
                      size=db["size"] + data["size"], warnings=["백업 중 data/ 파일이 변경됨"] if data["changed_while_reading"] else [])
        log(f"백업 완료 {set_id} ({result['size'] >> 20}MB), 정리된 세트 {len(removed)}개")
    except BackupError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        result.update(error=str(exc), finished_at=now_iso())
        log(f"백업 실패: {exc}")
    except Exception as exc:  # noqa: BLE001 — never leave a half-written set behind
        shutil.rmtree(tmp, ignore_errors=True)
        result.update(error=f"{type(exc).__name__}: {exc}", finished_at=now_iso())
        log(f"백업 실패(예외): {exc}")
    finally:
        write_json(bdir / "last_run.json", result)
        refresh_status(keep)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return result


# ---------------------------------------------------------------- requests (admin page → host)

def process_requests(keep: int) -> int:
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    done_dir = REQ_DIR / "done"
    pending = sorted(p for p in REQ_DIR.glob("*.json"))
    if not pending:
        refresh_status(keep)
        return 0
    rc = 0
    # one run serves all pending requests (they asked for the same thing)
    first = pending[0]
    req = read_json(first) or {}
    req.update(status="running", started_at=now_iso())
    write_json(first, req)
    res = do_backup("manual", keep, requested_by=req.get("requested_by"))
    if res.get("skipped"):  # another backup holds the lock: leave the requests for the next minute
        req["status"] = "pending"
        write_json(first, req)
        return 0
    for p in pending:
        r = read_json(p) or {}
        r.update(status="done" if res.get("ok") else "failed", finished_at=now_iso(),
                 set_id=res.get("id") if res.get("ok") else None, error=res.get("error"))
        write_json(done_dir / p.name, r)
        p.unlink(missing_ok=True)
    for old in sorted(done_dir.glob("*.json"))[:-KEEP_REQUESTS]:
        old.unlink(missing_ok=True)
    return 0 if res.get("ok") else 1


# ---------------------------------------------------------------- verify

def verify(set_id: str | None) -> int:
    sets = list_sets(backup_dir())
    if not sets:
        print("백업 세트가 없습니다.")
        return 1
    m = next((s for s in sets if s["id"] == set_id), None) if set_id else sets[0]
    if not m:
        print(f"세트를 찾을 수 없습니다: {set_id}")
        return 1
    d = backup_dir() / m["id"]
    ok = True
    for key in ("db", "data"):
        f = d / m[key]["file"]
        good = f.is_file() and sha256_of(f) == m[key]["sha256"]
        ok &= good
        print(f"{'OK ' if good else 'BAD'} {key}: {f.name} sha256 {'일치' if good else '불일치/누락'}")
    try:
        with gzip.open(d / m["db"]["file"], "rb") as gz:
            tail = b""
            for chunk in iter(lambda: gz.read(1 << 20), b""):
                tail = (tail + chunk)[-4096:]
        good = DUMP_DONE_MARK in tail
    except OSError:
        good = False
    ok &= good
    print(f"{'OK ' if good else 'BAD'} db: 덤프 완료 표식 {'있음' if good else '없음'}")
    try:
        with tarfile.open(d / m["data"]["file"], "r:gz") as tf:
            n = sum(1 for _ in tf)
        print(f"OK  data: tar 목록 {n}개 항목 읽기 성공")
    except (OSError, tarfile.TarError) as exc:
        ok = False
        print(f"BAD data: tar 읽기 실패 {exc}")
    print("검증 " + ("통과" if ok else "실패") + f": {m['id']}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--kind", choices=["scheduled", "manual"], default="scheduled")
    r.add_argument("--keep", type=int, default=int(os.environ.get("BACKUP_KEEP") or DEFAULT_KEEP))
    p = sub.add_parser("process-requests"); p.add_argument("--keep", type=int, default=int(os.environ.get("BACKUP_KEEP") or DEFAULT_KEEP))
    sub.add_parser("status")
    v = sub.add_parser("verify"); v.add_argument("set_id", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        if a.keep < 1:
            print("--keep은 1 이상이어야 합니다.", file=sys.stderr)
            return 2
        res = do_backup(a.kind, a.keep)
        return 0 if res.get("ok") or res.get("skipped") else 1
    if a.cmd == "process-requests":
        return process_requests(max(1, a.keep))
    if a.cmd == "status":
        refresh_status()
        print(f"status 갱신: {STATUS_FILE}")
        return 0
    return verify(a.set_id)


if __name__ == "__main__":
    sys.exit(main())
