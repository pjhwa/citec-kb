"""Lexicon term seed + in-memory synonym map for FTS expansion."""

from __future__ import annotations

import json
import logging
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
    # cache_clear() 호출이 더 이상 필요 없다 — load_lexicon_map()이 이제
    # DB 자체의 (count, max(updated_at)) 지문으로 캐시를 무효화한다(G06 잔여,
    # 아래 참고). 이 프로세스뿐 아니라 다른 API 워커 프로세스도 다음 호출에서
    # 자동으로 새 lexicon을 본다 — 과거엔 cache_clear()가 "이 프로세스"에만
    # 적용돼 여러 워커 간 동기화가 안 됐다.
    return {
        "upserted": n,
        "total": int(total),
        "loaded_from": data.get("_loaded_from"),
        "llm_used": False,
    }


def build_lexicon_map(terms: list[dict[str, Any]]) -> dict[str, list[str]]:
    """순수 함수 — [{"canonical","variants"},...] -> 소문자 토큰 -> [canonical,
    변형어...] 맵(variants[0]이 항상 canonical). DB 행이든 seed 파일 행이든
    같은 모양(dict with "canonical"/"variants")이라 둘 다 이 함수 하나로
    처리한다(G11 — load_lexicon_map()이 이 로직을 세 군데에 중복해서 갖고
    있던 것을 통합, DB 없이도 실제 core.json으로 테스트 가능해짐)."""
    mapping: dict[str, list[str]] = {}
    for t in terms:
        can = str(t["canonical"])
        variants = [can] + list(t.get("variants") or [])
        for v in variants:
            key = v.lower()
            mapping.setdefault(key, [])
            for x in variants:
                if x not in mapping[key]:
                    mapping[key].append(x)
    return mapping


_lexicon_cache: dict[str, Any] = {"fingerprint": None, "map": None}


def _lexicon_fingerprint(session) -> str:
    count, max_updated = session.execute(
        select(func.count(), func.max(LexiconTerm.updated_at))
    ).one()
    return f"{count}:{max_updated}"


def load_lexicon_map() -> dict[str, list[str]]:
    """Map lowercased token -> variants (includes canonical).

    G06(이번 라운드 잔여) — 과거 @lru_cache(maxsize=1)는 프로세스 전역이라,
    API 워커가 여러 개면 한 워커에서 seed_lexicon()을 돌려도 다른 워커는
    재시작 전까지 옛 lexicon을 계속 쓴다(cache_clear()가 그 프로세스에만
    적용됨). 이제 매 호출마다 DB의 (count, max(updated_at)) 지문을 싼 쿼리로
    확인하고, 지문이 바뀌었을 때만 실제 맵을 다시 만든다 — 모든 워커가
    자기 프로세스 캐시를 각자 자동으로 무효화한다(명시적 cache_clear() 호출
    없이도 동작)."""
    try:
        with session_scope() as session:
            fingerprint = _lexicon_fingerprint(session)
            if (
                _lexicon_cache["map"] is not None
                and _lexicon_cache["fingerprint"] == fingerprint
            ):
                return _lexicon_cache["map"]
            rows = list(session.scalars(select(LexiconTerm)).all())
            if rows:
                mapping = build_lexicon_map(
                    [{"canonical": r.canonical, "variants": list(r.variants or [])} for r in rows]
                )
                _lexicon_cache["fingerprint"] = fingerprint
                _lexicon_cache["map"] = mapping
                return mapping
    except Exception:  # noqa: BLE001 — DB 없이도(CI 등) seed 파일로 degrade
        pass
    return build_lexicon_map(load_lexicon_seed_file().get("terms") or [])


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--no-replace", action="store_true")
    args = p.parse_args()
    print(json.dumps(seed_lexicon(replace=not args.no_replace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
