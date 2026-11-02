"""add doc_expansion (2026-11-03 document expansion; search ranking signal only)

Revision ID: 20261103_0018
Revises: 20261102_0017

New table only. Does not touch documents / chunks / embeddings.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261103_0018"
down_revision: Union[str, None] = "20261102_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doc_expansion",
        sa.Column("document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("keywords", postgresql.ARRAY(sa.String()), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("grounded", postgresql.ARRAY(sa.String()), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("tsv_all", postgresql.TSVECTOR(), nullable=True),
        sa.Column("tsv_grounded", postgresql.TSVECTOR(), nullable=True),
        sa.Column("rule", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_doc_expansion_tsv_all", "doc_expansion", ["tsv_all"], postgresql_using="gin")
    op.create_index("ix_doc_expansion_tsv_grounded", "doc_expansion", ["tsv_grounded"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_doc_expansion_tsv_grounded", table_name="doc_expansion")
    op.drop_index("ix_doc_expansion_tsv_all", table_name="doc_expansion")
    op.drop_table("doc_expansion")
