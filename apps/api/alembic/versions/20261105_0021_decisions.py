"""decisions / decision_evidence / decision_verdicts (append-only) / decision_nonces

Revision ID: 20261105_0021
Revises: 20261105_0020

New tables only. decision_verdicts is immutable: a trigger rejects UPDATE and DELETE.
Downgrade drops trigger and tables.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261105_0021"
down_revision: Union[str, None] = "20261105_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = dict(timezone=True)


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("question", sa.String(300), nullable=False),
        sa.Column("options", JSONB, nullable=False),
        sa.Column("topic", sa.String(256)),
        sa.Column("domain", sa.String(64)),
        sa.Column("stakes", sa.String(8), nullable=False, server_default="mid"),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("status_before_retire", sa.String(16)),
        sa.Column("ai_recommendation_key", sa.String(64)),
        sa.Column("current_verdict_id", sa.String(64)),
        sa.Column("superseded_by", sa.String(64), sa.ForeignKey("decisions.id", ondelete="SET NULL")),
        sa.Column("review_interval_days", sa.Integer, server_default="180"),
        sa.Column("created_by_kind", sa.String(16), nullable=False, server_default="mcp"),
        sa.Column("created_by", sa.String(128)),
        sa.Column("created_at", sa.DateTime(**_TS), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(**_TS), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_decisions_status", "decisions", ["status"])
    op.create_index("ix_decisions_topic", "decisions", ["topic"])
    op.create_index("ix_decisions_domain", "decisions", ["domain"])

    op.create_table(
        "decision_evidence",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("decision_id", sa.String(64), sa.ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("option_key", sa.String(64)),
        sa.Column("document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("pinned_version", sa.Integer),
        sa.Column("pinned_content_hash", sa.String(128)),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("quote_hash", sa.String(64), nullable=False),
        sa.Column("quote_locator", sa.String(256)),
        sa.Column("source_modified_at", sa.DateTime(**_TS)),
        sa.Column("grade_snapshot", sa.String(16)),
        sa.Column("stance", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False, server_default="internal"),
        sa.Column("external_url", sa.String(2048)),
        sa.Column("external_snapshot", sa.Text),
        sa.Column("retrieved_at", sa.DateTime(**_TS)),
        sa.Column("added_by", sa.String(128)),
        sa.Column("retracted_at", sa.DateTime(**_TS)),
        sa.Column("created_at", sa.DateTime(**_TS), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "decision_id", "option_key", "document_id", "pinned_content_hash", "quote_hash",
            name="uq_decision_evidence_dedup",
        ),
    )
    op.create_index("ix_decision_evidence_decision", "decision_evidence", ["decision_id"])

    op.create_table(
        "decision_verdicts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("decision_id", sa.String(64), sa.ForeignKey("decisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("option_key", sa.String(64), nullable=False),
        sa.Column("free_text", sa.Text),
        sa.Column("rationale", sa.Text),
        sa.Column("decided_by", sa.String(128), nullable=False),
        sa.Column("auth_via", sa.String(16), nullable=False),
        sa.Column("decided_via", sa.String(16), nullable=False, server_default="ui"),
        sa.Column("decided_at", sa.DateTime(**_TS), nullable=False, server_default=sa.func.now()),
        sa.Column("ai_recommendation_key", sa.String(64)),
        sa.Column("time_to_decide_ms", sa.Integer),
        sa.Column("evidence_snapshot", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("supersedes_verdict_id", sa.String(64)),
        sa.Column("effective_from", sa.DateTime(**_TS), nullable=False, server_default=sa.func.now()),
        sa.Column("external_only_confirmed", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_decision_verdicts_decision", "decision_verdicts", ["decision_id", "decided_at"])
    op.execute(
        """
        CREATE FUNCTION decision_verdicts_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'decision_verdicts is append-only (% not allowed)', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_decision_verdicts_immutable BEFORE UPDATE OR DELETE ON decision_verdicts "
        "FOR EACH ROW EXECUTE FUNCTION decision_verdicts_immutable()"
    )

    op.create_table(
        "decision_nonces",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("decision_id", sa.String(64), sa.ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issued_at", sa.DateTime(**_TS), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(**_TS), nullable=False),
        sa.Column("used_at", sa.DateTime(**_TS)),
    )


def downgrade() -> None:
    op.drop_table("decision_nonces")
    op.execute("DROP TRIGGER IF EXISTS trg_decision_verdicts_immutable ON decision_verdicts")
    op.drop_index("ix_decision_verdicts_decision", table_name="decision_verdicts")
    op.drop_table("decision_verdicts")
    op.execute("DROP FUNCTION IF EXISTS decision_verdicts_immutable()")
    op.drop_index("ix_decision_evidence_decision", table_name="decision_evidence")
    op.drop_table("decision_evidence")
    op.drop_index("ix_decisions_domain", table_name="decisions")
    op.drop_index("ix_decisions_topic", table_name="decisions")
    op.drop_index("ix_decisions_status", table_name="decisions")
    op.drop_table("decisions")
