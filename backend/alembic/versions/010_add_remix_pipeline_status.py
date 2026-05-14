"""add remix pipeline waiting status

Revision ID: 010_add_remix_pipeline_status
Revises: 009_add_public_api_jobs
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "010_add_remix_pipeline_status"
down_revision = "009_add_public_api_jobs"
branch_labels = None
depends_on = None


NEW_STATUS_CHECK = (
    "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', "
    "'waiting_confirmation', 'waiting_prompt_review', 'waiting_remix_confirmation')"
)
OLD_STATUS_CHECK = (
    "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', "
    "'waiting_confirmation', 'waiting_prompt_review')"
)


def _status_constraint_names() -> list[str]:
    inspector = sa.inspect(op.get_bind())
    return [
        item.get("name")
        for item in inspector.get_check_constraints("pipeline_runs")
        if item.get("name") in {"status", "ck_pipeline_runs_status"}
    ]


def upgrade() -> None:
    names = _status_constraint_names()
    with op.batch_alter_table("pipeline_runs", recreate="always") as batch_op:
        for name in names:
            batch_op.drop_constraint(name, type_="check")
        batch_op.create_check_constraint("status", NEW_STATUS_CHECK)


def downgrade() -> None:
    names = _status_constraint_names()
    with op.batch_alter_table("pipeline_runs", recreate="always") as batch_op:
        for name in names:
            batch_op.drop_constraint(name, type_="check")
        batch_op.create_check_constraint("status", OLD_STATUS_CHECK)
