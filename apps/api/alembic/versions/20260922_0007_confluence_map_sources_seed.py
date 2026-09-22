"""seed confluence_map source registry (12 originally-hardcoded spaces)

Revision ID: 20260922_0007
Revises: 20260916_0006

Promotes the `sources` table (type="confluence_map") into the live
registry app.confluence.map_sync.get_source_defs() reads from, instead of
rows being lazily created by the first sync_map() run for each source_id.
Idempotent: ON CONFLICT (id) DO NOTHING, so re-running this (or running it
against a DB where some of these sources already synced and have
last_sync_at/checkpoint set) never clobbers existing progress.
"""

from __future__ import annotations

from typing import Sequence, Union

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


def upgrade() -> None:
    from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS

    bind = op.get_bind()
    for source_id, sd in SEED_MAP_SOURCE_DEFS.items():
        stmt = pg_insert(_sources).values(
            id=source_id,
            type="confluence_map",
            name=f"Confluence Map {sd['space_key']}",
            config={
                "space_key": sd["space_key"],
                "space_name": sd["space_name"],
                "roots": sd.get("roots") or {},
                "explicit_pages": sd.get("explicit_pages") or {},
            },
            status="active",
        ).on_conflict_do_nothing(index_elements=["id"])
        bind.execute(stmt)


def downgrade() -> None:
    from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS

    bind = op.get_bind()
    # Only remove rows that still look exactly as seeded (never synced) —
    # a source that has since run for real (last_sync_at set) keeps its row
    # rather than losing sync history/checkpoint on downgrade.
    bind.execute(
        sa.text(
            "DELETE FROM sources WHERE type = 'confluence_map' "
            "AND id = ANY(:ids) AND last_sync_at IS NULL"
        ),
        {"ids": list(SEED_MAP_SOURCE_DEFS.keys())},
    )
