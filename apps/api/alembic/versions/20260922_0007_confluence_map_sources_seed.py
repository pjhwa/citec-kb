"""seed confluence_map source registry (12 originally-hardcoded spaces)

Revision ID: 20260922_0007
Revises: 20260916_0006

Promotes the `sources` table (type="confluence_map") into the live
registry app.confluence.map_sync.get_source_defs() reads from, instead of
rows being lazily created by the first sync_map() run for each source_id.

Idempotent via a merge upsert (ON CONFLICT (id) DO UPDATE): a source_id
that already has a row — e.g. one lazily created by the pre-existing
app.confluence.map_sync._ensure_source_row() path, which only ever wrote
a 2-key config ({"space_key", "roots"}), no "space_name"/"explicit_pages"
— gets those missing keys merged into its config via Postgres JSONB `||`.
This never touches `roots` (already matches, since _ensure_source_row
seeded it from this same source dict — left alone anyway to minimize
blast radius) or `checkpoint` (a runtime field a synced row may already
have in its config; must never be clobbered), and never touches `name`/
`status` on conflict at all, so admin-set status or in-progress runtime
state is never disturbed by this migration.
"""

from __future__ import annotations

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "20260922_0007"
down_revision: Union[str, None] = "20260916_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_sources = sa.table(
    "sources",
    sa.column("id", sa.String),
    sa.column("type", sa.String),
    sa.column("name", sa.String),
    sa.column("config", postgresql.JSONB),
    sa.column("status", sa.String),
)


def _seed_source(bind, source_id: str, sd: dict[str, Any]) -> None:
    """Insert-or-merge a single seed source row. Extracted from upgrade()'s
    loop body so tests can exercise the merge-on-conflict behavior directly
    against a real Postgres bind without invoking full alembic machinery."""
    full_config = {
        "space_key": sd["space_key"],
        "space_name": sd["space_name"],
        "roots": sd.get("roots") or {},
        "explicit_pages": sd.get("explicit_pages") or {},
    }
    stmt = pg_insert(_sources).values(
        id=source_id,
        type="confluence_map",
        name=f"Confluence Map {sd['space_key']}",
        config=full_config,
        status="active",
    )
    # Merge in only the keys _ensure_source_row's old lazy-create path never
    # wrote (space_name, explicit_pages) plus space_key (harmless refresh) —
    # deliberately NOT touching `name`, `status`, or the row's own `roots`/
    # `checkpoint`, so an already-synced source's in-progress state and any
    # admin-set status are never disturbed by this migration.
    merge_patch = {
        "space_key": sd["space_key"],
        "space_name": sd["space_name"],
        "explicit_pages": sd.get("explicit_pages") or {},
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            # NOTE: pass the dict directly to sa.cast(), not json.dumps(dict)
            # — the JSONB bind processor already serializes a python value
            # to JSON text; casting an already-serialized string produces a
            # JSONB *string scalar*, and object || string-scalar silently
            # wraps both sides into a 2-element array instead of merging
            # keys (confirmed against real Postgres while building this).
            "config": _sources.c.config.op("||")(
                sa.cast(merge_patch, postgresql.JSONB)
            ),
        },
    )
    bind.execute(stmt)


def upgrade() -> None:
    from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS

    bind = op.get_bind()
    for source_id, sd in SEED_MAP_SOURCE_DEFS.items():
        _seed_source(bind, source_id, sd)


def downgrade() -> None:
    from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS

    bind = op.get_bind()
    # Only remove rows that still look exactly as seeded (never synced) —
    # a source that has since run for real (last_sync_at set) keeps its row
    # rather than losing sync history/checkpoint on downgrade. Note this
    # also deletes any pre-existing *unsynced* row with the same id even
    # if it wasn't inserted by this migration's own upgrade() (e.g. a row
    # lazily created by _ensure_source_row() before this migration ever
    # ran) — accepted/intentional, since "never synced" is the only signal
    # available here for "safe to remove".
    bind.execute(
        sa.text(
            "DELETE FROM sources WHERE type = 'confluence_map' "
            "AND id = ANY(:ids) AND last_sync_at IS NULL"
        ),
        {"ids": list(SEED_MAP_SOURCE_DEFS.keys())},
    )
