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


def test_seed_lexicon_apply_blocks_on_concurrent_row_lock_and_sees_latest_state():
    """F03(2026-10-13 최종 핸드오프) §2.3.5/8 — "dry-run 시점 이후 데이터가
    바뀌면... 오래된 계획을 그대로 덮어쓰지 말라"의 실제 재현. 실제 두 번째
    커넥션으로 같은 행에 FOR UPDATE 잠금을 먼저 쥐고(= "seed_lexicon() 호출
    도중 다른 writer가 수동으로 이 행을 고친다"는 상황을 흉내), seed_lexicon()
    이 실제로 그 잠금에 블록되는지, 블록이 풀린 뒤 그 수동 변경 위에 자기
    변경을 올바르게 병합하는지(수동 변경을 지우지 않는지) 검증한다."""
    import threading
    import time
    import uuid as uuid_mod

    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope, get_engine
    from sqlalchemy import text

    canonical = f"TestLock-{uuid_mod.uuid4().hex[:8]}"
    other_conn = None
    try:
        with session_scope() as session:
            session.add(LexiconTerm(canonical=canonical, variants=["base"], priority=100, metadata_={}))

        # 1) 별도 커넥션(= "다른 writer")으로 이 행을 FOR UPDATE로 먼저 잠근다.
        other_conn = get_engine().connect()
        other_conn.execute(text(
            "SELECT * FROM lexicon_terms WHERE canonical = :c FOR UPDATE"
        ), {"c": canonical})

        monkeypatch_seed = {"canonical": canonical, "variants": ["base", "from-seed"], "priority": 100}
        from unittest.mock import patch

        seed_done = threading.Event()
        with patch.object(
            seed_module, "load_lexicon_seed_file",
            return_value={"terms": [monkeypatch_seed], "_loaded_from": "test"},
        ):
            def run_seed():
                seed_module.seed_lexicon(replace=False)
                seed_done.set()

            t = threading.Thread(target=run_seed, daemon=True)
            t.start()

            # 2) seed_lexicon()이 실제로 블록돼 있는지 간접 증명 — 잠금이 없으면
            # 이 작업은 수 ms면 끝나므로, 넉넉한 유예시간(1.5s) 안에도 안 끝났다면
            # FOR UPDATE에 블록된 것으로 본다(pg_stat_activity 폴링보다 훨씬
            # 단순하고, 이 테스트가 실제로 한 번 hang을 일으킨 적이 있어
            # 더 견고한 방식으로 바꿨다).
            still_blocked = not seed_done.wait(timeout=1.5)
            assert still_blocked, (
                "seed_lexicon()이 다른 writer가 FOR UPDATE로 잠근 행 때문에 블록되기도 전에 "
                "끝나버렸다 — 잠금이 전혀 효과가 없다는 뜻"
            )

            # 3) "다른 writer"가 수동으로 alias를 추가하고 커밋 — seed_lexicon()은 아직 대기 중.
            other_conn.execute(text(
                "UPDATE lexicon_terms SET variants = variants || ARRAY['manual-alias'] WHERE canonical = :c"
            ), {"c": canonical})
            other_conn.commit()
            other_conn.close()
            other_conn = None

            t.join(timeout=10)
            assert seed_done.is_set(), "seed_lexicon()이 잠금 해제 후에도 끝나지 않음"

        with session_scope() as session:
            row = session.query(LexiconTerm).filter_by(canonical=canonical).one()
            # 수동 alias가 살아있어야 한다(= seed_lexicon()이 블록 풀린 뒤 최신 상태를
            # 다시 읽어 그 위에 병합했다는 뜻 — 잠금 전 스냅샷으로 덮어쓰지 않았다).
            assert "manual-alias" in row.variants
            assert "from-seed" in row.variants
            assert "base" in row.variants
    finally:
        if other_conn is not None:
            # 중간에 assert가 실패해도 이 커넥션이 쥔 행 잠금을 반드시 풀어야
            # 아래 cleanup DELETE가 영원히 블록되지 않는다(실제로 한 번 이렇게
            # hang이 났다 — 수정 전에는 이 finally가 없었음).
            try:
                other_conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            other_conn.close()
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=canonical).delete()


def test_seed_lexicon_apply_is_atomic_on_mid_loop_failure():
    """F03(2026-10-13 최종 핸드오프) §2.3.6 — "적용의 원자성... 변경 실패 시
    상태를 검증하라". seed_lexicon()은 전부 한 session_scope() 안에서 도므로,
    루프 중간에 예외가 나면 그 전까지 처리한 canonical들도 전부 롤백돼야
    한다(부분 적용 금지) — _plan_term_change를 N번째 호출에서 일부러 실패
    시켜 실제로 검증한다."""
    import uuid as uuid_mod
    from unittest.mock import patch

    import app.lexicon.seed as seed_module
    from app.db.models import LexiconTerm
    from app.db.session import session_scope

    c1 = f"TestAtomic1-{uuid_mod.uuid4().hex[:8]}"
    c2 = f"TestAtomic2-{uuid_mod.uuid4().hex[:8]}"  # 이 canonical 처리 중 실패를 주입
    c3 = f"TestAtomic3-{uuid_mod.uuid4().hex[:8]}"

    seed_terms = {
        "terms": [
            {"canonical": c1, "variants": ["v1"], "priority": 100},
            {"canonical": c2, "variants": ["v1"], "priority": 100},
            {"canonical": c3, "variants": ["v1"], "priority": 100},
        ],
        "_loaded_from": "test",
    }

    original_plan = seed_module._plan_term_change

    def failing_plan(row, seed_term, *, replace):
        if seed_term.get("canonical") == c2:
            raise RuntimeError("injected mid-loop failure for atomicity test")
        return original_plan(row, seed_term, replace=replace)

    try:
        with patch.object(seed_module, "load_lexicon_seed_file", return_value=seed_terms):
            with patch.object(seed_module, "_plan_term_change", side_effect=failing_plan):
                with pytest.raises(RuntimeError):
                    seed_module.seed_lexicon(replace=False)

        with session_scope() as session:
            # c1은 c2보다 먼저 루프를 돌아 session.add()까지 됐을 수 있지만,
            # 예외로 전체 트랜잭션이 롤백됐으므로 셋 다 DB에 없어야 한다.
            assert session.query(LexiconTerm).filter_by(canonical=c1).first() is None
            assert session.query(LexiconTerm).filter_by(canonical=c2).first() is None
            assert session.query(LexiconTerm).filter_by(canonical=c3).first() is None
    finally:
        with session_scope() as session:
            session.query(LexiconTerm).filter_by(canonical=c1).delete()
            session.query(LexiconTerm).filter_by(canonical=c2).delete()
            session.query(LexiconTerm).filter_by(canonical=c3).delete()
