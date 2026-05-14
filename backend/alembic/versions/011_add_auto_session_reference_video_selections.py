"""add auto session reference video selections

Revision ID: 011_add_auto_session_reference_video_selections
Revises: 010_add_remix_pipeline_status
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "011_add_auto_session_reference_video_selections"
down_revision = "010_add_remix_pipeline_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auto_session_reference_video_selections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("video_upload_id", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name=op.f("ck_auto_session_reference_video_selections_sort_order_non_negative")),
        sa.ForeignKeyConstraint(["session_id"], ["auto_chat_sessions.id"], name=op.f("fk_auto_session_reference_video_selections_session_id_auto_chat_sessions"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_upload_id"], ["video_uploads.id"], name=op.f("fk_auto_session_reference_video_selections_video_upload_id_video_uploads"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auto_session_reference_video_selections")),
        sa.UniqueConstraint("session_id", "video_upload_id", name="uq_auto_session_reference_videos_session_video"),
    )
    op.create_index(op.f("ix_auto_session_reference_video_selections_session_id"), "auto_session_reference_video_selections", ["session_id"])
    op.create_index(op.f("ix_auto_session_reference_video_selections_video_upload_id"), "auto_session_reference_video_selections", ["video_upload_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auto_session_reference_video_selections_video_upload_id"), table_name="auto_session_reference_video_selections")
    op.drop_index(op.f("ix_auto_session_reference_video_selections_session_id"), table_name="auto_session_reference_video_selections")
    op.drop_table("auto_session_reference_video_selections")
