"""DB-touching test proving the 20260922_0007 seed migration's upgrade()
merges into an already-existing `sources` row instead of skipping it.

Skipped unless CONFLUENCE_SYNC_TEST_DATABASE_URL points at a reachable
scratch Postgres DB already at alembic head (same opt-in convention as
test_confluence_map_inventory_db.py) — never the live citec_knowledge DB.

Rather than re-running full alembic machinery (the scratch DB is already
at head, so `alembic upgrade` is a no-op), this imports the migration
module directly (importlib, same technique as verifying the migration
file in the prior task) and calls its `_seed_source(bind, source_id, sd)`
helper — the merge-upsert logic upgrade() applies per source_id, factored
out specifically so it can be exercised in isolation like this — against
a real Postgres connection, so the assertions below exercise real JSONB
`||` merge semantics, not a mock.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

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


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260922_0007_confluence_map_sources_seed.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "confluence_map_sources_seed_migration", _MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_seed_source_merges_into_pre_existing_lazy_created_row():
    """Simulates _ensure_source_row()'s old 2-key lazy-create shape (plus a
    runtime checkpoint marker) for a real seeded source_id, then calls the
    migration's merge-upsert helper against it directly. Must backfill
    space_name/explicit_pages while leaving roots/checkpoint byte-for-byte
    unchanged — the exact bug this migration fix addresses. Restores the
    scratch DB to its normal fully-migrated state afterward so later tasks
    reusing this DB see the same 12 rows they'd see on a fresh `alembic
    upgrade head`."""
    import sqlalchemy as sa

    from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS
    from app.db.session import get_engine

    mod = _load_migration_module()
    source_id = "confluence_map_devops001"
    sd = SEED_MAP_SOURCE_DEFS[source_id]
    engine = get_engine()

    with engine.begin() as conn:
        before = conn.execute(
            sa.text("SELECT config FROM sources WHERE id = :id"), {"id": source_id}
        ).scalar_one()

    try:
        # Old lazy-create shape: only space_key + roots ever written by
        # _ensure_source_row(), plus a runtime checkpoint marker that must
        # never be clobbered by the migration's merge.
        lazy_config = {
            "space_key": sd["space_key"],
            "roots": sd["roots"],
            "checkpoint": {"601879661": {"start": 5}},
        }
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM sources WHERE id = :id"), {"id": source_id}
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sources (id, type, name, config, status) "
                    "VALUES (:id, 'confluence_map', :name, CAST(:cfg AS jsonb), 'active')"
                ),
                {
                    "id": source_id,
                    "name": f"Confluence Map {sd['space_key']}",
                    "cfg": __import__("json").dumps(lazy_config),
                },
            )

        with engine.begin() as conn:
            mod._seed_source(conn, source_id, sd)

        with engine.begin() as conn:
            after = conn.execute(
                sa.text("SELECT config FROM sources WHERE id = :id"), {"id": source_id}
            ).scalar_one()

        assert after["space_key"] == sd["space_key"]
        assert after["space_name"] == sd["space_name"]
        assert after["explicit_pages"] == sd["explicit_pages"]
        # Untouched by the merge, exactly as seeded above.
        assert after["roots"] == lazy_config["roots"]
        assert after["checkpoint"] == lazy_config["checkpoint"]
    finally:
        # Restore the scratch DB to its normal post-migration state (no
        # lazy-create leftovers, no checkpoint) so other tasks reusing this
        # DB see the same 12 fully-seeded rows a fresh `alembic upgrade
        # head` would produce.
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM sources WHERE id = :id"), {"id": source_id}
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sources (id, type, name, config, status) "
                    "VALUES (:id, 'confluence_map', :name, CAST(:cfg AS jsonb), 'active')"
                ),
                {
                    "id": source_id,
                    "name": f"Confluence Map {sd['space_key']}",
                    "cfg": __import__("json").dumps(before),
                },
            )

        with engine.begin() as conn:
            restored = conn.execute(
                sa.text("SELECT config FROM sources WHERE id = :id"), {"id": source_id}
            ).scalar_one()
        assert restored == before

        with engine.begin() as conn:
            count = conn.execute(
                sa.text("SELECT count(*) FROM sources WHERE type = 'confluence_map'")
            ).scalar_one()
        assert count == 12
