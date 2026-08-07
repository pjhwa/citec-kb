"""add failure_buckets.environment (csp|msp|onprem|hybrid facet)

Revision ID: 20260807_0005
Revises: 20260806_0004
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0005"
down_revision: Union[str, None] = "20260806_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "failure_buckets",
        sa.Column("environment", sa.String(length=16), nullable=True),
    )
    # No backfill: unlike fb_domain (§20260806_0004), we have no reliable source
    # to infer environment for pre-existing rows from (see analysis doc —
    # packet-capture-rca_개선지침_및_citec-kb_연동분석.md §B-1). Registering a
    # guessed value would violate the "근거 없는 등록 금지" principle
    # (FAILURE_BUCKET_PLUGIN_GUIDE.md §1.5) applied retroactively. Leave NULL —
    # a future confirm/refine pass can fill it in with real evidence.
    op.create_index("ix_failure_buckets_environment", "failure_buckets", ["environment"])


def downgrade() -> None:
    op.drop_index("ix_failure_buckets_environment", table_name="failure_buckets")
    op.drop_column("failure_buckets", "environment")
