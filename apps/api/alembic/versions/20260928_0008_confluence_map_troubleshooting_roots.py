"""add 운영매뉴얼/트러블슈팅 roots to the Openstack101 and DevOps001 map sources

Revision ID: 20260928_0008
Revises: 20260922_0007

The 0007 seed never touches an existing row's `roots`, so editing
map_source_seed.py alone does not reach a database that already has these
sources. This merges the two new roots into `config.roots` (JSONB `||`, so
existing roots and runtime keys such as `checkpoint` are untouched) and is
a no-op when the source row does not exist or already has the root. It only
changes configuration: nothing is crawled until sync_map()/
run_map_inventory() is run for the two sources.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0008"
down_revision: Union[str, None] = "20260922_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_ROOTS = {
    "confluence_map_openstack101": {"1816390936": "운영매뉴얼/트러블슈팅"},
    "confluence_map_devops001": {"2125217911": "운영매뉴얼/트러블슈팅"},
}


def upgrade() -> None:
    import json

    bind = op.get_bind()
    for source_id, roots in _NEW_ROOTS.items():
        bind.execute(
            sa.text(
                "UPDATE sources SET config = jsonb_set("
                "config, '{roots}', "
                "COALESCE(config->'roots', '{}'::jsonb) || CAST(:roots AS jsonb), true) "
                "WHERE id = :id AND type = 'confluence_map'"
            ),
            {"id": source_id, "roots": json.dumps(roots, ensure_ascii=False)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for source_id, roots in _NEW_ROOTS.items():
        for page_id in roots:
            bind.execute(
                sa.text(
                    "UPDATE sources SET config = jsonb_set("
                    "config, '{roots}', (config->'roots') - :pid, true) "
                    "WHERE id = :id AND type = 'confluence_map' AND config ? 'roots'"
                ),
                {"id": source_id, "pid": page_id},
            )
