"""DB-touching tests for app.lexicon.seed — version-keyed load_lexicon_map()
cache (G06 잔여: 과거 @lru_cache는 프로세스 전역이라 여러 API 워커 간
seed_lexicon() 후 동기화가 안 됐다).

Skipped unless GRAPH_TEST_DATABASE_URL points at a reachable scratch
Postgres DB with the alembic schema applied — never the live citec_knowledge DB.
"""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DSN = os.environ.get("GRAPH_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set GRAPH_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings
    import app.lexicon.seed as seed_module

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    seed_module._lexicon_cache["fingerprint"] = None
    seed_module._lexicon_cache["map"] = None
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    seed_module._lexicon_cache["fingerprint"] = None
    seed_module._lexicon_cache["map"] = None


def test_seed_lexicon_default_preserves_manually_added_variant(monkeypatch):
    """R03(2026-10-13 2차 핸드오프) §1/3 실제 재현 — 과거엔 replace=False라도
    seed에 '있는' canonical의 variants를 통째로 덮어써서, 수동으로 추가한
    별칭이 사라졌다. 이제는 union 병합이라 보존돼야 한다."""
    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    canonical = f"TestCan-{uuid.uuid4().hex[:8]}"
    manual_variant = f"manual-{uuid.uuid4().hex[:8]}"
    seed_variant = f"seed-{uuid.uuid4().hex[:8]}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(canonical=canonical, variants=[manual_variant], priority=100))

        monkeypatch.setattr(
            seed_module, "load_lexicon_seed_file",
            lambda: {"terms": [{"canonical": canonical, "variants": [seed_variant], "priority": 100}],
                     "_loaded_from": "test"},
        )
        result = seed_module.seed_lexicon(replace=False)
        assert result["upserted"] == 1

        with session_scope() as session:
            row = session.query(LexiconTerm).filter_by(canonical=canonical).one()
            assert manual_variant in row.variants  # 보존됨
            assert seed_variant in row.variants  # seed 값도 더해짐
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=canonical).delete()


def test_seed_lexicon_dry_run_writes_nothing(monkeypatch):
    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    canonical = f"TestCanNew-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(
        seed_module, "load_lexicon_seed_file",
        lambda: {"terms": [{"canonical": canonical, "variants": ["v1"], "priority": 100}], "_loaded_from": "test"},
    )
    result = seed_module.seed_lexicon(replace=False, dry_run=True)
    assert result["dry_run"] is True
    assert canonical in result["would_add"]
    with session_scope() as session:
        assert session.query(LexiconTerm).filter_by(canonical=canonical).first() is None


def test_seed_lexicon_replace_removes_canonical_not_in_seed(monkeypatch):
    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    stays = f"TestStays-{uuid.uuid4().hex[:8]}"
    goes = f"TestGoes-{uuid.uuid4().hex[:8]}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(canonical=goes, variants=["x"], priority=100))

        monkeypatch.setattr(
            seed_module, "load_lexicon_seed_file",
            lambda: {"terms": [{"canonical": stays, "variants": ["y"], "priority": 100}], "_loaded_from": "test"},
        )
        seed_module.seed_lexicon(replace=True)
        with session_scope() as session:
            assert session.query(LexiconTerm).filter_by(canonical=goes).first() is None
            assert session.query(LexiconTerm).filter_by(canonical=stays).first() is not None
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=stays).delete()
            session.query(LexiconTerm).filter_by(canonical=goes).delete()


def test_build_lexicon_map_logs_alias_collision_across_canonicals(caplog):
    # R03 §5 — 같은 variant가 두 canonical에 걸치면 조용히 넘어가지 않고
    # 경고 로그를 남긴다.
    import logging
    from app.lexicon.seed import build_lexicon_map

    shared = f"shared-{uuid.uuid4().hex[:6]}"
    terms = [
        {"canonical": "A", "variants": [shared]},
        {"canonical": "B", "variants": [shared]},
    ]
    with caplog.at_level(logging.WARNING, logger="citec.lexicon"):
        build_lexicon_map(terms)
    assert any("collision" in r.message for r in caplog.records)


