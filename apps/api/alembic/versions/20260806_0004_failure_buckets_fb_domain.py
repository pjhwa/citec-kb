"""add failure_buckets.fb_domain + evidence_ref (multi-plugin support)

Revision ID: 20260806_0004
Revises: 20260729_0003
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0004"
down_revision: Union[str, None] = "20260729_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "failure_buckets",
        sa.Column("fb_domain", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "failure_buckets",
        sa.Column("evidence_ref", sa.Text(), nullable=True),
    )
    # Backfill: every existing row predates the multi-plugin split and came
    # from network packet analysis (see design doc §2). evidence_ref has no
    # historical source to point to, so it gets an explicit placeholder
    # rather than an empty string (the new validation rule rejects blanks).
    op.execute("UPDATE failure_buckets SET fb_domain = 'network' WHERE fb_domain IS NULL")
    op.execute(
        "UPDATE failure_buckets SET evidence_ref = 'legacy:pre-migration' WHERE evidence_ref IS NULL"
    )
    op.alter_column("failure_buckets", "fb_domain", nullable=False)
    op.alter_column("failure_buckets", "evidence_ref", nullable=False)
    op.create_index("ix_failure_buckets_fb_domain", "failure_buckets", ["fb_domain"])
    op.create_index(
        "ix_failure_buckets_fb_domain_protocol", "failure_buckets", ["fb_domain", "protocol"]
    )
    # Fix a pre-existing bug (design doc §1/§2): the old taxonomy branch put
    # `protocol` (e.g. "tcp"/"tls") straight into documents.domain, which isn't
    # one of the 7 corpus-wide domain values kb_search(area=) filters on. Every
    # pre-migration bucket is fb_domain='network' (just backfilled above),
    # which maps to corpus domain "network" — so this mirrors that mapping onto
    # the existing Document rows without a full re-chunk/re-embed (domain is a
    # plain column, not part of content_hash).
    op.execute(
        """
        UPDATE documents
        SET domain = 'network'
        WHERE source_type = 'failure_bucket'
          AND id IN (SELECT document_id FROM failure_buckets WHERE document_id IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_failure_buckets_fb_domain_protocol", table_name="failure_buckets")
    op.drop_index("ix_failure_buckets_fb_domain", table_name="failure_buckets")
    op.drop_column("failure_buckets", "evidence_ref")
    op.drop_column("failure_buckets", "fb_domain")
