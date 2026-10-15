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


def _merge_variants(existing: list[str], seed: list[str]) -> list[str]:
    """R03(2026-10-13 2차 핸드오프) §1/6 — 보존형 upsert는 기존 variants를
    지우지 않고 seed variants를 union으로 더한다(수동으로 추가된 별칭이
    seed 재실행으로 사라지는 걸 막는다)."""
    merged = list(existing)
    for v in seed:
        if v not in merged:
            merged.append(v)
    return merged


def _plan_term_change(row: "LexiconTerm | None", seed_term: dict[str, Any], *, replace: bool) -> dict[str, Any]:
    """F03(2026-10-13 최종 핸드오프) — dry-run과 실제 apply가 반드시 같은
    답을 내도록, "이 행이 어떤 상태가 돼야 하는가"를 한 곳에서만 계산한다.
    과거엔 이 로직이 두 군데(dry-run 비교 블록과 apply 쓰기 블록)에 따로
    있어서 서로 어긋났다 — 특히 dry-run은 variants/priority만 비교해
    metadata 변경을 전혀 못 봤는데, apply는 매번
    `row.metadata_ = dict(t.get("metadata") or {})`로 **항상** 덮어써서,
    seed 파일에 metadata 키가 없는(실제로 늘 이렇다) canonical은 매 실행마다
    수동 metadata가 조용히 빈 dict로 지워지는 실제 버그였다(실제 재현:
    comparison/raw/local_source_probes.json).

    "소유권이 불명확한 기존 필드는 기본 보존하라"는 요구에 따라, metadata는
    variants와 같은 철학으로 다룬다 — seed가 명시한 키는 기본값으로
    제공하되, 이미 DB에 있는(수동으로 설정됐을 수 있는) 키가 있으면 그
    값이 이긴다. replace=True(명시적 파괴 옵션)일 때만 seed 쪽이 전부
    이긴다 — variants/삭제가 이미 그렇게 동작하는 것과 동일한 정책."""
    seed_variants = list(seed_term.get("variants") or [])
    seed_priority = int(seed_term.get("priority") or 100)
    seed_metadata = dict(seed_term.get("metadata") or {})

    if row is None:
        return {
            "action": "add",
            "target_variants": seed_variants, "target_priority": seed_priority,
            "target_metadata": seed_metadata, "changed_fields": [],
        }

    existing_variants = list(row.variants or [])
    existing_metadata = dict(row.metadata_ or {})

    target_variants = seed_variants if replace else _merge_variants(existing_variants, seed_variants)
    target_priority = seed_priority
    target_metadata = seed_metadata if replace else {**seed_metadata, **existing_metadata}

    changed_fields: list[str] = []
    if set(target_variants) != set(existing_variants):
        changed_fields.append("variants")
    if target_priority != row.priority:
        changed_fields.append("priority")
    if target_metadata != existing_metadata:
        changed_fields.append("metadata")

    return {
        "action": "change" if changed_fields else "noop",
        "target_variants": target_variants, "target_priority": target_priority,
        "target_metadata": target_metadata, "changed_fields": changed_fields,
    }


