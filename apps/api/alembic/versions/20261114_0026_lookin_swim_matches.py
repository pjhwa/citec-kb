"""lookin_swim_matches / lookin_swim_match_runs — LOOKIN 기술지원 문서와 SWIM 장애의 내용 매칭

dogether_swim_matches와 같은 구조(Revision 20261113_0025 참고).

Revision ID: 20261114_0026
Revises: 20261113_0025
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261114_0026"
down_revision: Union[str, None] = "20261113_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lookin_swim_matches",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("post_document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("incident_document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column("rank", sa.Integer, nullable=False, server_default="1"),
        sa.Column("signals", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("matcher_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("post_document_id", "incident_document_id", name="uq_lookin_swim_matches_pair"),
        sa.CheckConstraint("method in ('content','manual')", name="ck_lookin_swim_matches_method"),
        sa.CheckConstraint("status in ('auto','review','confirmed','rejected')", name="ck_lookin_swim_matches_status"),
    )
    op.create_index("ix_lookin_swim_matches_incident", "lookin_swim_matches", ["incident_document_id", "status"])
    op.create_index("ix_lookin_swim_matches_post", "lookin_swim_matches", ["post_document_id", "status"])
    op.create_table(
        "lookin_swim_match_runs",
        sa.Column("post_document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("matcher_version", sa.String(32), nullable=False),
        sa.Column("candidates", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("lookin_swim_match_runs")
    op.drop_index("ix_lookin_swim_matches_post", table_name="lookin_swim_matches")
    op.drop_index("ix_lookin_swim_matches_incident", table_name="lookin_swim_matches")
    op.drop_table("lookin_swim_matches")
