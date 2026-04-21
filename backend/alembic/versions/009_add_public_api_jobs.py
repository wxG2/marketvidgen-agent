"""add public api keys and external video jobs

Revision ID: 009_add_public_api_jobs
Revises: 008_extend_social_account_status
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "009_add_public_api_jobs"
down_revision = "008_extend_social_account_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("key_prefix", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_user_id_status", "api_keys", ["user_id", "status"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    op.create_table(
        "external_video_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("api_key_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(), nullable=False),
        sa.Column("client_reference_id", sa.String(), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_id", "idempotency_key_hash", name="uq_external_video_jobs_api_key_id_idempotency_key_hash"),
    )
    op.create_index("ix_external_video_jobs_user_id", "external_video_jobs", ["user_id"])
    op.create_index("ix_external_video_jobs_api_key_id", "external_video_jobs", ["api_key_id"])
    op.create_index("ix_external_video_jobs_project_id", "external_video_jobs", ["project_id"])
    op.create_index("ix_external_video_jobs_pipeline_run_id", "external_video_jobs", ["pipeline_run_id"])
    op.create_index("ix_external_video_jobs_user_id_created_at", "external_video_jobs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_external_video_jobs_user_id_created_at", table_name="external_video_jobs")
    op.drop_index("ix_external_video_jobs_pipeline_run_id", table_name="external_video_jobs")
    op.drop_index("ix_external_video_jobs_project_id", table_name="external_video_jobs")
    op.drop_index("ix_external_video_jobs_api_key_id", table_name="external_video_jobs")
    op.drop_index("ix_external_video_jobs_user_id", table_name="external_video_jobs")
    op.drop_table("external_video_jobs")

    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id_status", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
