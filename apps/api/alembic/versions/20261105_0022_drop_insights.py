"""retire 1st-generation insights: archive+purge insight documents, drop insights table

Revision ID: 20261105_0022
Revises: 20261105_0021

Order (spec §2-1 step 5): insight documents are first set to status='archived' (so they stop
appearing in default search at once), then their embeddings, chunks, sections and the documents
themselves are deleted (other per-document rows cascade), feedback rows targeting insights are
removed, and finally the insights table is dropped.

DOWNGRADE RESTORES THE EMPTY SCHEMA ONLY. Deleted insight rows/documents are NOT recoverable from
this migration — take a backup first (scripts/backup_postgres.sh). Runbook:
docs/DECISION_INSIGHT_RETIREMENT_RUNBOOK_20261105.md
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261105_0022"
down_revision: Union[str, None] = "20261105_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INSIGHT_DOCS = "SELECT id FROM documents WHERE source_type = 'insight'"


def upgrade() -> None:
    op.execute("UPDATE documents SET status = 'archived' WHERE source_type = 'insight'")
    op.execute(
        f"DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id IN ({_INSIGHT_DOCS}))"
    )
    op.execute(f"DELETE FROM chunks WHERE document_id IN ({_INSIGHT_DOCS})")
    op.execute(f"DELETE FROM document_sections WHERE document_id IN ({_INSIGHT_DOCS})")
    op.execute("DELETE FROM documents WHERE source_type = 'insight'")
    op.execute("DELETE FROM feedback WHERE target_type = 'insight'")
    op.drop_index("ix_insights_status", table_name="insights")
    op.drop_table("insights")


def downgrade() -> None:
    op.create_table(
        "insights",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("body_md", sa.Text(), server_default="", nullable=False),
        sa.Column("source_doc_ids", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column("reviewer", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_document_id", sa.String(length=64), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_insights_status", "insights", ["status"])
