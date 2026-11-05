"""api_access_log — non-MCP API usage (web UI / internal systems / external systems)

Revision ID: 20261105_0020
Revises: 20261104_0019

New table only; nothing existing is touched. Downgrade drops it.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0020"
down_revision: Union[str, None] = "20261104_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_access_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("client_kind", sa.String(16), nullable=False),
        sa.Column("client_name", sa.String(128), nullable=True),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(256), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
    )
    op.create_index("ix_api_access_log_created_at", "api_access_log", ["created_at"])
    op.create_index("ix_api_access_log_kind_created", "api_access_log", ["client_kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_api_access_log_kind_created", table_name="api_access_log")
    op.drop_index("ix_api_access_log_created_at", table_name="api_access_log")
    op.drop_table("api_access_log")
