"""extend social account status values

Revision ID: 008_extend_social_account_status
Revises: 007_add_voiceover_no_audio
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008_extend_social_account_status"
down_revision = "007_add_voiceover_no_audio"
branch_labels = None
depends_on = None


NEW_STATUS_CHECK = (
    "status IN ('active', 'inactive', 'revoked', 'expired', 'reauthorization_required')"
)
OLD_STATUS_CHECK = "status IN ('active', 'inactive', 'revoked', 'expired')"


def _status_check_name() -> str | None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "social_accounts" not in set(inspector.get_table_names()):
        return None
    names = {ck["name"] for ck in inspector.get_check_constraints("social_accounts")}
    for candidate in ("ck_social_accounts_status", "status"):
        if candidate in names:
            return candidate
    return None


def upgrade() -> None:
    existing_name = _status_check_name()
    if existing_name:
        with op.batch_alter_table("social_accounts", recreate="always") as batch_op:
            batch_op.drop_constraint(existing_name, type_="check")
            batch_op.create_check_constraint("ck_social_accounts_status", NEW_STATUS_CHECK)
    else:
        with op.batch_alter_table("social_accounts", recreate="always") as batch_op:
            batch_op.create_check_constraint("ck_social_accounts_status", NEW_STATUS_CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE social_accounts
            SET status = 'expired'
            WHERE status = 'reauthorization_required'
            """
        )
    )
    existing_name = _status_check_name()
    if existing_name:
        with op.batch_alter_table("social_accounts", recreate="always") as batch_op:
            batch_op.drop_constraint(existing_name, type_="check")
            batch_op.create_check_constraint("ck_social_accounts_status", OLD_STATUS_CHECK)
    else:
        with op.batch_alter_table("social_accounts", recreate="always") as batch_op:
            batch_op.create_check_constraint("ck_social_accounts_status", OLD_STATUS_CHECK)
