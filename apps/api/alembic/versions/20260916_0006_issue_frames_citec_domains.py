"""add issue_frames.citec_domains + severity_tier (CI-TEC recurring-incident dashboard)

Revision ID: 20260916_0006
Revises: 20260807_0005
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0006"
down_revision: Union[str, None] = "20260807_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "issue_frames",
        sa.Column(
            "citec_domains",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.add_column(
        "issue_frames",
        sa.Column("severity_tier", sa.String(length=32), nullable=True),
    )
    # GIN index for "documents tagged Kubernetes" / "... AND 성능" style
    # array-contains queries the recurring-pattern dashboard will run.
    op.execute(
        "CREATE INDEX ix_issue_frames_citec_domains "
        "ON issue_frames USING gin (citec_domains)"
    )
    op.create_index("ix_issue_frames_severity_tier", "issue_frames", ["severity_tier"])
    # No backfill: both columns are populated by a batch job
    # (app.frames.citec_taxonomy.tag_citec_domains /
    # classify_severity_tier, run over incident_reports documents) — this
    # migration only adds the columns. Existing rows get citec_domains='{}'
    # (empty, not "no domains apply" vs "not yet computed" — same
    # ambiguity as issue_frames.components already had) and
    # severity_tier=NULL (not yet computed) until that job runs.


def downgrade() -> None:
    op.drop_index("ix_issue_frames_severity_tier", table_name="issue_frames")
    op.execute("DROP INDEX ix_issue_frames_citec_domains")
    op.drop_column("issue_frames", "severity_tier")
    op.drop_column("issue_frames", "citec_domains")
