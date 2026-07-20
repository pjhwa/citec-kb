"""Match war-room seed bundles to a free-text symptom."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _seed_dirs() -> list[Path]:
    here = Path(__file__).resolve()
    dirs = [Path("/app/data/seeds/bundles"), Path.cwd() / "data/seeds/bundles"]
    for p in here.parents:
        dirs.append(p / "data" / "seeds" / "bundles")
        if len(dirs) > 10:
            break
    return dirs


def load_bundles() -> list[dict[str, Any]]:
    for d in _seed_dirs():
        if d.is_dir():
            items = []
            for p in sorted(d.glob("*.json")):
                items.append(json.loads(p.read_text(encoding="utf-8")))
            if items:
                return items
    return []


def match_bundles(symptom: str, *, top_k: int = 2) -> list[dict[str, Any]]:
    """Score bundles by symptom_hint / component keyword overlap."""
    text = (symptom or "").strip()
    if not text:
        return []
    low = text.lower()
    scored: list[tuple[float, dict[str, Any]]] = []
    for b in load_bundles():
        hints = b.get("symptom_hints") or []
        comps = b.get("related_components") or []
        hit_hints = [h for h in hints if str(h).lower() in low or str(h) in text]
        hit_comps = [c for c in comps if str(c).lower() in low or str(c) in text]
        if not hit_hints and not hit_comps:
            # fuzzy: any hint token as word
            for h in hints:
                if re.search(re.escape(str(h)), text, re.I):
                    hit_hints.append(h)
        score = 0.4 * len(hit_hints) + 0.25 * len(hit_comps)
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "id": b.get("id"),
                    "name": b.get("name"),
                    "title": b.get("title"),
                    "score": round(score, 3),
                    "matched_hints": hit_hints,
                    "matched_components": hit_comps,
                    "checklist": b.get("checklist") or [],
                    "commands": b.get("commands") or [],
                },
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:top_k]]
