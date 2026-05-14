"""fix pipeline_runs status constraint to include waiting_remix_confirmation

Revision ID: 012_fix_remix_status_constraint
Revises: 011_add_auto_session_reference_video_selections
Create Date: 2026-05-13

SQLite does not support DROP CONSTRAINT, and SQLAlchemy's get_check_constraints()
does not reliably reflect inline check constraints on SQLite. The only safe approach
is to rebuild the table via raw SQL with the correct constraint definition.
"""

from __future__ import annotations

from alembic import op


revision = "012_fix_remix_status_constraint"
down_revision = "011_add_auto_session_reference_video_selections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Read the current CREATE TABLE statement to get column definitions
    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='pipeline_runs'"
    ).fetchone()
    if row is None:
        return

    # Rebuild the table with only the correct status constraint.
    # We use a temp name, copy data, drop original, rename.
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        conn.exec_driver_sql("""
            CREATE TABLE pipeline_runs_new (
                id VARCHAR NOT NULL,
                user_id VARCHAR REFERENCES users(id) ON DELETE CASCADE,
                project_id VARCHAR NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                session_id VARCHAR REFERENCES auto_chat_sessions(id) ON DELETE SET NULL,
                trace_id VARCHAR NOT NULL,
                engine VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                input_config TEXT NOT NULL,
                current_agent VARCHAR,
                overall_score FLOAT,
                artifacts_snapshot TEXT,
                final_video_path VARCHAR,
                error_message TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME,
                updated_at DATETIME,
                completed_at DATETIME,
                PRIMARY KEY (id),
                CHECK (status IN (
                    'pending', 'running', 'completed', 'failed', 'cancelled',
                    'waiting_confirmation', 'waiting_prompt_review', 'waiting_remix_confirmation'
                )),
                CHECK (retry_count >= 0)
            )
        """)
        conn.exec_driver_sql("""
            INSERT INTO pipeline_runs_new
            SELECT id, user_id, project_id, session_id, trace_id, engine,
                   status, input_config, current_agent, overall_score,
                   artifacts_snapshot, final_video_path, error_message,
                   retry_count, created_at, updated_at, completed_at
            FROM pipeline_runs
        """)
        conn.exec_driver_sql("DROP TABLE pipeline_runs")
        conn.exec_driver_sql("ALTER TABLE pipeline_runs_new RENAME TO pipeline_runs")

        # Recreate indexes
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_user_id ON pipeline_runs (user_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_project_id ON pipeline_runs (project_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_session_id ON pipeline_runs (session_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_trace_id ON pipeline_runs (trace_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_project_id_created_at ON pipeline_runs (project_id, created_at)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_user_id_status ON pipeline_runs (user_id, status)")
    finally:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        conn.exec_driver_sql("""
            CREATE TABLE pipeline_runs_new (
                id VARCHAR NOT NULL,
                user_id VARCHAR REFERENCES users(id) ON DELETE CASCADE,
                project_id VARCHAR NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                session_id VARCHAR REFERENCES auto_chat_sessions(id) ON DELETE SET NULL,
                trace_id VARCHAR NOT NULL,
                engine VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                input_config TEXT NOT NULL,
                current_agent VARCHAR,
                overall_score FLOAT,
                artifacts_snapshot TEXT,
                final_video_path VARCHAR,
                error_message TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME,
                updated_at DATETIME,
                completed_at DATETIME,
                PRIMARY KEY (id),
                CHECK (status IN (
                    'pending', 'running', 'completed', 'failed', 'cancelled',
                    'waiting_confirmation', 'waiting_prompt_review'
                )),
                CHECK (retry_count >= 0)
            )
        """)
        conn.exec_driver_sql("""
            INSERT INTO pipeline_runs_new
            SELECT id, user_id, project_id, session_id, trace_id, engine,
                   status, input_config, current_agent, overall_score,
                   artifacts_snapshot, final_video_path, error_message,
                   retry_count, created_at, updated_at, completed_at
            FROM pipeline_runs
        """)
        conn.exec_driver_sql("DROP TABLE pipeline_runs")
        conn.exec_driver_sql("ALTER TABLE pipeline_runs_new RENAME TO pipeline_runs")

        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_user_id ON pipeline_runs (user_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_project_id ON pipeline_runs (project_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_session_id ON pipeline_runs (session_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_trace_id ON pipeline_runs (trace_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_project_id_created_at ON pipeline_runs (project_id, created_at)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_user_id_status ON pipeline_runs (user_id, status)")
    finally:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
