"""align agent db spec v2

Revision ID: 004_align_agent_db_spec_v2
Revises: 003_add_progress_text
Create Date: 2026-04-09
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa

from app.db.session import Base
import app.models  # noqa: F401


revision = "004_align_agent_db_spec_v2"
down_revision = "003_add_progress_text"
branch_labels = None
depends_on = None


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table_name: str) -> bool:
    return table_name in set(_inspector(bind).get_table_names())


def _has_column(bind, table_name: str, column_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return column_name in {col["name"] for col in _inspector(bind).get_columns(table_name)}


def _has_index(bind, table_name: str, index_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return index_name in {idx["name"] for idx in _inspector(bind).get_indexes(table_name)}


def _has_check(bind, table_name: str, check_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return check_name in {ck["name"] for ck in _inspector(bind).get_check_constraints(table_name)}


def _has_fk(bind, table_name: str, fk_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return fk_name in {fk.get("name") for fk in _inspector(bind).get_foreign_keys(table_name)}


def _create_index_if_missing(table_name: str, index_name: str, columns: Iterable[str], *, unique: bool = False) -> None:
    bind = op.get_bind()
    if not _has_table(bind, table_name) or _has_index(bind, table_name, index_name):
        return
    op.create_index(index_name, table_name, list(columns), unique=unique)


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _has_table(bind, table_name) or _has_column(bind, table_name, column.name):
        return
    op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    # Create any tables that are still missing from the historical Alembic chain.
    Base.metadata.create_all(bind)

    # Legacy columns that existed only via runtime schema patching.
    _add_column_if_missing("projects", sa.Column("user_id", sa.String(), nullable=True))
    _add_column_if_missing("materials", sa.Column("user_id", sa.String(), nullable=True))
    _add_column_if_missing("prompt_messages", sa.Column("user_id", sa.String(), nullable=True))
    _add_column_if_missing("prompts", sa.Column("user_id", sa.String(), nullable=True))
    _add_column_if_missing("video_uploads", sa.Column("session_id", sa.String(), nullable=True))
    _add_column_if_missing("pipeline_runs", sa.Column("user_id", sa.String(), nullable=True))
    _add_column_if_missing("pipeline_runs", sa.Column("session_id", sa.String(), nullable=True))
    _add_column_if_missing("pipeline_runs", sa.Column("artifacts_snapshot", sa.Text(), nullable=True))
    _add_column_if_missing("video_deliveries", sa.Column("social_account_id", sa.String(), nullable=True))
    _add_column_if_missing("video_deliveries", sa.Column("draft_payload_json", sa.Text(), nullable=True))
    _add_column_if_missing("video_deliveries", sa.Column("external_status", sa.String(), nullable=True))
    _add_column_if_missing("video_deliveries", sa.Column("platform_error_code", sa.String(), nullable=True))
    _add_column_if_missing("video_deliveries", sa.Column("submitted_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("video_deliveries", sa.Column("published_at", sa.DateTime(), nullable=True))

    # Align older agent_memories schema, but skip if create_all already produced the new shape.
    if _has_table(bind, "agent_memories") and _has_column(bind, "agent_memories", "key"):
        with op.batch_alter_table("agent_memories", recreate="always") as batch_op:
            batch_op.alter_column("key", new_column_name="memory_key", existing_type=sa.String())
            batch_op.alter_column("value_json", new_column_name="content_json", existing_type=sa.Text())
            batch_op.add_column(sa.Column("scope", sa.String(), nullable=False, server_default="user"))
            batch_op.add_column(sa.Column("namespace_key", sa.String(), nullable=False, server_default="legacy"))
            batch_op.add_column(sa.Column("summary", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("source_type", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("source_thread_id", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("source_run_id", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("importance", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("metadata_json", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
            batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
            if _has_fk(bind, "agent_memories", "fk_agent_memories_source_thread_id_agent_threads") is False:
                batch_op.create_foreign_key(
                    "fk_agent_memories_source_thread_id_agent_threads",
                    "agent_threads",
                    ["source_thread_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if _has_fk(bind, "agent_memories", "fk_agent_memories_source_run_id_agent_runs") is False:
                batch_op.create_foreign_key(
                    "fk_agent_memories_source_run_id_agent_runs",
                    "agent_runs",
                    ["source_run_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            batch_op.drop_constraint("uq_agent_memory_user_key", type_="unique")
            batch_op.create_unique_constraint(
                "uq_agent_memories_namespace_key_memory_key",
                ["namespace_key", "memory_key"],
            )
            batch_op.create_check_constraint(
                "ck_agent_memories_scope",
                "scope IN ('conversation', 'session', 'user', 'organization')",
            )
            batch_op.create_check_constraint(
                "ck_agent_memories_importance_non_negative",
                "importance IS NULL OR importance >= 0",
            )

        bind.exec_driver_sql(
            """
            UPDATE agent_memories
            SET namespace_key = 'user:' || user_id || ':vidgen'
            WHERE namespace_key = 'legacy' OR namespace_key IS NULL OR namespace_key = ''
            """
        )

    # Current-model indexes that old revisions did not create.
    _create_index_if_missing("projects", "ix_projects_user_id", ["user_id"])
    _create_index_if_missing("materials", "ix_materials_user_id", ["user_id"])
    _create_index_if_missing("prompt_messages", "ix_prompt_messages_user_id", ["user_id"])
    _create_index_if_missing("prompt_messages", "ix_prompt_messages_project_id", ["project_id"])
    _create_index_if_missing("prompts", "ix_prompts_user_id", ["user_id"])
    _create_index_if_missing("prompts", "ix_prompts_project_id", ["project_id"])
    _create_index_if_missing("video_uploads", "ix_video_uploads_project_id", ["project_id"])
    _create_index_if_missing("video_uploads", "ix_video_uploads_session_id", ["session_id"])
    _create_index_if_missing("pipeline_runs", "ix_pipeline_runs_user_id", ["user_id"])
    _create_index_if_missing("pipeline_runs", "ix_pipeline_runs_session_id", ["session_id"])
    _create_index_if_missing("pipeline_runs", "ix_pipeline_runs_trace_id", ["trace_id"])
    _create_index_if_missing("pipeline_runs", "ix_pipeline_runs_project_id_created_at", ["project_id", "created_at"])
    _create_index_if_missing("pipeline_runs", "ix_pipeline_runs_user_id_status", ["user_id", "status"])
    _create_index_if_missing("agent_executions", "ix_agent_executions_pipeline_run_id", ["pipeline_run_id"])
    _create_index_if_missing("agent_executions", "ix_agent_executions_pipeline_run_id_created_at", ["pipeline_run_id", "created_at"])
    _create_index_if_missing("model_usages", "ix_model_usages_pipeline_run_id", ["pipeline_run_id"])
    _create_index_if_missing("video_deliveries", "ix_video_deliveries_social_account_id", ["social_account_id"])

    # Foreign keys / checks on old tables that previously came from runtime patching only.
    if _has_table(bind, "projects") and _has_column(bind, "projects", "user_id") and not _has_fk(bind, "projects", "fk_projects_user_id_users"):
        with op.batch_alter_table("projects", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_projects_user_id_users",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )

    if _has_table(bind, "materials") and _has_column(bind, "materials", "user_id") and not _has_fk(bind, "materials", "fk_materials_user_id_users"):
        with op.batch_alter_table("materials", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_materials_user_id_users",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )

    if _has_table(bind, "prompt_messages") and _has_column(bind, "prompt_messages", "user_id") and not _has_fk(bind, "prompt_messages", "fk_prompt_messages_user_id_users"):
        with op.batch_alter_table("prompt_messages", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_prompt_messages_user_id_users",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )

    if _has_table(bind, "prompts") and _has_column(bind, "prompts", "user_id") and not _has_fk(bind, "prompts", "fk_prompts_user_id_users"):
        with op.batch_alter_table("prompts", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_prompts_user_id_users",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )

    if _has_table(bind, "video_uploads") and _has_column(bind, "video_uploads", "session_id") and not _has_fk(bind, "video_uploads", "fk_video_uploads_session_id_auto_chat_sessions"):
        with op.batch_alter_table("video_uploads", recreate="always") as batch_op:
            batch_op.create_foreign_key(
                "fk_video_uploads_session_id_auto_chat_sessions",
                "auto_chat_sessions",
                ["session_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if _has_table(bind, "pipeline_runs"):
        needs_pipeline_batch = (
            not _has_check(bind, "pipeline_runs", "ck_pipeline_runs_status")
            or not _has_check(bind, "pipeline_runs", "ck_pipeline_runs_retry_count_non_negative")
            or (_has_column(bind, "pipeline_runs", "user_id") and not _has_fk(bind, "pipeline_runs", "fk_pipeline_runs_user_id_users"))
            or (_has_column(bind, "pipeline_runs", "session_id") and not _has_fk(bind, "pipeline_runs", "fk_pipeline_runs_session_id_auto_chat_sessions"))
        )
        if needs_pipeline_batch:
            with op.batch_alter_table("pipeline_runs", recreate="always") as batch_op:
                if not _has_check(bind, "pipeline_runs", "ck_pipeline_runs_status"):
                    batch_op.create_check_constraint(
                        "ck_pipeline_runs_status",
                        "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'waiting_confirmation', 'waiting_prompt_review')",
                    )
                if not _has_check(bind, "pipeline_runs", "ck_pipeline_runs_retry_count_non_negative"):
                    batch_op.create_check_constraint(
                        "ck_pipeline_runs_retry_count_non_negative",
                        "retry_count >= 0",
                    )
                if _has_column(bind, "pipeline_runs", "user_id") and not _has_fk(bind, "pipeline_runs", "fk_pipeline_runs_user_id_users"):
                    batch_op.create_foreign_key(
                        "fk_pipeline_runs_user_id_users",
                        "users",
                        ["user_id"],
                        ["id"],
                        ondelete="CASCADE",
                    )
                if _has_column(bind, "pipeline_runs", "session_id") and not _has_fk(bind, "pipeline_runs", "fk_pipeline_runs_session_id_auto_chat_sessions"):
                    batch_op.create_foreign_key(
                        "fk_pipeline_runs_session_id_auto_chat_sessions",
                        "auto_chat_sessions",
                        ["session_id"],
                        ["id"],
                        ondelete="SET NULL",
                    )

    if _has_table(bind, "agent_executions"):
        needs_execution_batch = (
            not _has_check(bind, "agent_executions", "ck_agent_executions_status")
            or not _has_check(bind, "agent_executions", "ck_agent_executions_attempt_number_positive")
            or not _has_check(bind, "agent_executions", "ck_agent_executions_duration_ms_non_negative")
        )
        if needs_execution_batch:
            with op.batch_alter_table("agent_executions", recreate="always") as batch_op:
                if not _has_check(bind, "agent_executions", "ck_agent_executions_status"):
                    batch_op.create_check_constraint(
                        "ck_agent_executions_status",
                        "status IN ('pending', 'running', 'completed', 'failed', 'skipped', 'cancelled')",
                    )
                if not _has_check(bind, "agent_executions", "ck_agent_executions_attempt_number_positive"):
                    batch_op.create_check_constraint(
                        "ck_agent_executions_attempt_number_positive",
                        "attempt_number >= 1",
                    )
                if not _has_check(bind, "agent_executions", "ck_agent_executions_duration_ms_non_negative"):
                    batch_op.create_check_constraint(
                        "ck_agent_executions_duration_ms_non_negative",
                        "duration_ms IS NULL OR duration_ms >= 0",
                    )

    if _has_table(bind, "model_usages"):
        needs_model_usage_batch = (
            not _has_check(bind, "model_usages", "ck_model_usages_prompt_tokens_non_negative")
            or not _has_check(bind, "model_usages", "ck_model_usages_completion_tokens_non_negative")
            or not _has_check(bind, "model_usages", "ck_model_usages_total_tokens_non_negative")
            or not _has_fk(bind, "model_usages", "fk_model_usages_pipeline_run_id_pipeline_runs")
        )
        if needs_model_usage_batch:
            with op.batch_alter_table("model_usages", recreate="always") as batch_op:
                if not _has_check(bind, "model_usages", "ck_model_usages_prompt_tokens_non_negative"):
                    batch_op.create_check_constraint(
                        "ck_model_usages_prompt_tokens_non_negative",
                        "prompt_tokens >= 0",
                    )
                if not _has_check(bind, "model_usages", "ck_model_usages_completion_tokens_non_negative"):
                    batch_op.create_check_constraint(
                        "ck_model_usages_completion_tokens_non_negative",
                        "completion_tokens >= 0",
                    )
                if not _has_check(bind, "model_usages", "ck_model_usages_total_tokens_non_negative"):
                    batch_op.create_check_constraint(
                        "ck_model_usages_total_tokens_non_negative",
                        "total_tokens >= 0",
                    )
                if not _has_fk(bind, "model_usages", "fk_model_usages_pipeline_run_id_pipeline_runs"):
                    batch_op.create_foreign_key(
                        "fk_model_usages_pipeline_run_id_pipeline_runs",
                        "pipeline_runs",
                        ["pipeline_run_id"],
                        ["id"],
                        ondelete="CASCADE",
                    )

    if _has_table(bind, "material_selections") and not _has_check(bind, "material_selections", "ck_material_selections_sort_order_non_negative"):
        with op.batch_alter_table("material_selections", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "ck_material_selections_sort_order_non_negative",
                "sort_order >= 0",
            )

    if _has_table(bind, "auto_session_material_selections") and not _has_check(bind, "auto_session_material_selections", "ck_auto_session_material_selections_sort_order_non_negative"):
        with op.batch_alter_table("auto_session_material_selections", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "ck_auto_session_material_selections_sort_order_non_negative",
                "sort_order >= 0",
            )

    if _has_table(bind, "social_accounts"):
        needs_social_batch = (
            not _has_check(bind, "social_accounts", "ck_social_accounts_platform")
            or not _has_check(bind, "social_accounts", "ck_social_accounts_status")
        )
        if needs_social_batch:
            with op.batch_alter_table("social_accounts", recreate="always") as batch_op:
                if not _has_check(bind, "social_accounts", "ck_social_accounts_platform"):
                    batch_op.create_check_constraint(
                        "ck_social_accounts_platform",
                        "platform IN ('douyin', 'generic')",
                    )
                if not _has_check(bind, "social_accounts", "ck_social_accounts_status"):
                    batch_op.create_check_constraint(
                        "ck_social_accounts_status",
                        "status IN ('active', 'inactive', 'revoked', 'expired')",
                    )

    if _has_table(bind, "repository_assets") and not _has_check(bind, "repository_assets", "ck_repository_assets_duration_ms_non_negative"):
        with op.batch_alter_table("repository_assets", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "ck_repository_assets_duration_ms_non_negative",
                "duration_ms IS NULL OR duration_ms >= 0",
            )

    if _has_table(bind, "video_deliveries"):
        needs_delivery_batch = (
            not _has_check(bind, "video_deliveries", "ck_video_deliveries_action_type")
            or not _has_check(bind, "video_deliveries", "ck_video_deliveries_platform")
            or not _has_check(bind, "video_deliveries", "ck_video_deliveries_status")
            or (_has_column(bind, "video_deliveries", "social_account_id") and not _has_fk(bind, "video_deliveries", "fk_video_deliveries_social_account_id_social_accounts"))
        )
        if needs_delivery_batch:
            with op.batch_alter_table("video_deliveries", recreate="always") as batch_op:
                if not _has_check(bind, "video_deliveries", "ck_video_deliveries_action_type"):
                    batch_op.create_check_constraint(
                        "ck_video_deliveries_action_type",
                        "action_type IN ('save', 'publish')",
                    )
                if not _has_check(bind, "video_deliveries", "ck_video_deliveries_platform"):
                    batch_op.create_check_constraint(
                        "ck_video_deliveries_platform",
                        "platform IN ('repository', 'douyin')",
                    )
                if not _has_check(bind, "video_deliveries", "ck_video_deliveries_status"):
                    batch_op.create_check_constraint(
                        "ck_video_deliveries_status",
                        "status IN ('pending', 'saved', 'published', 'failed')",
                    )
                if _has_column(bind, "video_deliveries", "social_account_id") and not _has_fk(bind, "video_deliveries", "fk_video_deliveries_social_account_id_social_accounts"):
                    batch_op.create_foreign_key(
                        "fk_video_deliveries_social_account_id_social_accounts",
                        "social_accounts",
                        ["social_account_id"],
                        ["id"],
                    )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in [
        "retrieval_documents",
        "run_events",
        "model_calls",
        "prompt_versions",
        "tool_calls",
        "agent_checkpoints",
        "agent_steps",
        "agent_messages",
        "agent_runs",
        "agent_threads",
    ]:
        if _has_table(bind, table_name):
            op.drop_table(table_name)
