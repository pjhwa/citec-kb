"""add lexicon_terms.updated_at (G06 — version-keyed load_lexicon_map() cache
so multiple API workers don't go stale after seed_lexicon() in another
process)

Revision ID: 20261013_0012
Revises: 20261012_0011

Purely additive. See docs/features/graph-evidence.md G06 잔여.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0012"
down_revision: Union[str, None] = "20261012_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lexicon_terms",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("lexicon_terms", "updated_at")
