"""Admin-editable topic-scan settings (override file under data_dir).

Default: `<config_dir>/decision_scan.json` (git-tracked, read-only in the api container) merged over
`scan.DEFAULTS`. The first admin edit writes `<data_dir>/decision_scan.json`, which then wins.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.settings import get_settings

FILENAME = "decision_scan.json"
DETECTORS = ("policy", "fb_confirm", "deadline", "bucket_gap", "cause_conflict")
_INT_RANGES = {"max_new_per_run": (0, 200), "max_open_server_proposals": (0, 500), "gap_since_days": (1, 3650),
               "gap_min_count": (1, 1000), "deadline_soon_days": (0, 3650), "llm_max_per_run": (0, 100), "llm_workers": (1, 8),
               "gap_max_groups_per_domain": (1, 20), "gap_max_incidents": (5, 200), "gap_max_group_size": (3, 50), "cause_conflict_max": (1, 50)}


class ScanConfigError(ValueError):
    pass


def _override_path() -> Path:
    return Path(get_settings().data_dir) / FILENAME


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a broken file must never block the scan
        return {}


def effective(defaults: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(defaults)
    cfg.update(_read(Path(get_settings().config_dir) / FILENAME))
    cfg.update({k: v for k, v in _read(_override_path()).items() if k in defaults})
    return cfg


def snapshot(defaults: dict[str, Any]) -> dict[str, Any]:
    ovr = _read(_override_path()) if _override_path().exists() else {}
    return {"config": {k: effective(defaults)[k] for k in defaults}, "source": "override" if ovr else "config",
            "updated_by": ovr.get("updated_by"), "updated_at": ovr.get("updated_at")}


def validate(patch: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in patch.items():
        if k not in defaults:
            raise ScanConfigError(f"알 수 없는 설정: {k}")
        if k in _INT_RANGES:
            lo, hi = _INT_RANGES[k]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ScanConfigError(f"{k}는 {lo}..{hi} 정수여야 합니다")
        elif k == "llm_refine":
            if not isinstance(v, bool):
                raise ScanConfigError("llm_refine은 true/false여야 합니다")
        elif k == "detectors":
            if not isinstance(v, list) or any(d not in DETECTORS for d in v):
                raise ScanConfigError(f"detectors는 {', '.join(DETECTORS)} 중에서 고르세요")
            v = [d for d in DETECTORS if d in v]
        out[k] = v
    return out


def update(patch: dict[str, Any], defaults: dict[str, Any], updated_by: str) -> dict[str, Any]:
    clean = validate(patch, defaults)
    path = _override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(FILENAME + ".lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        cfg = {k: effective(defaults)[k] for k in defaults}
        cfg.update(clean)
        cfg.update({"updated_by": updated_by,
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return snapshot(defaults)
