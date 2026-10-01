"""add body_hash/extractor_version to issue_frames (P0-C freshness binding)

Revision ID: 20260930_0009
Revises: 20260928_0008

Purely additive, nullable columns — no backfill here. Existing rows keep
body_hash/extractor_version NULL, which app.frames.job.extract_frames()
treats as "extracted before this migration" (stale-unknown, eligible for one
regeneration pass on the next non-force run) rather than as fresh. This
migration changes schema only; it does not itself regenerate any frame —
that happens the next time extract_frames() runs and finds a NULL hash, or
via an explicit `force=True` backfill pass (separate, operator-run step; not
executed automatically by this migration or by application startup).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260930_0009"
down_revision: Union[str, None] = "20260928_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("issue_frames", sa.Column("body_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "issue_frames", sa.Column("extractor_version", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("issue_frames", "extractor_version")
    op.drop_column("issue_frames", "body_hash")
