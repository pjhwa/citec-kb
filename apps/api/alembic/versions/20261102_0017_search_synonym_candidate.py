"""add search_synonym_candidate (2026-11-02 Fabrix synonym mining; search-only)

Revision ID: 20261102_0017
Revises: 20261014_0016

New table only. Holds LLM-proposed synonym groups and their approval state.
Does not touch lexicon_terms, so lexicon_version / graph fingerprint are unaffected.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261102_0017"
down_revision: Union[str, None] = "20261014_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_synonym_candidate",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("terms", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("terms_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("terms_key", name="uq_search_synonym_candidate_terms_key"),
    )
    op.create_index("ix_search_synonym_candidate_status", "search_synonym_candidate", ["status"])


def downgrade() -> None:
    op.drop_index("ix_search_synonym_candidate_status", table_name="search_synonym_candidate")
    op.drop_table("search_synonym_candidate")
