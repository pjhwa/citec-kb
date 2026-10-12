"""Lexicon term seed + in-memory synonym map for FTS expansion."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from app.db.models import LexiconTerm
from app.db.session import session_scope

logger = logging.getLogger("citec.lexicon")

_EMBEDDED = {
    "terms": [
        {"canonical": "GRO", "variants": ["rx-gro-hw", "generic receive offload", "hardware offload"], "priority": 10},
        {"canonical": "Linux", "variants": ["리눅스", "linux", "PISAOLNX"], "priority": 20},
        {"canonical": "filesystem", "variants": ["파일시스템", "파일 시스템", "FS"], "priority": 20},
        {"canonical": "Redis", "variants": ["레디스", "redis"], "priority": 15},
        {"canonical": "monimo", "variants": ["모니모", "Monimo"], "priority": 10},
        {"canonical": "timeout", "variants": ["타임아웃", "timed out"], "priority": 30},
        {"canonical": "hang", "variants": ["hung", "soft lockup", "행"], "priority": 25},
    ]
}


def _paths() -> list[Path]:
    here = Path(__file__).resolve()
    out = [
        Path("/app/data/seeds/lexicon/core.json"),
        Path("data/seeds/lexicon/core.json"),
    ]
    for i, p in enumerate(here.parents):
        if i > 6:
            break
        out.append(p / "data" / "seeds" / "lexicon" / "core.json")
    return out


def load_lexicon_seed_file() -> dict[str, Any]:
    for p in _paths():
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                data["_loaded_from"] = str(p)
                return data
        except OSError:
            continue
    d = dict(_EMBEDDED)
    d["_loaded_from"] = "embedded"
    return d


def seed_lexicon(*, replace: bool = False) -> dict[str, Any]:
    """replace=True는 seed 파일에 없는 기존 항목(공용 DB에 수동 추가된 별칭 등)을
    먼저 전부 지운다 — G06: 공용 DB에서 무심코 실행하면 안 되므로 기본값을 False로
    바꿨다(과거 기본값 True 때문에 운영에서 10건이 삭제된 사고가 있었다). False는
    seed 파일의 canonical만 upsert하고 다른 항목은 건드리지 않는다."""
    data = load_lexicon_seed_file()
    terms = data.get("terms") or []
    n = 0
    with session_scope() as session:
        if replace:
            session.execute(delete(LexiconTerm))
        for t in terms:
            can = str(t["canonical"]).strip()
            if not can:
                continue
            existing = session.scalar(select(LexiconTerm).where(LexiconTerm.canonical == can))
            if existing is None:
                row = LexiconTerm(canonical=can)
                session.add(row)
            else:
                row = existing
            row.variants = list(t.get("variants") or [])
            row.priority = int(t.get("priority") or 100)
            row.metadata_ = dict(t.get("metadata") or {})
            n += 1
        session.flush()
        total = session.scalar(select(func.count()).select_from(LexiconTerm)) or 0
    load_lexicon_map.cache_clear()
    return {
        "upserted": n,
        "total": int(total),
        "loaded_from": data.get("_loaded_from"),
        "llm_used": False,
    }


@lru_cache(maxsize=1)
def load_lexicon_map() -> dict[str, list[str]]:
    """Map lowercased token -> variants (includes canonical)."""
    mapping: dict[str, list[str]] = {}
    try:
        with session_scope() as session:
            rows = list(session.scalars(select(LexiconTerm)).all())
            if not rows:
                # fall back to file
                for t in load_lexicon_seed_file().get("terms") or []:
                    can = str(t["canonical"])
                    variants = [can] + list(t.get("variants") or [])
                    for v in variants:
                        mapping.setdefault(v.lower(), [])
                        for x in variants:
                            if x not in mapping[v.lower()]:
                                mapping[v.lower()].append(x)
                return mapping
            for r in rows:
                variants = [r.canonical] + list(r.variants or [])
                for v in variants:
                    key = v.lower()
                    mapping.setdefault(key, [])
                    for x in variants:
                        if x not in mapping[key]:
                            mapping[key].append(x)
    except Exception:  # noqa: BLE001
        for t in load_lexicon_seed_file().get("terms") or []:
            can = str(t["canonical"])
            variants = [can] + list(t.get("variants") or [])
            for v in variants:
                mapping.setdefault(v.lower(), [])
                for x in variants:
                    if x not in mapping[v.lower()]:
                        mapping[v.lower()].append(x)
    return mapping


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-replace", action="store_true")
    args = p.parse_args()
    print(json.dumps(seed_lexicon(replace=not args.no_replace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