def test_load_lexicon_map_picks_up_db_change_without_cache_clear():
    """다른 프로세스가 seed_lexicon()을 돌린 것처럼, cache_clear()를 전혀
    호출하지 않고 DB 행을 직접 바꾼 뒤 load_lexicon_map()을 다시 불러도
    새 값을 봐야 한다 — 이게 과거 @lru_cache(maxsize=1)가 못 하던 것."""
    from app.db.models import LexiconTerm
    from app.db.session import session_scope
    from app.lexicon.seed import load_lexicon_map

    # 변형어 문자열 자체도 매 실행마다 고유하게 만든다 — 그래프 테스트
    # DB는 테스트 간에 비워지지 않으므로, 고정 문자열("변형어1")을 쓰면
    # 과거 실행이 남긴 다른 canonical과 같은 키에서 섞여 실패한다(실제로
    # 재현됨: build_lexicon_map은 변형어 -> 그 변형어를 쓰는 모든 canonical
    # 목록이라, 키가 겹치면 두 테스트 실행의 값이 한 리스트에 합쳐진다).
    suffix = uuid.uuid4().hex[:8]
    canonical = f"TestCanonical-{suffix}"
    variant1, variant2 = f"변형어1-{suffix}", f"변형어2-{suffix}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(canonical=canonical, variants=[variant1], priority=100))

        first = load_lexicon_map()
        assert variant1 in first
        assert first[variant1] == [canonical, variant1]

        # cache_clear() 없이 DB를 직접 더 바꾼다 — 다른 워커 프로세스가
        # seed_lexicon()을 돌린 상황을 흉내낸다.
        with session_scope() as session:
            row = session.query(LexiconTerm).filter_by(canonical=canonical).one()
            row.variants = [variant1, variant2]

        second = load_lexicon_map()
        assert variant2 in second
        assert second[variant2] == [canonical, variant1, variant2]
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=canonical).delete()


def test_load_lexicon_map_reuses_cache_when_fingerprint_unchanged():
    """DB에 변화가 없으면(지문 동일) 같은 dict 객체를 재사용해야 한다 — 매
    호출마다 115k 문서 규모 재백필에서 전체 LexiconTerm을 다시 읽는 비용을
    피한다."""
    from app.db.models import LexiconTerm
    from app.db.session import session_scope
    from app.lexicon.seed import load_lexicon_map

    suffix = uuid.uuid4().hex[:8]
    canonical = f"TestCanonical-{suffix}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(canonical=canonical, variants=[f"v1-{suffix}"], priority=100))

        first = load_lexicon_map()
        second = load_lexicon_map()
        assert first is second
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=canonical).delete()


def test_seed_lexicon_preserves_manual_metadata_default_replace_false(monkeypatch):
    """F03(2026-10-13 최종 핸드오프) 실제 재현 — 기존 metadata={"manual": True}
    인 canonical에 seed를 (replace=False, 기본값) 돌려도 metadata가 지워지면
    안 된다. 과거엔 seed에 metadata 키가 없으면(실제로 늘 그렇다)
    `row.metadata_ = dict(t.get("metadata") or {})`가 매번 빈 dict로
    덮어썼다."""
    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    canonical = f"TestMeta-{uuid.uuid4().hex[:8]}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(
                canonical=canonical, variants=["v1"], priority=100, metadata_={"manual": True},
            ))

        monkeypatch.setattr(
            seed_module, "load_lexicon_seed_file",
            lambda: {"terms": [{"canonical": canonical, "variants": ["v1"], "priority": 100}],
                     "_loaded_from": "test"},
        )
        result = seed_module.seed_lexicon(replace=False)
        assert result["upserted"] == 1

        with session_scope() as session:
            row = session.query(LexiconTerm).filter_by(canonical=canonical).one()
            assert row.metadata_ == {"manual": True}  # 보존됨
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=canonical).delete()


def test_seed_lexicon_dry_run_detects_metadata_only_change(monkeypatch):
    """F03.4 — metadata-only 변경(variants/priority는 동일)도 dry-run의
    would_change와 change_fields에 드러나야 한다. 과거엔 dry-run이
    variants/priority만 비교해서, 이 경우 would_change가 비어 있었다."""
    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    canonical = f"TestMetaDiff-{uuid.uuid4().hex[:8]}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(
                canonical=canonical, variants=["v1"], priority=100, metadata_={"old": 1},
            ))

        monkeypatch.setattr(
            seed_module, "load_lexicon_seed_file",
            lambda: {"terms": [{"canonical": canonical, "variants": ["v1"], "priority": 100,
                                 "metadata": {"new": 2}}],
                     "_loaded_from": "test"},
        )
        result = seed_module.seed_lexicon(replace=False, dry_run=True)
        assert canonical in result["would_change"]
        assert result["change_fields"][canonical] == ["metadata"]

        with session_scope() as session:
            row = session.query(LexiconTerm).filter_by(canonical=canonical).one()
            assert row.metadata_ == {"old": 1}  # dry-run이라 안 바뀜
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=canonical).delete()


