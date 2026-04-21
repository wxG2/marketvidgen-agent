"""fix agent memories indexes

Revision ID: 005_fix_agent_memories_indexes
Revises: 004_align_agent_db_spec_v2
Create Date: 2026-04-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "005_fix_agent_memories_indexes"
down_revision = "004_align_agent_db_spec_v2"
branch_labels = None
depends_on = None


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table_name: str) -> bool:
    return table_name in set(_inspector(bind).get_table_names())


def _has_index(bind, table_name: str, index_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return index_name in {idx["name"] for idx in _inspector(bind).get_indexes(table_name)}


def _has_unique_constraint(bind, table_name: str, constraint_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return constraint_name in {
        constraint.get("name") for constraint in _inspector(bind).get_unique_constraints(table_name)
    }


def _has_duplicate_namespace_memory_keys(bind) -> bool:
    row = bind.exec_driver_sql(
        """
        SELECT namespace_key, memory_key, COUNT(*) AS duplicate_count
        FROM agent_memories
        GROUP BY namespace_key, memory_key
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "agent_memories"):
        return

    if (
        not _has_unique_constraint(bind, "agent_memories", "uq_agent_memories_namespace_key_memory_key")
        and not _has_index(bind, "agent_memories", "uq_agent_memories_namespace_key_memory_key")
    ):
        if _has_duplicate_namespace_memory_keys(bind):
            raise RuntimeError(
                "agent_memories contains duplicate (namespace_key, memory_key) rows; "
                "cannot add uq_agent_memories_namespace_key_memory_key"
            )
        op.create_index(
            "uq_agent_memories_namespace_key_memory_key",
            "agent_memories",
            ["namespace_key", "memory_key"],
            unique=True,
        )

    if not _has_index(bind, "agent_memories", "ix_agent_memories_source_thread_id"):
        op.create_index(
            "ix_agent_memories_source_thread_id",
            "agent_memories",
            ["source_thread_id"],
            unique=False,
        )

    if not _has_index(bind, "agent_memories", "ix_agent_memories_source_run_id"):
        op.create_index(
            "ix_agent_memories_source_run_id",
            "agent_memories",
            ["source_run_id"],
            unique=False,
        )

    if not _has_index(bind, "agent_memories", "ix_agent_memories_namespace_key_scope_updated_at"):
        op.create_index(
            "ix_agent_memories_namespace_key_scope_updated_at",
            "agent_memories",
            ["namespace_key", "scope", "updated_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "agent_memories"):
        return

    for index_name in [
        "ix_agent_memories_namespace_key_scope_updated_at",
        "ix_agent_memories_source_run_id",
        "ix_agent_memories_source_thread_id",
        "uq_agent_memories_namespace_key_memory_key",
    ]:
        if _has_index(bind, "agent_memories", index_name):
            op.drop_index(index_name, table_name="agent_memories")
