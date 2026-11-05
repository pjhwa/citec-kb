"""Admin-editable system-registrant allowlist (override file under data_dir).

The git-tracked default lives in config/system_registrants.json (read-only in the api
container). The first admin edit copies the effective list into
`<data_dir>/system_registrants.json`; from then on the override wins.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.failure_buckets import service as svc
from app.settings import get_settings

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FILENAME = "system_registrants.json"


class RegistrantNameError(ValueError):
    pass


def _override_path() -> Path:
    return Path(get_settings().data_dir) / FILENAME


def _config_path() -> Path:
    return Path(get_settings().config_dir) / FILENAME


def validate_name(name: str) -> str:
    value = (name or "").strip()
    if not NAME_RE.match(value):
        raise RegistrantNameError(
            "시스템 이름은 소문자·숫자로 시작하고 [a-z0-9._-]만 쓸 수 있습니다(최대 64자). 예: insight-ops"
        )
    return value


def snapshot() -> dict[str, Any]:
    """Effective list plus where it came from."""
    ovr = _override_path()
    meta: dict[str, Any] = {}
    source = "config"
    if ovr.exists():
        try:
            data = json.loads(ovr.read_text(encoding="utf-8"))
            meta = {"updated_by": data.get("updated_by"), "updated_at": data.get("updated_at")}
            source = "override"
        except Exception:  # noqa: BLE001 — loader falls back to config in this case
            source = "config"
    return {
        "systems": sorted(svc._system_registrants()),
        "source": source,
        **meta,
    }


def _write(names: set[str], updated_by: str) -> None:
    path = _override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "systems": sorted(names),
        "updated_by": updated_by,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _mutate(updated_by: str, fn) -> dict[str, Any]:
    """Read-modify-write under an flock so concurrent admin edits do not lose updates."""
    lock = _override_path().with_name(FILENAME + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        names = set(svc._system_registrants())
        before = set(names)
        changed = fn(names)
        if changed:
            _write(names, updated_by)
    out = snapshot()
    out["changed"] = bool(changed)
    out["before_count"] = len(before)
    return out


def add(name: str, updated_by: str) -> dict[str, Any]:
    value = validate_name(name)

    def fn(names: set[str]) -> bool:
        if value in names:
            return False
        names.add(value)
        return True

    return _mutate(updated_by, fn)


def remove(name: str, updated_by: str) -> dict[str, Any]:
    value = (name or "").strip()

    def fn(names: set[str]) -> bool:
        if value not in names:
            return False
        names.discard(value)
        return True

    return _mutate(updated_by, fn)
