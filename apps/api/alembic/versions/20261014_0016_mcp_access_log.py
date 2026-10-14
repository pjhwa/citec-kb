"""add mcp_access_log table (2026-10-14 admin dashboard -- MCP-origin query visibility)

Revision ID: 20261014_0016
Revises: 20261013_0015

New table only. Records every HTTP request carrying the mcp-server's
X-Citec-Client header, regardless of which REST endpoint handled it --
the existing QueryLog table only captures calls that explicitly opt into
audit=True, which kb_search (the most common MCP query) never did, so it
was invisible to admin.html's "최근 쿼리" card.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0016"
down_revision: Union[str, None] = "20261013_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_access_log",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tool", sa.String(128), nullable=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(256), nullable=False),
        sa.Column("query_summary", sa.String(512), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_mcp_access_log_created_at", "mcp_access_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mcp_access_log_created_at", table_name="mcp_access_log")
    op.drop_table("mcp_access_log")
