"""add voiceover_no_audio to auto_chat_sessions

Revision ID: 007_add_voiceover_no_audio
Revises: 006_add_video_upload_analysis_report
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007_add_voiceover_no_audio"
down_revision = "006_add_video_upload_analysis_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auto_chat_sessions",
        sa.Column("voiceover_no_audio", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("auto_chat_sessions", "voiceover_no_audio")
