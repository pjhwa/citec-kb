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
