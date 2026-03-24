"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("current_step", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # --- materials ---
    op.create_table(
        "materials",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("thumbnail_path", sa.String(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(), nullable=False, server_default="image"),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_materials_category", "materials", ["category"])

    # --- video_uploads ---
    op.create_table(
        "video_uploads",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # --- video_analyses ---
    op.create_table(
        "video_analyses",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("video_upload_id", sa.String(), sa.ForeignKey("video_uploads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("scene_tags", sa.Text(), nullable=True),
        sa.Column("recommended_categories", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # --- material_selections ---
    op.create_table(
        "material_selections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_id", sa.String(), sa.ForeignKey("materials.id"), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "material_id"),
    )

    # --- prompt_messages ---
    op.create_table(
        "prompt_messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # --- prompts ---
    op.create_table(
        "prompts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_selection_id", sa.String(), sa.ForeignKey("material_selections.id"), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # --- model_images ---
    op.create_table(
        "model_images",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # --- talking_head_tasks ---
    op.create_table(
        "talking_head_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shot_index", sa.Integer(), nullable=True),
        sa.Column("model_image_id", sa.String(), sa.ForeignKey("model_images.id"), nullable=False),
        sa.Column("bg_material_id", sa.String(), sa.ForeignKey("materials.id"), nullable=True),
        sa.Column("composite_status", sa.String(), server_default="pending"),
        sa.Column("composite_image_path", sa.String(), nullable=True),
        sa.Column("compositor_task_id", sa.String(), nullable=True),
        sa.Column("motion_prompt", sa.Text(), nullable=True),
        sa.Column("audio_segment_url", sa.String(), nullable=True),
        sa.Column("audio_start_ms", sa.Integer(), nullable=True),
        sa.Column("audio_end_ms", sa.Integer(), nullable=True),
        sa.Column("lipsync_status", sa.String(), server_default="pending"),
        sa.Column("lipsync_task_id", sa.String(), nullable=True),
        sa.Column("video_path", sa.String(), nullable=True),
        sa.Column("thumbnail_path", sa.String(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # --- generated_videos ---
    op.create_table(
        "generated_videos",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_id", sa.String(), sa.ForeignKey("prompts.id"), nullable=True),
        sa.Column("material_id", sa.String(), sa.ForeignKey("materials.id"), nullable=True),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("kling_task_id", sa.String(), nullable=True),
        sa.Column("video_path", sa.String(), nullable=True),
        sa.Column("thumbnail_path", sa.String(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("is_selected", sa.Boolean(), server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generation_type", sa.String(), server_default="image_to_video"),
        sa.Column("talking_head_task_id", sa.String(), sa.ForeignKey("talking_head_tasks.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # --- timeline_assets ---
    op.create_table(
        "timeline_assets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # --- timeline_clips ---
    op.create_table(
        "timeline_clips",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generated_video_id", sa.String(), sa.ForeignKey("generated_videos.id"), nullable=True),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("timeline_assets.id"), nullable=True),
        sa.Column("track_type", sa.String(), server_default="video"),
        sa.Column("track_index", sa.Integer(), server_default="0"),
        sa.Column("position_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # --- pipeline_runs ---
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("input_config", sa.Text(), nullable=False),
        sa.Column("current_agent", sa.String(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("final_video_path", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # --- agent_executions ---
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("input_data", sa.Text(), nullable=True),
        sa.Column("output_data", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), server_default="1"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # --- model_usages ---
    op.create_table(
        "model_usages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), server_default="0"),
        sa.Column("total_tokens", sa.Integer(), server_default="0"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_model_usages_pipeline_run_id", "model_usages", ["pipeline_run_id"])
    op.create_index("ix_model_usages_trace_id", "model_usages", ["trace_id"])
    op.create_index("ix_model_usages_agent_name", "model_usages", ["agent_name"])


def downgrade() -> None:
    op.drop_table("agent_executions")
    op.drop_table("pipeline_runs")
    op.drop_table("model_usages")
    op.drop_table("timeline_clips")
    op.drop_table("timeline_assets")
    op.drop_table("generated_videos")
    op.drop_table("talking_head_tasks")
    op.drop_table("model_images")
    op.drop_table("prompts")
    op.drop_table("prompt_messages")
    op.drop_table("material_selections")
    op.drop_table("video_analyses")
    op.drop_table("video_uploads")
    op.drop_index("ix_materials_category", "materials")
    op.drop_table("materials")
    op.drop_table("projects")