def seed_lexicon(*, replace: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """replace=True는 seed 파일에 없는 기존 항목(공용 DB에 수동 추가된 별칭 등)을
    먼저 전부 지운다 — G06: 공용 DB에서 무심코 실행하면 안 되므로 기본값을 False로
    바꿨다(과거 기본값 True 때문에 운영에서 10건이 삭제된 사고가 있었다).

    replace=False(기본)는 세 필드 모두 보존형이다(R03/F03 — 과거엔 "False도
    같은 canonical의 variants를 seed로 통째로 덮어씀", 그리고 "metadata는
    항상 덮어씀"이 실제 버그였다):
    1. seed에 없는 canonical 자체는 건드리지 않는다(기존 동작).
    2. seed에 '있는' canonical이어도, variants는 겹쳐쓰지 않고 기존 값과
       union 병합한다 — 수동으로 추가된 별칭이 유지된다.
    3. metadata도 같은 철학 — seed가 명시한 키는 기본값, 이미 DB에 있는
       키는 그 값이 이긴다(소유권 불명확한 필드는 보존). priority는 여전히
       seed가 관리하는 설정값으로 본다(수동 편집 대상이 아니라고 가정).

    dry_run과 apply는 _plan_term_change() 하나를 공유한다(F03.4) — 둘이
    서로 다른 답을 낼 수 없다. dry_run=True면 아무것도 쓰지 않고 추가/
    변경(필드별 diff 포함)/삭제(= replace일 때만) 예상 canonical 목록만
    돌려준다."""
    data = load_lexicon_seed_file()
    terms = [t for t in (data.get("terms") or []) if str(t.get("canonical") or "").strip()]
    seed_canonicals = {str(t["canonical"]).strip() for t in terms}

    with session_scope() as session:
        # F03(2026-10-13 최종 핸드오프) §2.3.5/8 — "dry-run 시점 이후 데이터가
        # 바뀌면... 오래된 계획을 그대로 덮어쓰지 말라", "여러 worker·batch의
        # 사전 revision과 갱신을 검증하라". dry_run=False(실제 적용)일 때만
        # FOR UPDATE로 이 테이블 전체 행을 잠근다 — seed는 드물게 도는 배치
        # 작업이라 전체 잠금 비용이 낮고, 두 번째 호출의 SELECT 자체가 첫
        # 번째 호출의 COMMIT까지 블록돼 "오래된 읽기 위에 쓰기"를 구조적으로
        # 막는다(두 번째 호출은 블록이 풀리는 순간 첫 번째가 이미 커밋한
        # 최신 값을 읽어 그 위에 병합한다 — lost update 불가능). dry_run은
        # 쓰지 않으므로 잠그지 않는다(다른 dry-run/apply를 막을 이유가 없음).
        existing_query = select(LexiconTerm)
        if not dry_run:
            existing_query = existing_query.with_for_update()
        existing_rows = {r.canonical: r for r in session.scalars(existing_query).all()}

        would_add: list[str] = []
        would_change: list[str] = []
        change_fields: dict[str, list[str]] = {}
        for t in terms:
            can = str(t["canonical"]).strip()
            row = existing_rows.get(can)
            plan = _plan_term_change(row, t, replace=replace)
            if plan["action"] == "add":
                would_add.append(can)
            elif plan["action"] == "change":
                would_change.append(can)
                change_fields[can] = plan["changed_fields"]
        would_remove = sorted(set(existing_rows) - seed_canonicals) if replace else []

        if dry_run:
            return {
                "dry_run": True,
                "would_add": sorted(would_add), "would_change": sorted(would_change),
                "would_remove": would_remove, "loaded_from": data.get("_loaded_from"),
                # F03.4 — metadata-only 변경도 이 필드별 diff로 드러난다
                # (would_change에만 있으면 "뭐가 바뀌는지"가 안 보였다).
                "change_fields": change_fields,
            }

        if replace:
            session.execute(delete(LexiconTerm))
            existing_rows = {}

        n = 0
        for t in terms:
            can = str(t["canonical"]).strip()
            row = existing_rows.get(can)
            plan = _plan_term_change(row, t, replace=replace)
            if row is None:
                row = LexiconTerm(canonical=can)
                session.add(row)
            row.variants = plan["target_variants"]
            row.priority = plan["target_priority"]
            row.metadata_ = plan["target_metadata"]
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
    owner_canonical: dict[str, str] = {}
    for t in terms:
        can = str(t["canonical"])
        variants = [can] + list(t.get("variants") or [])
        for v in variants:
            key = v.lower()
            owner = owner_canonical.get(key)
            if owner is not None and owner != can:
                # R03(2026-10-13 2차 핸드오프) §5 — 같은 변형어가 서로 다른
                # canonical에 걸치면 어느 쪽이 resolve_component_anchor의
                # variants[0](대표)이 되는지가 입력 순서에 좌우된다. 조용히
                # 첫 값으로 확정하지 않고 최소한 로그로 남긴다.
                logger.warning(
                    "lexicon alias collision: variant=%r는 canonical=%r와 %r에 둘 다 속함 "
                    "— 먼저 등록된 쪽이 대표로 쓰임", v, owner, can,
                )
            else:
                owner_canonical.setdefault(key, can)
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
            # R03 §5 — canonical 오름차순으로 고정해 alias 충돌(둘 이상의
            # canonical이 같은 variant를 공유)이 생겨도 어느 쪽이 대표가
            # 되는지가 DB 반환 순서(원래는 보장 안 됨)에 좌우되지 않는다.
            rows = list(session.scalars(select(LexiconTerm).order_by(LexiconTerm.canonical)).all())
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
    # R03(2026-10-13 2차 핸드오프) §1 실제 재현: 과거 기본값이
    # `replace=not args.no_replace`라 `--no-replace`를 안 주면 항상
    # replace=True(전체 삭제 후 재삽입)로 호출됐다 — seed_lexicon()의
    # 함수 기본값(False)과 CLI 기본 동작이 서로 달랐다. 이제 CLI도
    # 기본값이 보존형(False)이고, 파괴적 교체는 --replace로 명시해야만
    # 실행된다(모든 진입점 기본 동작 통일, R03.1).
    p.add_argument("--replace", action="store_true", help="seed 파일에 없는 기존 항목까지 지우고 재삽입(파괴적, 기본 꺼짐)")
    p.add_argument("--dry-run", action="store_true", help="쓰지 않고 추가/변경/삭제 예상 canonical만 출력")
    args = p.parse_args()
    print(json.dumps(
        seed_lexicon(replace=args.replace, dry_run=args.dry_run), ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
