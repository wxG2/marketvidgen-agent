from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentMemory(Base):
    """Persistent long-term memory scoped by namespace and scope."""

    __tablename__ = "agent_memories"
    __table_args__ = (
        UniqueConstraint(
            "namespace_key",
            "memory_key",
            name="uq_agent_memories_namespace_key_memory_key",
        ),
        CheckConstraint(
            "scope IN ('conversation', 'session', 'user', 'organization')",
            name="scope",
        ),
        CheckConstraint(
            "importance IS NULL OR importance >= 0",
            name="importance_non_negative",
        ),
        Index(
            "ix_agent_memories_namespace_key_scope_updated_at",
            "namespace_key",
            "scope",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String, default="user", nullable=False)
    namespace_key: Mapped[str] = mapped_column(String, nullable=False)
    memory_key: Mapped[str] = mapped_column(String, nullable=False)
    content_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_thread_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("agent_threads.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    source_run_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    importance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