def test_seed_lexicon_noop_does_not_bump_updated_at(monkeypatch):
    """F05(2026-10-13 최종 핸드오프) 사전 점검 — F05가 lexicon_version
    (count:max(updated_at))을 "lexicon revision 변경" 재계산 신호로 쓰려면,
    아무것도 안 바뀐 seed 재실행이 updated_at을 흔들면 안 된다. apply
    루프가 plan["action"]=="noop"인 행도 row.variants/priority/metadata_를
    무조건 재할당하길래(seed.py), onupdate=func.now()가 매번 갱신시키는
    버그를 의심해 이 테스트를 작성했다 — 실측 결과 **버그가 아니었다**:
    SQLAlchemy의 스칼라 속성 추적이 재할당 값이 이미 로드된 기존 값과
    같으면 dirty로 표시하지 않아, UPDATE 자체가 발생하지 않는다(JSONB도
    새 dict 객체라도 내용이 같으면 동일하게 처리됨). 이 회귀를 앞으로도
    지키기 위한 가드 테스트로 남긴다."""
    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    canonical = f"TestNoop-{uuid.uuid4().hex[:8]}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(
                canonical=canonical, variants=["v1"], priority=100, metadata_={"manual": True},
            ))
            session.flush()

        with session_scope() as session:
            before = session.query(LexiconTerm).filter_by(canonical=canonical).one().updated_at

        monkeypatch.setattr(
            seed_module, "load_lexicon_seed_file",
            lambda: {"terms": [{"canonical": canonical, "variants": ["v1"], "priority": 100}],
                     "_loaded_from": "test"},
        )
        # seed 쪽은 이 canonical에 변경할 게 전혀 없다(variants 동일, priority
        # 동일, metadata는 seed에 키가 없으니 기존 값이 그대로 이김) — 즉
        # _plan_term_change가 "noop"을 반환해야 하는 입력이다.
        result = seed_module.seed_lexicon(replace=False)
        assert result["upserted"] == 1  # 루프는 여전히 모든 canonical을 순회함

        with session_scope() as session:
            after = session.query(LexiconTerm).filter_by(canonical=canonical).one().updated_at
        assert after == before, "변경이 전혀 없는데 updated_at이 갱신됨 — lexicon_version이 매 seed마다 흔들린다"
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=canonical).delete()


def test_seed_lexicon_dry_run_matches_actual_apply_diff(monkeypatch):
    """F03 합격 기준 — "허용된 변경은 dry-run에 표시, apply 실제 diff와
    일치"를 직접 확인한다: 같은 입력에 dry_run=True로 예측한 add/change
    집합이, 그 다음 실제 apply(dry_run=False) 실행 결과와 정확히 같아야
    한다(둘이 _plan_term_change()를 공유하므로)."""
    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    unchanged = f"TestUnchanged-{uuid.uuid4().hex[:8]}"
    changing = f"TestChanging-{uuid.uuid4().hex[:8]}"
    new_one = f"TestNew-{uuid.uuid4().hex[:8]}"
    try:
        with session_scope() as session:
            session.add(LexiconTerm(canonical=unchanged, variants=["v1"], priority=100, metadata_={"m": 1}))
            session.add(LexiconTerm(canonical=changing, variants=["v1"], priority=100, metadata_={"old": 1}))

        seed_terms = {
            "terms": [
                {"canonical": unchanged, "variants": ["v1"], "priority": 100, "metadata": {"m": 1}},
                {"canonical": changing, "variants": ["v1", "v2"], "priority": 100},
                {"canonical": new_one, "variants": ["v1"], "priority": 100},
            ],
            "_loaded_from": "test",
        }
        monkeypatch.setattr(seed_module, "load_lexicon_seed_file", lambda: seed_terms)

        dry = seed_module.seed_lexicon(replace=False, dry_run=True)
        assert dry["would_add"] == [new_one]
        assert dry["would_change"] == [changing]  # variants union만 바뀜; unchanged는 진짜 무변화
        assert unchanged not in dry["would_change"]

        apply_result = seed_module.seed_lexicon(replace=False, dry_run=False)
        assert apply_result["upserted"] == 3  # 3개 canonical 모두 upsert 대상이지만

        with session_scope() as session:
            row_unchanged = session.query(LexiconTerm).filter_by(canonical=unchanged).one()
            row_changing = session.query(LexiconTerm).filter_by(canonical=changing).one()
            assert row_unchanged.metadata_ == {"m": 1}  # 정말 안 바뀜
            assert set(row_changing.variants) == {"v1", "v2"}  # dry-run이 예측한 그대로
            assert session.query(LexiconTerm).filter_by(canonical=new_one).one() is not None

        # 재실행 — 멱등성(F03.6): 이제 더 바꿀 게 없어야 한다.
        dry_again = seed_module.seed_lexicon(replace=False, dry_run=True)
        assert dry_again["would_add"] == []
        assert dry_again["would_change"] == []
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=unchanged).delete()
            session.query(LexiconTerm).filter_by(canonical=changing).delete()
            session.query(LexiconTerm).filter_by(canonical=new_one).delete()
