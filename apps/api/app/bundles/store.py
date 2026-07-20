"""Load/save war-room bundles (JSON seed files)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from app.bundles.match import _seed_dirs, load_bundles


def _writable_dir() -> Path:
    for d in _seed_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return d
        except OSError:
            continue
    # last resort cwd
    d = Path("data/seeds/bundles")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9가-힣_-]+", "-", (name or "").strip()).strip("-").lower()
    return s or "bundle"


def normalize_bundle(body: dict[str, Any], *, name: Optional[str] = None) -> dict[str, Any]:
    n = name or body.get("name") or body.get("id") or "unnamed"
    n = str(n).replace("bundle:", "")
    bid = body.get("id") or f"bundle:{n}"
    if not str(bid).startswith("bundle:"):
        bid = f"bundle:{bid}"
    return {
        "id": bid,
        "name": n,
        "title": body.get("title") or n,
        "symptom_hints": list(body.get("symptom_hints") or []),
        "checklist": list(body.get("checklist") or []),
        "commands": list(body.get("commands") or []),
        "related_components": list(body.get("related_components") or []),
        "notes": body.get("notes") or "",
    }


def get_bundle(name: str) -> Optional[dict[str, Any]]:
    key = name.replace("bundle:", "")
    for b in load_bundles():
        if b.get("name") == key or b.get("id") == name or b.get("id") == f"bundle:{key}":
            return b
    return None


def save_bundle(body: dict[str, Any], *, name: Optional[str] = None) -> dict[str, Any]:
    b = normalize_bundle(body, name=name)
    d = _writable_dir()
    path = d / f"{_slug(b['name'])}.json"
    path.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "bundle": b}


def delete_bundle(name: str) -> bool:
    key = name.replace("bundle:", "")
    slug = _slug(key)
    deleted = False
    for d in _seed_dirs():
        path = d / f"{slug}.json"
        if path.is_file():
            try:
                path.unlink()
                deleted = True
            except OSError:
                pass
    return deleted
