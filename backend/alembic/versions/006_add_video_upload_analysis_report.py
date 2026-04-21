"""add analysis_report to video_uploads

Revision ID: 006_add_video_upload_analysis_report
Revises: 005_fix_agent_memories_indexes
Create Date: 2026-04-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006_add_video_upload_analysis_report"
down_revision = "005_fix_agent_memories_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_uploads",
        sa.Column("analysis_report", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("video_uploads", "analysis_report")
