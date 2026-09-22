"""DB-touching tests for app.confluence.map_sync.get_source_defs(). Same
opt-in scratch-DB convention as test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _add_source(session, *, source_id, space_key, status="active", roots=None, explicit_pages=None):
    from app.db.models import Source

    session.add(
        Source(
            id=source_id,
            type="confluence_map",
            name=f"Confluence Map {space_key}",
            config={
                "space_key": space_key,
                "space_name": f"{space_key} name",
                "roots": roots or {"1": "root one"},
                "explicit_pages": explicit_pages or {},
            },
            status=status,
        )
    )


@pytest.fixture
def _cleanup_test_rows():
    """Deletes the throwaway confluence_map_test_*/fs_raw rows this file's
    tests seed into the shared scratch DB, so it stays at exactly 12
    confluence_map rows for whoever runs tests after this file.
    """
    ids = [
        "confluence_map_test_a",
        "confluence_map_test_b",
        "confluence_map_test_c",
        "confluence_map_test_d",
        "fs_raw_test_other_type",
    ]
    try:
        yield
    finally:
        from app.db.models import Source
        from app.db.session import session_scope

        with session_scope() as session:
            session.query(Source).filter(Source.id.in_(ids)).delete(synchronize_session=False)


def test_get_source_defs_returns_all_confluence_map_sources(_cleanup_test_rows):
    from app.confluence.map_sync import get_source_defs
    from app.db.session import session_scope

    with session_scope() as session:
        _add_source(session, source_id="confluence_map_test_a", space_key="TESTA")
        _add_source(session, source_id="confluence_map_test_b", space_key="TESTB", status="disabled")
        # A different type must never leak in. Note: uses a throwaway id
        # distinct from the real permanent "fs_raw" source row (seeded by
        # the initial-schema migration) so this test never collides with
        # or deletes that row.
        from app.db.models import Source
        session.add(
            Source(id="fs_raw_test_other_type", type="fs_raw", name="fs_raw", config={}, status="active")
        )

    defs = get_source_defs()
    assert "confluence_map_test_a" in defs
    assert "confluence_map_test_b" in defs
    assert "fs_raw_test_other_type" not in defs
    assert defs["confluence_map_test_a"]["space_key"] == "TESTA"
    assert defs["confluence_map_test_a"]["roots"] == {"1": "root one"}
    assert defs["confluence_map_test_a"]["explicit_pages"] == {}


def test_get_source_defs_active_only_excludes_disabled(_cleanup_test_rows):
    from app.confluence.map_sync import get_source_defs
    from app.db.session import session_scope

    with session_scope() as session:
        _add_source(session, source_id="confluence_map_test_c", space_key="TESTC", status="active")
        _add_source(session, source_id="confluence_map_test_d", space_key="TESTD", status="disabled")

    defs = get_source_defs(active_only=True)
    assert "confluence_map_test_c" in defs
    assert "confluence_map_test_d" not in defs
