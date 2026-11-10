"""unmask_audit_log — permanent record of pii/net original views

Revision ID: 20261109_0024
Revises: 20261105_0023
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261109_0024"
down_revision: Union[str, None] = "20261105_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "unmask_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sub", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128)),
        sa.Column("roles", sa.String(128), nullable=False, server_default=""),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(256), nullable=False),
        sa.Column("doc_refs", JSONB, nullable=False, server_default="[]"),
        sa.Column("counts", JSONB, nullable=False, server_default="{}"),
        sa.Column("client_ip", sa.String(64)),
        sa.Column("outcome", sa.String(16), nullable=False),
    )
    op.create_index("ix_unmask_audit_log_created_at", "unmask_audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_unmask_audit_log_created_at", table_name="unmask_audit_log")
    op.drop_table("unmask_audit_log")
