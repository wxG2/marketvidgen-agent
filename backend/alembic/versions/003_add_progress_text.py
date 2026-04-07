"""add progress_text to agent_executions

Revision ID: 003_add_progress_text
Revises: 002_add_swarm_state_columns
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


revision = "003_add_progress_text"
down_revision = "002_add_swarm_state_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_executions", sa.Column("progress_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_executions", "progress_text")
