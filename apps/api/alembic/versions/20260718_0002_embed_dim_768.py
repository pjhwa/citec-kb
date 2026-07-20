"""switch embeddings to 768-d for multilingual-e5-base

Revision ID: 20260718_0002
Revises: 20260718_0001
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260718_0002"
down_revision: Union[str, None] = "20260718_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector_hnsw")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS vector")
    op.execute("ALTER TABLE embeddings ADD COLUMN vector vector(768) NOT NULL")
    op.execute(
        """
        CREATE INDEX ix_embeddings_vector_hnsw
        ON embeddings
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
    op.execute("ALTER TABLE embeddings ALTER COLUMN dim SET DEFAULT 768")
    op.execute(
        "ALTER TABLE embeddings ALTER COLUMN model SET DEFAULT 'intfloat/multilingual-e5-base'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector_hnsw")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS vector")
    op.execute("ALTER TABLE embeddings ADD COLUMN vector vector(1024) NOT NULL")
    op.execute(
        """
        CREATE INDEX ix_embeddings_vector_hnsw
        ON embeddings
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
