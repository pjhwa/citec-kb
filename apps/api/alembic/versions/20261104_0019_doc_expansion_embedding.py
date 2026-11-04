"""add doc_expansion_embedding (2026-11-04 expansion vectors; search ranking signal only)

Revision ID: 20261104_0019
Revises: 20261103_0018

New table only. Does not touch documents / chunks / embeddings / doc_expansion.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20261104_0019"
down_revision: Union[str, None] = "20261103_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doc_expansion_embedding",
        sa.Column("document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model", sa.String(128), nullable=False, server_default="intfloat/multilingual-e5-base"),
        sa.Column("vector", Vector(768), nullable=False),
        sa.Column("source_content_hash", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_doc_expansion_embedding_hnsw",
        "doc_expansion_embedding",
        ["vector"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vector": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_doc_expansion_embedding_hnsw", table_name="doc_expansion_embedding")
    op.drop_table("doc_expansion_embedding")
