from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentMemory(Base):
    """Persistent cross-run key-value memory scoped to a user.

    Agents can read and write arbitrary JSON values here to remember
    preferences across pipeline runs (e.g. preferred voice params,
    platform-specific prompt styles, successful shot-duration patterns).
    """

    __tablename__ = "agent_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_agent_memory_user_key"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Dot-namespaced key, e.g. "voice.preferred_params" or "platform_style.douyin"
    key: Mapped[str] = mapped_column(String, nullable=False)
    # JSON-serialised value (any JSON type)
    value_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
