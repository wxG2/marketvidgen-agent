"""add swarm state columns

Revision ID: 002_add_swarm_state_columns
Revises: 001_initial_schema
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa


revision = "002_add_swarm_state_columns"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("engine", sa.String(), nullable=True, server_default="pipeline"))
    op.add_column("pipeline_runs", sa.Column("swarm_state_json", sa.Text(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("latest_lead_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_runs", "latest_lead_message")
    op.drop_column("pipeline_runs", "swarm_state_json")
    op.drop_column("pipeline_runs", "engine")
