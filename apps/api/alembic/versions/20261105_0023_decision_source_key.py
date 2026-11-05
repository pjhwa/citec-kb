"""decisions.source_key / source_ref — idempotent, traceable server-proposed decisions

Revision ID: 20261105_0023
Revises: 20261105_0022

source_key: stable key of the thing a scan derived the question from (e.g. fb:<bucket id>,
deadline:<document id>, gap:<domain>). Unique when set, so a re-run never proposes the same topic
twice (whatever the earlier decision's status). source_ref: structured link back to the source
(failure bucket, document, domain). Nullable; MCP/human proposals leave both empty.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261105_0023"
down_revision: Union[str, None] = "20261105_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("source_key", sa.String(256), nullable=True))
    op.add_column("decisions", sa.Column("source_ref", JSONB, nullable=True))
    op.create_index(
        "uq_decisions_source_key", "decisions", ["source_key"], unique=True,
        postgresql_where=sa.text("source_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_decisions_source_key", table_name="decisions")
    op.drop_column("decisions", "source_ref")
    op.drop_column("decisions", "source_key")
